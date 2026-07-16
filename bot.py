#!/usr/bin/env python3
"""
Feishu/Lark bot — ingest + orchestrator for the passive, event-driven bridge.

Per incoming message:
  - internal commands ("command ...") are handled here without involving Claude.
  - otherwise: log it; if Claude is busy, auto-reply "still on the last message" + done emoji;
    if ready, stamp the working emoji (group only), set a single-flight busy lock, and hand the
    message to deliver_to_claude.py, which pastes it into Claude's pane, scrapes the wrapped reply,
    and sends it back to Feishu.

Single-flight invariant: a module-level threading.Lock guards the busy-lock read/modify/write and the
ingress decision; the busy lock also tracks the deliver child's PID so a dead child is reaped on the
next ingress (no multi-minute DoS).
"""

import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone

import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

import feishu_api as api

CORRID_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no I/L/O/0/1 — unambiguous
MENTION_RE = re.compile(r"(?:^@_user_\d+\s+)+")
SAFE_MODE_CYCLE = ["manual", "auto", "plan"]  # default(manual) → auto → plan → manual

_busy_lock = threading.Lock()
_seen = OrderedDict()  # message_id LRU for ingress dedup
_SEEN_MAX = 512
BOOT_TIME = time.time()  # any message created before this is discarded (sent while the bot was down)


# ---------- small helpers ----------
def new_corrid() -> str:
    return "".join(secrets.choice(CORRID_ALPHABET) for _ in range(8))


def _pid_alive(pid) -> bool:
    if not pid:
        return True  # unknown -> assume alive
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _seen_mark(message_id: str) -> bool:
    """Return True if this message_id was already seen (redelivery)."""
    if message_id in _seen:
        return True
    _seen[message_id] = True
    if len(_seen) > _SEEN_MAX:
        _seen.popitem(last=False)
    return False


def is_stale_message(msg) -> bool:
    """True if the message was created before this bot process started (i.e. sent while the bot was
    down). Feishu's message.create_time may arrive as seconds or milliseconds — normalize both."""
    ct = getattr(msg, "create_time", None)
    if not ct:
        return False  # unknown — allow
    try:
        ct = int(ct)
    except (TypeError, ValueError):
        return False
    if ct > 1_000_000_000_000:  # milliseconds
        ct //= 1000
    return ct < int(BOOT_TIME)


def strip_mentions(text: str) -> str:
    return MENTION_RE.sub("", text or "").strip()


def append_inbox(record: dict) -> None:
    api.CONVERSATION_DIR.mkdir(parents=True, exist_ok=True)
    chat_id = record.get("chat_id") or "default"
    safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in chat_id)
    path = api.CONVERSATION_DIR / f"{safe}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"[inbox] wrote -> {path}")


def extract_text(message) -> str:
    try:
        content = json.loads(message.content or "{}")
    except Exception:
        content = {}
    if message.message_type == "text":
        return content.get("text", "")
    return f"[{message.message_type}] {message.content}"


# ---------- busy lock ----------
def is_busy() -> bool:
    """Under the lock: reap a dead deliver child if needed, then report busy state. Clears stale/dead locks."""
    busy = api.read_json(api.BUSY_PATH)
    if not isinstance(busy, dict) or not busy.get("corrid"):
        return False
    since = busy.get("since_ts") or 0
    fresh = (time.time() - since) < api.STALE_SEC
    alive = _pid_alive(busy.get("deliver_pid"))
    if fresh and alive:
        return True
    # stale or deliver child dead -> clear and treat as not busy
    api.atomic_write_json(api.BUSY_PATH, {})
    print(f"[busy] cleared stale/dead lock (corrid={busy.get('corrid')})", file=sys.stderr)
    return False


def claim_busy(corrid, message_id, chat_id, chat_type, text) -> None:
    api.atomic_write_json(
        api.BUSY_PATH,
        {
            "corrid": corrid,
            "message_id": message_id,
            "chat_id": chat_id,
            "chat_type": chat_type,
            "text": text,
            "since_ts": time.time(),
            "deliver_pid": None,
        },
    )


