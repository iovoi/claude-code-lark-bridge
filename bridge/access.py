"""Static allowlist access control (FEISHU_ALLOWED_OPEN_IDS / FEISHU_ALLOWED_CHAT_IDS).

Moved verbatim from the old mcp_channel/access.py: env is read at import time, so
feishu_api.load_env() (run on import of feishu_api) must have executed first.
"""
from __future__ import annotations

import sys

import feishu_api


def _split(v: str | None) -> set[str]:
    return {x.strip() for x in (v or "").split(",") if x.strip()}


ALLOWED_OPEN_IDS = _split(feishu_api.cred("FEISHU_ALLOWED_OPEN_IDS"))
ALLOWED_CHAT_IDS = _split(feishu_api.cred("FEISHU_ALLOWED_CHAT_IDS"))
_warned = False


def allowed(open_id: str | None, chat_id: str | None) -> bool:
    """True if the sender/chat may talk to the bot.

    If neither allowlist is set, allow everyone (preserves v1 behavior) but warn once.
    If either is set, the sender's open_id OR the chat_id must match."""
    global _warned
    if not ALLOWED_OPEN_IDS and not ALLOWED_CHAT_IDS:
        if not _warned:
            print(
                "[access] WARNING: FEISHU_ALLOWED_OPEN_IDS / FEISHU_ALLOWED_CHAT_IDS unset — "
                "bot is open to everyone who can reach the Feishu app",
                file=sys.stderr,
            )
            _warned = True
        return True
    if chat_id and chat_id in ALLOWED_CHAT_IDS:
        return True
    if open_id and open_id in ALLOWED_OPEN_IDS:
        return True
    return False
