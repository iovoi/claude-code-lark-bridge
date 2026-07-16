#!/usr/bin/env python3
"""
Deliver one received Feishu message into the Claude Code session, then scrape Claude's
reply and send it back to Feishu (the bot — not Claude — does all Feishu I/O).

Usage:
    .venv/bin/python deliver_to_claude.py <corrid>

Flow:
  1. Read conversation/.claude_busy.json; if its corrid != <corrid> (superseded) → exit.
  2. If the message_id is already in .delivered.json → clear the busy lock (if ours) and exit.
  3. Paste a short prompt into Claude's tmux pane. Mark delivered.
  4. Poll Claude's pane for READY4NextMsg:<corrid>; scrape the reply between the
     REPLY_FeiShu_Msg:<corrid> and READY4NextMsg:<corrid> markers; send it to Feishu; stamp the
     done emoji (and remove the working one for groups); clear the busy lock.
  - Early-abort (~40s, no start marker) and hard timeout (~280s) send a notice and clear the lock.
"""

import subprocess
import sys
import time
from datetime import datetime, timezone

import feishu_api as api

EARLY_ABORT_SEC = 40
HARD_TIMEOUT_SEC = 280
POLL_SLEEP = 5


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def capture_pane() -> str:
    r = subprocess.run(
        ["tmux", "capture-pane", "-p", "-J", "-t", api.CLAUDE_PANE, "-S", "-2000"],
        capture_output=True, text=True, timeout=5,
    )
    return r.stdout


def paste_prompt(text: str) -> None:
    subprocess.run(["tmux", "send-keys", "-l", "-t", api.CLAUDE_PANE, text], check=True, timeout=3)
    subprocess.run(["tmux", "send-keys", "-t", api.CLAUDE_PANE, "Enter"], check=True, timeout=3)


def build_prompt(rec: dict, corrid: str) -> str:
    text = (rec.get("text") or "").replace("\n", " ").replace("\r", " ")
    chat_type = rec.get("chat_type") or "p2p"
    return (
        f"[FEISHU IN — reply via lark-feishu-bot skill] corrid={corrid} "
        f"chat_type={chat_type} text={text}"
    )


def clear_busy_if_match(corrid: str) -> None:
    busy = api.read_json(api.BUSY_PATH)
    if isinstance(busy, dict) and busy.get("corrid") == corrid:
        api.atomic_write_json(api.BUSY_PATH, {})


def scrape_reply(pane: str, corrid: str):
    """Return (reply_text|None, ready_seen: bool)."""
    start_tok = f"REPLY_FeiShu_Msg:{corrid}"
    end_tok = f"READY4NextMsg:{corrid}"
    lines = pane.splitlines()
    # find the LAST end marker, then nearest preceding start marker
    end_idx = None
    for i in range(len(lines) - 1, -1, -1):
        if end_tok in lines[i]:
            end_idx = i
            break
    if end_idx is None:
        # not ready yet; but report whether a start marker exists (for early-abort)
        return None, False
    start_idx = None
    for i in range(end_idx - 1, -1, -1):
        if start_tok in lines[i]:
            start_idx = i
            break
    if start_idx is None:
        return None, True  # ready seen but no matching start — treat as empty reply
    body = []
    for line in lines[start_idx + 1: end_idx]:
        c = api.clean_line(line)
        body.append(c)
    # drop leading/trailing blank lines
    while body and not body[0]:
        body.pop(0)
    while body and not body[-1]:
        body.pop()
    return ("\n".join(body) if body else ""), True