def set_deliver_pid(corrid, pid) -> None:
    busy = api.read_json(api.BUSY_PATH)
    if isinstance(busy, dict) and busy.get("corrid") == corrid:
        busy["deliver_pid"] = pid
        api.atomic_write_json(api.BUSY_PATH, busy)


# ---------- in-process delivery (paste + poll + finalize) ----------
# Runs in a daemon thread (see deliver_in_thread) so the bot's WebSocket event loop stays free.
# Pasting happens here, ~instantly after the busy check + claim — no child-process import delay.
# Timeout settings (tunable). On each deadline, if Claude is still processing, the bot backs off
# by another HARD_TIMEOUT_SEC window — indefinitely — until Claude finishes the task or the user
# sends `command kill`. A real "timed out / not picked up" message is sent ONLY when Claude is idle.
EARLY_ABORT_SEC = 40    # first deadline: how long to wait for Claude to start on the message
HARD_TIMEOUT_SEC = 280  # length of each subsequent backoff window while Claude keeps working
POLL_SLEEP = 5


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clear_busy_if_match(corrid: str) -> None:
    busy = api.read_json(api.BUSY_PATH)
    if isinstance(busy, dict) and busy.get("corrid") == corrid:
        api.atomic_write_json(api.BUSY_PATH, {})


def _capture_claude_pane() -> str:
    r = subprocess.run(
        ["tmux", "capture-pane", "-p", "-J", "-t", api.CLAUDE_PANE, "-S", "-2000"],
        capture_output=True, text=True, timeout=5,
    )
    return r.stdout


def _paste_prompt(text: str) -> None:
    subprocess.run(["tmux", "send-keys", "-l", "-t", api.CLAUDE_PANE, text], check=True, timeout=3)
    subprocess.run(["tmux", "send-keys", "-t", api.CLAUDE_PANE, "Enter"], check=True, timeout=3)


def _build_prompt(text: str, chat_type: str, corrid: str) -> str:
    text = (text or "").replace("\n", " ").replace("\r", " ")
    return (f"[FEISHU IN — reply via lark-feishu-bot skill] corrid={corrid} "
            f"chat_type={chat_type or 'p2p'} text={text}")


def _scrape_reply(pane: str, corrid: str):
    """Return (reply_text|None, ready_seen: bool). Ported from deliver_to_claude.py."""
    start_tok = f"REPLY_FeiShu_Msg:{corrid}"
    end_tok = f"READY4NextMsg:{corrid}"
    lines = pane.splitlines()
    end_idx = None
    for i in range(len(lines) - 1, -1, -1):
        if end_tok in lines[i]:
            end_idx = i
            break
    if end_idx is None:
        return None, False
    start_idx = None
    for i in range(end_idx - 1, -1, -1):
        if start_tok in lines[i]:
            start_idx = i
            break
    if start_idx is None:
        # End marker is present but the start marker isn't (yet) in this pane snapshot — NOT ready.
        # Previously this returned (None, True), which made the caller finalize with an empty body
        # and send "(empty reply)" whenever a poll captured the message tail without its head.
        # Keep polling until both markers bracket the body.
        return None, False
    body = [api.clean_line(l) for l in lines[start_idx + 1:end_idx]]
    while body and not body[0]:
        body.pop(0)
    while body and not body[-1]:
        body.pop()
    return ("\n".join(body) if body else ""), True


def _finalize(message_id, chat_id, chat_type, corrid, reply: str) -> None:
    if chat_id and reply != "":
        api.send_text(chat_id, reply)
    elif chat_id and reply == "":
        api.send_text(chat_id, "(empty reply)")
    if message_id:
        api.add_reaction(message_id, api.EMOJI_DONE)
        old = api.read_json(api.REACTIONS_PATH).get(message_id, {}).get("reaction_id")
        if old:
            api.delete_reaction(message_id, old)
    clear_busy_if_match(corrid)


