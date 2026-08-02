"""Cross-process state for the bridge watchdog + keystroke forwarding.

The keeper (`mcp_channel/launcher.py`) and the MCP server (`mcp_channel/server.py`)
are separate processes. They coordinate via small JSON files under STATE_DIR.
To avoid write contention each file has EXACTLY ONE writer:

  bridge.active.json    writer: SERVER   reader: keeper
      the task currently in flight (chat_id / message_id / pushed_at). The
      watchdog only acts while a task is active, so this is its source of truth
      for "is something in progress?". Written on push, cleared on reply.

  bridge.stuck.json     writer: KEEPER   reader: server
      watchdog state: awaiting_keystroke (the keeper is holding the PTY open for
      the user's reply), alerted (already sent the stuck alert, don't spam),
      stuck_screen (the cleaned screen we forwarded), updated_at.

  bridge.keystrokes.json writer: SERVER (append)  reader: keeper (drain)
      a queue of pending keystrokes [{"seq":N,"text":"..."}]. When
      awaiting_keystroke is set, the server routes the user's Feishu reply here
      instead of pushing it as a Claude turn; the keeper drains it and types
      each entry into the PTY.

All writes go through feishu_api.atomic_write_json (fsync + os.replace); reads
through feishu_api.read_json (fail-soft to defaults). No new dependencies.
"""
from __future__ import annotations
import os
import tempfile
from pathlib import Path

import feishu_api  # for atomic_write_json / read_json / load_env

# Same expression as launcher.STATE_DIR (launcher.py:35). Computed independently
# so this module has no import dependency on launcher (which would pull in
# subprocess/select/pty at import time for callers that only need state I/O).
STATE_DIR = Path(os.environ.get("FEISHU_BRIDGE_STATE", str(Path.home() / ".feishu-bridge")))

ACTIVE_FILE = STATE_DIR / "bridge.active.json"
STUCK_FILE = STATE_DIR / "bridge.stuck.json"
KEYSTROKES_FILE = STATE_DIR / "bridge.keystrokes.json"
OUTBOX_FILE = STATE_DIR / "bridge.outbox.json"


def _ensure_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


# ---------- active task (server writes, keeper reads) ----------
def write_active(chat_id: str, message_id: str, pushed_at: float,
                 content_preview: str = "") -> None:
    """Record the task now in flight. Called by server._push."""
    _ensure_dir()
    feishu_api.atomic_write_json(
        ACTIVE_FILE,
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "pushed_at": pushed_at,
            "content_preview": content_preview,
        },
    )


def read_active() -> dict | None:
    """The active task, or None if no task is in flight (cleared on reply)."""
    d = feishu_api.read_json(ACTIVE_FILE, default=None)
    if not d or not d.get("chat_id"):
        return None
    return d


def clear_active() -> None:
    """Mark the task done. Called by server._finish_working on a successful reply."""
    try:
        ACTIVE_FILE.unlink()
    except OSError:
        pass


# ---------- watchdog / stuck state (keeper writes, server reads) ----------
def read_stuck() -> dict:
    """Watchdog state. Defaults to 'not stuck' if missing/unreadable."""
    d = feishu_api.read_json(STUCK_FILE, default=None)
    if not isinstance(d, dict):
        return {"awaiting_keystroke": False, "alerted": False,
                "stuck_screen": "", "updated_at": 0.0}
    d.setdefault("awaiting_keystroke", False)
    d.setdefault("alerted", False)
    d.setdefault("stuck_screen", "")
    d.setdefault("updated_at", 0.0)
    return d


def write_stuck(awaiting_keystroke: bool, alerted: bool,
                stuck_screen: str = "", updated_at: float = 0.0) -> None:
    """Persist watchdog state. Called by the keeper."""
    _ensure_dir()
    feishu_api.atomic_write_json(
        STUCK_FILE,
        {
            "awaiting_keystroke": awaiting_keystroke,
            "alerted": alerted,
            "stuck_screen": stuck_screen,
            "updated_at": updated_at,
        },
    )


def clear_stuck() -> None:
    """Reset watchdog state (e.g. after a keystroke is applied)."""
    write_stuck(awaiting_keystroke=False, alerted=False, stuck_screen="", updated_at=0.0)


def is_awaiting_keystroke() -> bool:
    """Cheap read for the server's inbound intercept path."""
    return bool(read_stuck().get("awaiting_keystroke"))


# ---------- keystroke queue (server appends, keeper drains) ----------
# seq gives a stable apply order across append+drain. We can't use time.time_ns()
# portably across both processes for a monotonic counter, so use an in-file
# max+1 (the queue is small and drained within ~1s, collisions are harmless).
def push_keystroke(text: str) -> None:
    """Append a keystroke request (the user's reply) to the queue."""
    _ensure_dir()
    cur = feishu_api.read_json(KEYSTROKES_FILE, default=[])
    if not isinstance(cur, list):
        cur = []
    seq = max([int(k.get("seq", 0)) for k in cur if isinstance(k, dict)] + [0]) + 1
    cur.append({"seq": seq, "text": text})
    feishu_api.atomic_write_json(KEYSTROKES_FILE, cur)


def drain_keystrokes() -> list[dict]:
    """Return all pending keystrokes (sorted by seq) and empty the queue."""
    cur = feishu_api.read_json(KEYSTROKES_FILE, default=[])
    if not isinstance(cur, list) or not cur:
        return []
    cur = [k for k in cur if isinstance(k, dict) and "text" in k]
    cur.sort(key=lambda k: int(k.get("seq", 0)))
    # Clear the queue atomically.
    try:
        feishu_api.atomic_write_json(KEYSTROKES_FILE, [])
    except Exception:
        pass
    return cur


def clear_keystrokes() -> None:
    try:
        feishu_api.atomic_write_json(KEYSTROKES_FILE, [])
    except Exception:
        pass


# ---------- outbox (keeper writes, server sends) ----------
# The watchdog lives in the KEEPER process, which runs under the system python
# and does NOT have lark_oapi installed (only the uvx server env does). So the
# keeper cannot send Feishu messages directly. Instead it queues them here and
# the MCP server (which has lark) drains + sends them. This is the reverse of
# the keystroke queue (server->keeper): here keeper->server.
def push_outbox(chat_id: str, text: str, kind: str = "progress") -> None:
    """Queue a Feishu message for the server to send (progress/stuck alerts).

    `kind` ("progress" or "stuck") tags how the server should deliver it: a
    `progress` (working-status digest) updates the previous digest in place when
    possible; a `stuck` alert is always a fresh message."""
    _ensure_dir()
    cur = feishu_api.read_json(OUTBOX_FILE, default=[])
    if not isinstance(cur, list):
        cur = []
    seq = max([int(k.get("seq", 0)) for k in cur if isinstance(k, dict)] + [0]) + 1
    cur.append({"seq": seq, "chat_id": chat_id, "text": text, "kind": kind})
    feishu_api.atomic_write_json(OUTBOX_FILE, cur)


def drain_outbox() -> list[dict]:
    """Return all queued messages (sorted by seq) and empty the outbox."""
    cur = feishu_api.read_json(OUTBOX_FILE, default=[])
    if not isinstance(cur, list) or not cur:
        return []
    cur = [k for k in cur if isinstance(k, dict) and k.get("chat_id") and k.get("text")]
    cur.sort(key=lambda k: int(k.get("seq", 0)))
    try:
        feishu_api.atomic_write_json(OUTBOX_FILE, [])
    except Exception:
        pass
    return cur
