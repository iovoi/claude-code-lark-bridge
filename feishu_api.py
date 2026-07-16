#!/usr/bin/env python3
"""
Shared helpers for the Feishu/Lark bridge scripts.

NOTE on clients:
  - client() here returns the REST `lark.Client` used for sending messages and reactions.
  - bot.py separately builds a `lark.ws.Client` for the WebSocket long-connection ingest. Do NOT route
    the websocket client through this module.
"""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    CreateMessageReactionRequest,
    CreateMessageReactionRequestBody,
    DeleteMessageReactionRequest,
    Emoji,
)

PROJECT_DIR = Path(__file__).resolve().parent


def load_env(path: Path = None) -> None:
    """Load a .env file. Never overrides existing env vars. Strips inline `#` comments that are
    preceded by whitespace AND outside of quotes (so values like URLs containing # are safe)."""
    path = path or (PROJECT_DIR / ".env")
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        # strip inline comment only if unquoted and whitespace-preceded
        if value and not (value[0] in "\"'"):
            m = re.search(r"\s+#", value)
            if m:
                value = value[: m.start()].rstrip()
        os.environ.setdefault(key, value.strip('"').strip("'"))


load_env()

# --- config (read after load_env) ---
APP_ID = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
CONVERSATION_DIR = Path(
    os.environ.get("FEISHU_CONVERSATION_DIR", str(PROJECT_DIR / "conversation"))
)
CLAUDE_PANE = os.environ.get("FEISHU_CLAUDE_PANE", "%0")
EMOJI_WORKING = os.environ.get("FEISHU_EMOJI_WORKING", "OnIt")
EMOJI_DONE = os.environ.get("FEISHU_EMOJI_DONE", "Done")
STALE_SEC = int(os.environ.get("FEISHU_STALE_SEC", "300"))

VENV_PYTHON = str(PROJECT_DIR / ".venv" / "bin" / "python")

# --- ledger paths ---
BUSY_PATH = CONVERSATION_DIR / ".claude_busy.json"
DELIVERED_PATH = CONVERSATION_DIR / ".delivered.json"
REACTIONS_PATH = CONVERSATION_DIR / ".reactions.json"

# --- internal command help (single source of truth) ---
HELP_TEXT = (
    "Supported commands:\n"
    "- command help — show this help\n"
    "- command kill — interrupt Claude's current task and return to ready\n"
    "- command mode plan|auto|manual — switch Claude Code permission mode"
)

# Box-drawing / TUI border characters to trim from scraped reply lines.
_BORDER_CHARS = "│║┃╮╭╯╰├┤┬┴┼─━┄┅┆┇┈┉┊┋╗╝╚╔║═•·●○◆◇■□▶►◀▼"

_client = None


def client() -> lark.Client:
    """REST client (cached singleton; the SDK refreshes the tenant token as needed)."""
    global _client
    if _client is None:
        _client = lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).build()
    return _client


# --- JSON ledger helpers (atomic) ---
def read_json(path: Path, default=None):
    if default is None:
        default = {}
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def atomic_write_json(path: Path, obj) -> None:
    """Write JSON atomically: write temp in same dir, fsync, os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --- Feishu REST actions ---
def send_text(chat_id: str, text: str):
    """Send a text message to chat_id. Returns message_id or None."""
    req = (
        CreateMessageRequest.builder()
        .receive_id_type("chat_id")
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(json.dumps({"text": text}, ensure_ascii=False))
            .build()
        )
        .build()
    )
    resp = client().im.v1.message.create(req)
    if resp.success():
        return getattr(getattr(resp, "data", None), "message_id", None)
    return None


def add_reaction(message_id: str, code: str):
    """Add an emoji reaction. Returns reaction_id or None (fail-soft)."""
    req = (
        CreateMessageReactionRequest.builder()
        .message_id(message_id)
        .request_body(
            CreateMessageReactionRequestBody.builder()
            .reaction_type(Emoji.builder().emoji_type(code).build())
            .build()
        )
        .build()
    )
    try:
        resp = client().im.v1.message_reaction.create(req)
    except Exception:
        return None
    if resp.success():
        return getattr(getattr(resp, "data", None), "reaction_id", None)
    return None


def delete_reaction(message_id: str, reaction_id: str) -> bool:
    req = (
        DeleteMessageReactionRequest.builder()
        .message_id(message_id)
        .reaction_id(reaction_id)
        .build()
    )
    try:
        return client().im.v1.message_reaction.delete(req).success()
    except Exception:
        return False


# --- TUI scraping helpers ---
def clean_line(s: str) -> str:
    """Strip leading/trailing whitespace and box-drawing border characters."""
    return s.strip().strip(_BORDER_CHARS).strip()


def claude_is_busy() -> bool:
    """Is Claude Code mid-turn (NOT idle at the input prompt)?

    Only the live bottom chrome is inspected (status line + input box + separator), so stale
    spinner lines from a finished turn can't false-positive. Claude Code shows 'esc to interrupt'
    on the status line during any active turn (including recaps), and the input box reads
    'queued messages' / 'press up to edit' when input has been queued on a busy Claude."""
    try:
        r = subprocess.run(
            ["tmux", "capture-pane", "-p", "-t", CLAUDE_PANE, "-S", "-3"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return False  # fail open on capture error; the deliver child re-checks anyway
    tail = " ".join((r.stdout or "").lower().splitlines()[-3:])
    return (
        "esc to interrupt" in tail
        or "queued messages" in tail
        or "press up to edit" in tail
    )
