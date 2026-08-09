"""Bridge configuration.

Reads ``.env`` / ``CLAUDE_PLUGIN_OPTION_*`` via :mod:`feishu_api` (which loads
``.env`` on import and exposes :func:`feishu_api.cred`). All keys have safe
defaults so :meth:`BridgeConfig.load` works with no ``.env`` at all (used in tests
and first-run). See PRD §6 for the key list.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import feishu_api  # ensures load_env() has run; exposes cred(), PROJECT_DIR, CONVERSATION_DIR

PROJECT_DIR: Path = feishu_api.PROJECT_DIR


def _env(key: str, default: str = "") -> str:
    """Resolve a config key via feishu_api.cred (plugin userConfig path first, then env/.env)."""
    return feishu_api.cred(key) or default


def _int(key: str, default: int) -> int:
    raw = _env(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass
class BridgeConfig:
    """Resolved bridge configuration (immutable values read at startup)."""

    # Where Claude does its work (cwd of every claude subprocess).
    workdir: Path
    # Where the bridge keeps sessions.json (reuses the Feishu conversation dir).
    conversation_dir: Path
    # Global cap on simultaneously active scopes (turns in flight).
    max_concurrent: int
    # Seconds with no stream events (and no pending approval) before a turn is "stuck".
    stuck_timeout: int
    # Seconds an approval card waits before auto-denying.
    approval_timeout: int
    # Tool names that run without an approval card.
    auto_approve_tools: set[str]
    # Minimum gap between streaming-card updates, in milliseconds.
    card_throttle_ms: int
    # A turn runs this long (seconds) before any "Working..." progress card is sent.
    card_defer_sec: int
    # Once the progress card exists, update it every this many seconds.
    card_interval_sec: int
    # Permission mode used when approval cards are unavailable (e.g. claude lacks
    # --permission-prompt-tool). Normally approvals drive the policy instead.
    default_permission_mode: str
    # Emoji reaction codes for the working -> done cycle.
    emoji_working: str
    emoji_done: str
    # Path to the claude CLI (bundled PATH lookup; overridable via FEISHU_CLAUDE_BIN).
    claude_bin: str

    @classmethod
    def load(cls) -> "BridgeConfig":
        workdir_raw = _env("FEISHU_WORKDIR", "").strip()
        workdir = Path(workdir_raw).expanduser().resolve() if workdir_raw else PROJECT_DIR

        auto = {
            t.strip()
            for t in _env(
                "FEISHU_AUTO_APPROVE_TOOLS",
                "Read,Grep,Glob,WebSearch,WebFetch,TodoWrite",
            ).split(",")
            if t.strip()
        }

        claude_bin = _env("FEISHU_CLAUDE_BIN", "").strip() or shutil.which("claude") or "claude"

        return cls(
            workdir=workdir,
            conversation_dir=feishu_api.CONVERSATION_DIR,
            max_concurrent=_int("FEISHU_MAX_CONCURRENT", 4),
            stuck_timeout=_int("FEISHU_STUCK_TIMEOUT", 180),
            approval_timeout=_int("FEISHU_APPROVAL_TIMEOUT", 300),
            auto_approve_tools=auto,
            card_throttle_ms=_int("FEISHU_CARD_THROTTLE_MS", 1500),
            card_defer_sec=_int("FEISHU_CARD_DEFER_SEC", 60),
            card_interval_sec=_int("FEISHU_CARD_INTERVAL_SEC", 30),
            default_permission_mode=_env("FEISHU_DEFAULT_PERMISSION_MODE", "bypassPermissions"),
            emoji_working=_env("FEISHU_EMOJI_WORKING", feishu_api.EMOJI_WORKING),
            emoji_done=_env("FEISHU_EMOJI_DONE", feishu_api.EMOJI_DONE),
            claude_bin=claude_bin,
        )