def _reject_busy(message_id, chat_id, chat_type) -> None:
    if chat_id:
        api.send_text(chat_id, "Claude is working on the last message — send `command kill` to interrupt.")
    if chat_type == "group" and message_id:
        api.add_reaction(message_id, api.EMOJI_DONE)
        old = api.read_json(api.REACTIONS_PATH).get(message_id, {}).get("reaction_id")
        if old:
            api.delete_reaction(message_id, old)


def _deliver_and_watch(corrid, message_id, chat_id, chat_type, text) -> None:
    """In-process replacement for the deliver_to_claude.py child. Paste ASAP, then poll Claude's
    pane for the wrapped reply and finalize. The busy lock is always cleared on exit (finally)."""
    try:
        # dedup: already delivered (e.g. Feishu redelivery)
        delivered = api.read_json(api.DELIVERED_PATH)
        if message_id and message_id in delivered:
            print(f"[deliver] {message_id} already delivered; clearing lock")
            return

        # belt-and-suspenders: re-check idle immediately before paste (closes any tiny race
        # between the event-thread check and this paste). If busy, never paste.
        if api.claude_is_busy():
            _reject_busy(message_id, chat_id, chat_type)
            print(f"[busy] rejected {message_id} (Claude busy at paste time)")
            return

        delivered[message_id] = {"corrid": corrid, "chat_id": chat_id, "ts": _now_iso()}
        api.atomic_write_json(api.DELIVERED_PATH, delivered)

        try:
            _paste_prompt(_build_prompt(text, chat_type, corrid))
        except Exception as e:
            print(f"[deliver] paste failed: {e}", file=sys.stderr)
            return
        print(f"[deliver] pasted {corrid} into pane {api.CLAUDE_PANE}")

        start = time.time()
        next_check = start + EARLY_ABORT_SEC   # first deadline: did Claude pick up the message?
        saw_start = False
        while True:
            # If our busy lock was cleared (e.g. `command kill`), stop promptly without sending
            # a confusing timeout message.
            busy = api.read_json(api.BUSY_PATH)
            if not (isinstance(busy, dict) and busy.get("corrid") == corrid):
                print(f"[deliver] {corrid}: busy lock cleared — stopping (likely command kill)")
                return
            try:
                pane = _capture_claude_pane()
            except Exception as e:
                print(f"[deliver] capture error: {e}", file=sys.stderr)
                pane = ""
            reply, ready = _scrape_reply(pane, corrid)
            if not saw_start and f"REPLY_FeiShu_Msg:{corrid}" in pane:
                saw_start = True
            if ready:
                print(f"[deliver] scraped {len(reply or '')} chars for {corrid}")
                _finalize(message_id, chat_id, chat_type, corrid, reply or "")
                print(f"[deliver] reply sent for {corrid}")
                return
            if time.time() > next_check:
                # Deadline reached with no reply yet. If Claude is still actively working
                # (thinking / running tools / writing a long reply), keep waiting and back off
                # by another HARD_TIMEOUT_SEC window — indefinitely — until Claude finishes or
                # the user sends `command kill`. Only declare a real timeout when Claude is IDLE.
                if claude_is_processing():
                    next_check = time.time() + HARD_TIMEOUT_SEC
                    print(f"[deliver] {corrid}: Claude still working — backing off "
                          f"{HARD_TIMEOUT_SEC}s (elapsed {int(time.time() - start)}s, "
                          f"saw_start={saw_start})")
                    continue
                # Claude is idle and still no reply -> genuine problem.
                if not saw_start:
                    if chat_id:
                        api.send_text(chat_id, "(Claude did not pick up the message — please check)")
                    print(f"[deliver] early-abort {corrid} (no start marker, Claude idle after "
                          f"{int(time.time() - start)}s)", file=sys.stderr)
                else:
                    if chat_id:
                        api.send_text(chat_id, "(timed out waiting for Claude)")
                    print(f"[deliver] hard timeout {corrid} (start seen but no reply, Claude idle)",
                          file=sys.stderr)
                return
            time.sleep(POLL_SLEEP)
    finally:
        clear_busy_if_match(corrid)


