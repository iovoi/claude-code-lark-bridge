#!/usr/bin/env python3
"""
Shared helpers for the Feishu/Lark MCP channel (mcp_channel/).

Provides the Feishu REST client + the send/react actions the channel's `reply` /
`react` tools call, plus .env loading. The inbound websocket ingest lives in
mcp_channel/feishu_ingest.py (it uses lark.ws.Client directly).

NOTE on clients:
  - client() here returns the REST `lark.Client` used for sending messages and
    reactions.
  - mcp_channel/feishu_ingest.py separately builds a `lark.ws.Client` for the
    WebSocket long-connection ingest. Do NOT route the websocket client through
    this module.

NOTE on imports: `lark_oapi` is imported LAZILY (inside the functions that use
it), not at module top. Importing lark_oapi is slow (~100s on a WSL /mnt/c drvfs),
and deferring it lets the MCP server answer `initialize` immediately and import
lark in the background. The first send/react call pays the import cost once.
"""

import json
import os
import re
import tempfile
from pathlib import Path

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
# Emoji reaction codes (Feishu UPPER_SNAKE). Used by the channel's working->done cycle.
EMOJI_WORKING = os.environ.get("FEISHU_EMOJI_WORKING", "OnIt")
EMOJI_DONE = os.environ.get("FEISHU_EMOJI_DONE", "Done")

_client = None



def route_lark_logs_to_stderr() -> None:
    """lark_oapi/core/log.py attaches a StreamHandler(sys.stdout) to the 'Lark'
    logger. Under the MCP stdio protocol stdout MUST be pure JSON-RPC, so any
    lark log line there (e.g. '[Lark] [INFO] connected to wss://...') corrupts
    the stream ('Ignoring non-JSON line on stdout'). Move lark's logs to stderr.

    Idempotent: call after importing lark_oapi (it installs the stdout handler
    at import time)."""
    import logging
    import sys
    lk = logging.getLogger("Lark")
    for h in list(lk.handlers):
        if getattr(h, "stream", None) is sys.stdout:
            lk.removeHandler(h)
    if not any(getattr(h, "stream", None) is sys.stderr for h in lk.handlers):
        lk.addHandler(logging.StreamHandler(sys.stderr))
    lk.propagate = False
    lk.setLevel(logging.DEBUG)
    for h in lk.handlers:
        h.setLevel(logging.DEBUG)


def client():
    """REST client (cached singleton; the SDK refreshes the tenant token as needed).
    Imports lark_oapi lazily on first use."""
    global _client
    if _client is None:
        import lark_oapi as lark
        route_lark_logs_to_stderr()
        _client = lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).build()
    return _client


# --- JSON helpers (atomic) ---
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


# --- Feishu REST actions (lark_oapi imported lazily inside each) ---
def send_text(chat_id: str, text: str):
    """Send a text message to chat_id. Returns message_id or None."""
    from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody
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
    from lark_oapi.api.im.v1 import (
        CreateMessageReactionRequest,
        CreateMessageReactionRequestBody,
        Emoji,
    )
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
    from lark_oapi.api.im.v1 import DeleteMessageReactionRequest
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