def finalize(rec: dict, corrid: str, reply: str) -> None:
    chat_id = rec.get("chat_id")
    message_id = rec.get("message_id")
    if chat_id and reply != "":
        api.send_text(chat_id, reply)
    elif chat_id and reply == "":
        api.send_text(chat_id, "(empty reply)")
    if rec.get("chat_type") == "group" and message_id:
        api.add_reaction(message_id, api.EMOJI_DONE)
        entry = api.read_json(api.REACTIONS_PATH).get(message_id, {})
        old = entry.get("reaction_id")
        if old:
            api.delete_reaction(message_id, old)
    clear_busy_if_match(corrid)


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("Usage: deliver_to_claude.py <corrid>")
    corrid = sys.argv[1]

    busy = api.read_json(api.BUSY_PATH)
    if not isinstance(busy, dict) or busy.get("corrid") != corrid:
        print(f"[deliver] busy lock does not match {corrid}; exiting", file=sys.stderr)
        return
    message_id = busy.get("message_id")
    chat_id = busy.get("chat_id")
    chat_type = busy.get("chat_type")

    # dedup: if already delivered, just clear our lock and exit
    delivered = api.read_json(api.DELIVERED_PATH)
    if isinstance(delivered, dict) and message_id and message_id in delivered:
        print(f"[deliver] {message_id} already delivered; clearing lock")
        clear_busy_if_match(corrid)
        return

    rec = {"message_id": message_id, "chat_id": chat_id, "chat_type": chat_type, "text": busy.get("text")}

    # Re-check Claude is idle IMMEDIATELY before pasting. The bot's pre-spawn check ran in a
    # different process, then this child started + imported lark_oapi (~1s); Claude may have begun
    # a turn in that gap. If Claude is busy now, never paste — it would queue and only surface when
    # the user types next. Reject so the user can `command kill` and resend.
    if api.claude_is_busy():
        if chat_id:
            api.send_text(chat_id, "Claude is working on the last message — send `command kill` to interrupt.")
        if chat_type == "group" and message_id:
            api.add_reaction(message_id, api.EMOJI_DONE)
        clear_busy_if_match(corrid)
        print(f"[deliver] abort {corrid}: Claude busy at paste time, not pasting", file=sys.stderr)
        return

    prompt = build_prompt(rec, corrid)
    try:
        paste_prompt(prompt)
    except subprocess.CalledProcessError as e:
        print(f"[deliver] tmux paste failed: {e}", file=sys.stderr)
        clear_busy_if_match(corrid)
        sys.exit(2)
    except FileNotFoundError:
        print("[deliver] tmux not found", file=sys.stderr)
        clear_busy_if_match(corrid)
        sys.exit(3)

    delivered = api.read_json(api.DELIVERED_PATH)
    delivered[message_id] = {"corrid": corrid, "chat_id": chat_id, "ts": now_iso()}
    api.atomic_write_json(api.DELIVERED_PATH, delivered)
    print(f"[deliver] pasted {corrid} into pane {api.CLAUDE_PANE}")

    start = time.time()
    saw_start = False
    while True:
        elapsed = time.time() - start
        try:
            pane = capture_pane()
        except Exception as e:
            print(f"[deliver] capture error: {e}", file=sys.stderr)
            pane = ""

        reply, ready = scrape_reply(pane, corrid)
        if not saw_start and (f"REPLY_FeiShu_Msg:{corrid}" in pane):
            saw_start = True

        if ready:
            finalize(rec, corrid, reply or "")
            print(f"[deliver] reply sent for {corrid}")
            return

        if not saw_start and elapsed > EARLY_ABORT_SEC:
            if chat_id:
                api.send_text(chat_id, "(Claude did not pick up the message — please check)")
            clear_busy_if_match(corrid)
            print(f"[deliver] early-abort {corrid} (no start marker in {EARLY_ABORT_SEC}s)", file=sys.stderr)
            return

        if elapsed > HARD_TIMEOUT_SEC:
            if chat_id:
                api.send_text(chat_id, "(timed out waiting for Claude)")
            clear_busy_if_match(corrid)
            print(f"[deliver] hard timeout {corrid}", file=sys.stderr)
            return

        time.sleep(POLL_SLEEP)


if __name__ == "__main__":
    main()