def deliver_in_thread(corrid, message_id, chat_id, chat_type, text):
    """Set the busy lock, stamp working emoji (group + p2p), fire-and-forget the deliver child."""
    with _busy_lock:
        if is_busy():
            # Claude is still on a previous message
            if chat_id:
                api.send_text(chat_id, "still on the last message")
            if message_id:
                api.add_reaction(message_id, api.EMOJI_DONE)
            print(f"[busy] rejected {message_id} (Claude busy)")
            return
        if claude_is_processing():
            # Claude is mid-turn (e.g. on a directly-typed prompt) — never paste on top of it.
            # The user can send `command kill` to interrupt and free Claude up.
            if chat_id:
                api.send_text(chat_id, "Claude is working on the last message — send `command kill` to interrupt.")
            if message_id:
                api.add_reaction(message_id, api.EMOJI_DONE)
            print(f"[busy] rejected {message_id} (Claude processing)")
            return
        corrid = corrid or new_corrid()
        claim_busy(corrid, message_id, chat_id, chat_type, text)

    # stamp working emoji for both group @-mentions and p2p messages
    if message_id:
        rid = api.add_reaction(message_id, api.EMOJI_WORKING)
        if rid:
            reactions = api.read_json(api.REACTIONS_PATH)
            reactions[message_id] = {"reaction_id": rid, "emoji": api.EMOJI_WORKING}
            api.atomic_write_json(api.REACTIONS_PATH, reactions)

    # Hand off to an in-process daemon thread (NOT a child process): the bot already has
    # lark_oapi/feishu_api loaded, so the thread pastes within milliseconds of receive (no ~1s
    # child import delay) and runs the long reply-marker poll without blocking the WS event loop.
    t = threading.Thread(
        target=_deliver_and_watch,
        args=(corrid, message_id, chat_id, chat_type, text),
        daemon=True,
        name=f"deliver-{corrid}",
    )
    t.start()
    print(f"[deliver] thread started corrid={corrid}")


# ---------- internal commands ----------
def cmd_help(chat_id):
    api.send_text(chat_id, api.HELP_TEXT)


def cmd_kill(chat_id):
    # interrupt Claude
    for _ in range(2):
        try:
            subprocess.run(["tmux", "send-keys", "-t", api.CLAUDE_PANE, "Escape"], timeout=3)
        except Exception as e:
            print(f"[kill] esc failed: {e}", file=sys.stderr)
    # kill the deliver child if any
    with _busy_lock:
        busy = api.read_json(api.BUSY_PATH)
        if isinstance(busy, dict) and busy.get("deliver_pid"):
            try:
                os.kill(busy["deliver_pid"], 9)
            except OSError:
                pass
        api.atomic_write_json(api.BUSY_PATH, {})
    # purge stale markers from Claude's pane
    try:
        subprocess.run(["tmux", "clear-history", "-t", api.CLAUDE_PANE], timeout=3)
    except Exception:
        pass
    api.send_text(chat_id, "task interrupted — ready for next")
    print("[kill] done")


def claude_is_processing() -> bool:
    """Is Claude Code mid-turn? Delegates to the shared chrome-scrape in feishu_api (used by both
    bot.py's pre-spawn check and deliver_to_claude.py's pre-paste check for consistency)."""
    return api.claude_is_busy()


def mode_is(target) -> bool:
    """Best-effort: does Claude's bottom status line currently show the `target` mode?
    Status line looks like: '◯◯ auto mode on (shift+tab to cycle) …'."""
    try:
        r = subprocess.run(
            ["tmux", "capture-pane", "-p", "-t", api.CLAUDE_PANE, "-S", "-3"],
            capture_output=True, text=True, timeout=5,
        )
        tail = " ".join((r.stdout or "").lower().splitlines()[-3:])
    except Exception:
        return False
    if target == "plan":
        return "plan mode" in tail
    if target == "auto":
        return "auto mode" in tail or "auto-accept" in tail
    if target == "manual":
        return "default mode" in tail or "manual mode" in tail
    return False


