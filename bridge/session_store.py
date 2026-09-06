"""Per-scope session persistence.

Maps each scope to the claude ``session_id`` so a follow-up turn (or a restart after a
crash) can ``--resume`` the same conversation. Stored as ``conversation/sessions.json``
via :func:`feishu_api.atomic_write_json` (temp + fsync + replace). Survives a corrupt
file by resetting to ``{}``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import feishu_api

_SESSIONS_FILE: Path = feishu_api.CONVERSATION_DIR / "sessions.json"


def _load() -> dict[str, dict]:
    data = feishu_api.read_json(_SESSIONS_FILE, default=None)
    return data if isinstance(data, dict) else {}


def _save(table: dict[str, dict]) -> None:
    feishu_api.atomic_write_json(_SESSIONS_FILE, table)


def get_session_id(scope: str, agent: str = "claude") -> Optional[str]:
    """The stored session id for ``scope`` — only when it belongs to ``agent``.

    Entries record which backend created them (``agent`` field; legacy entries
    predate the field and are claude's). A session id from another backend must
    not be resumed: codex would fail with ``no rollout found for thread id``."""
    entry = _load().get(scope)
    if isinstance(entry, dict):
        if (entry.get("agent") or "claude") != agent:
            return None
        sid = entry.get("session_id")
        return sid if isinstance(sid, str) and sid else None
    return None


def set_session_id(scope: str, session_id: str, cwd: str, agent: str = "claude") -> None:
    if not session_id:
        return
    table = _load()
    table[scope] = {
        "session_id": session_id,
        "cwd": cwd,
        "agent": agent,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _save(table)
