#!/usr/bin/env python3
"""
Shared Feishu/Lark REST client + helpers for the bridge (`bridge/`).

Provides the REST `lark.Client` (cached) and the send / update / reaction actions
the bridge uses (text + interactive cards + emoji reactions), plus .env loading.
The inbound websocket long-connection ingest lives in `bridge/ingest.py` and builds
its own `lark.ws.Client` — do NOT route the websocket client through this module.

NOTE on imports: `lark_oapi` is imported LAZILY (inside the functions that use it),
not at module top. Importing lark_oapi is slow (~100s on a WSL /mnt/c drvfs), and
deferring it lets the bridge start fast and import lark in the background. The first
send/react call pays the import cost once.
"""

import json
import os
import re
import tempfile
from pathlib import Path

# bridge/feishu_api.py -> parent = bridge package dir, parent.parent = the repo/project root.
# Robust whether imported from an editable checkout (-> repo) or run from an installed copy
# (the caller sets cwd to the install dir; load_env also checks cwd / FEISHU_ENV_FILE).
PROJECT_DIR = Path(__file__).resolve().parent.parent


def _env_candidates() -> list[Path]:
    cands: list[Path] = []
    explicit = os.environ.get("FEISHU_ENV_FILE")
    if explicit:
        cands.append(Path(explicit).expanduser())
    cands.append(Path.cwd() / ".env")
    cands.append(PROJECT_DIR / ".env")
    return cands


def load_env(path: Path = None) -> None:
    """Load a .env file. Never overrides existing env vars. Strips inline `#` comments that are
    preceded by whitespace AND outside of quotes (so values like URLs containing # are safe).
    With no explicit path, searches FEISHU_ENV_FILE, then cwd/.env, then PROJECT_DIR/.env."""
    if path is None:
        for c in _env_candidates():
            if c.is_file():
                path = c
                break
    if path is None or not path.is_file():
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
def cred(key: str) -> str:
    """Resolve a config value: CLAUDE_PLUGIN_OPTION_<key> (plugin userConfig path) first,
    then the classic <key> (env / .env dev path). Empty string if neither."""
    return os.environ.get(f"CLAUDE_PLUGIN_OPTION_{key}") or os.environ.get(key, "")


APP_ID = cred("FEISHU_APP_ID")
APP_SECRET = cred("FEISHU_APP_SECRET")
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


def update_text(message_id: str, text: str) -> bool:
    """Edit a text message the bot previously sent (PUT im/v1/messages/:id).

    The bot can only edit its OWN messages, and the API only supports text / rich
    text (post). Scope is satisfied by im:message:send_as_bot (already granted) —
    no new permission needed. Returns True on success, False otherwise (fail-soft:
    callers fall back to sending a fresh message)."""
    from lark_oapi.api.im.v1 import UpdateMessageRequest, UpdateMessageRequestBody
    req = (
        UpdateMessageRequest.builder()
        .message_id(message_id)
        .request_body(
            UpdateMessageRequestBody.builder()
            .content(json.dumps({"text": text}, ensure_ascii=False))
            .build()
        )
        .build()
    )
    try:
        resp = client().im.v1.message.update(req)
    except Exception:
        return False
    return resp.success()


def send_card(chat_id: str, card: dict):
    """Send an interactive card to chat_id. Returns message_id or None (fail-soft).

    `card` is the Feishu interactive-card JSON object (v1 schema); it is serialized as
    msg_type "interactive". Used by the bridge for streaming cards and approval cards."""
    from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody
    req = (
        CreateMessageRequest.builder()
        .receive_id_type("chat_id")
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("interactive")
            .content(json.dumps(card, ensure_ascii=False))
            .build()
        )
        .build()
    )
    try:
        resp = client().im.v1.message.create(req)
    except Exception:
        return None
    if resp.success():
        return getattr(getattr(resp, "data", None), "message_id", None)
    return None


def update_card(message_id: str, card: dict) -> bool:
    """Edit an interactive card the bot previously sent (PUT im/v1/messages/:id).

    Re-renders the whole card from `card`. Fail-soft: returns False on any error so
    callers can skip a failed throttled update without crashing the turn."""
    from lark_oapi.api.im.v1 import UpdateMessageRequest, UpdateMessageRequestBody
    req = (
        UpdateMessageRequest.builder()
        .message_id(message_id)
        .request_body(
            UpdateMessageRequestBody.builder()
            .content(json.dumps(card, ensure_ascii=False))
            .build()
        )
        .build()
    )
    try:
        resp = client().im.v1.message.update(req)
    except Exception:
        return False
    return resp.success()


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