def cmd_mode(chat_id, target):
    target = (target or "").strip().lower()
    if target not in ("plan", "auto", "manual"):
        api.send_text(chat_id, f"unknown mode {target!r}; use plan, auto, or manual")
        return
    if mode_is(target):
        api.send_text(chat_id, f"already in {target} mode")
        return
    # Self-correcting: send Shift+Tab (BTab) and verify after each press. Only stop on the
    # confirmed target, so we never land on an unknown mode. Cap covers a 3-state cycle.
    for _ in range(4):
        try:
            subprocess.run(["tmux", "send-keys", "-t", api.CLAUDE_PANE, "BTab"], timeout=3)
        except Exception as e:
            print(f"[mode] send-key failed: {e}", file=sys.stderr)
        time.sleep(1.2)
        if mode_is(target):
            api.send_text(chat_id, f"mode set to {target}")
            return
    api.send_text(
        chat_id,
        f"could not confirm switch to {target} mode (shift+tab may not be wired); "
        "please switch manually",
    )


def handle_command(raw_text, chat_id, message_id):
    parts = raw_text.split(None, 2)
    sub = parts[1].lower() if len(parts) > 1 else ""
    if sub == "help":
        cmd_help(chat_id)
    elif sub == "kill":
        cmd_kill(chat_id)
    elif sub == "mode":
        cmd_mode(chat_id, parts[2] if len(parts) > 2 else "")
    else:
        api.send_text(chat_id, f"unknown command: {sub}\n\n{api.HELP_TEXT}")


# ---------- main event handler ----------
def on_message_receive(data: P2ImMessageReceiveV1) -> None:
    msg = data.event.message
    # only handle messages sent AFTER this bot process started; discard anything older
    if is_stale_message(msg):
        print(f"[recv] discard pre-boot message {getattr(msg, 'message_id', None)} "
              f"(create_time={getattr(msg, 'create_time', None)} < boot={int(BOOT_TIME)})")
        return
    sender_open_id = data.event.sender.sender_id.open_id
    raw_text = extract_text(msg)
    text = strip_mentions(raw_text)
    chat_id = getattr(msg, "chat_id", None)
    chat_type = getattr(msg, "chat_type", None)
    message_id = msg.message_id

    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "message_id": message_id,
        "chat_id": chat_id,
        "chat_type": chat_type,
        "message_type": msg.message_type,
        "sender_open_id": sender_open_id,
        "text": text,
        "raw_text": raw_text,
    }
    print(f"[recv] {record}")
    append_inbox(record)

    # internal commands (handled regardless of busy state)
    if text.lower().startswith("command "):
        handle_command(text, chat_id, message_id)
        return

    # ingress dedup (Feishu redelivery)
    if _seen_mark(message_id):
        print(f"[recv] duplicate {message_id}, dropping")
        return

    deliver_in_thread(new_corrid(), message_id, chat_id, chat_type, text)


def main() -> None:
    if not api.APP_ID or not api.APP_SECRET:
        sys.exit("ERROR: set FEISHU_APP_ID and FEISHU_APP_SECRET (or put them in .env).")

    dispatcher = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message_receive)
        .build()
    )
    client = lark.ws.Client(
        api.APP_ID,
        api.APP_SECRET,
        event_handler=dispatcher,
        log_level=lark.LogLevel.INFO,
    )
    print(f"[boot] orchestrator starting (conversation dir={api.CONVERSATION_DIR})")
    print(f"[boot] claude_pane={api.CLAUDE_PANE} working={api.EMOJI_WORKING} done={api.EMOJI_DONE} stale={api.STALE_SEC}s boot={int(BOOT_TIME)}")
    client.start()


if __name__ == "__main__":
    main()
