"""End-to-end tests for ClaudeAdapter against the fake-claude stub (no real API).

Covers: streaming text + result + session-id capture; an approval raised and resolved
(allow); deny+stop interrupt path.
"""
from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path

from bridge.agent import DoneEvent, TextEvent
from bridge.agent.claude_adapter import ClaudeAdapter
from bridge.config import BridgeConfig

STUB = Path(__file__).parent / "fake_claude.py"


def _cfg(tmp_path):
    os.chmod(STUB, 0o755)
    cfg = BridgeConfig.load()
    cfg.claude_bin = str(STUB)
    cfg.workdir = tmp_path
    cfg.auto_approve_tools = set()  # force approvals for everything in approval mode
    return cfg


def test_plain_turn_streams_and_captures_session(tmp_path):
    os.environ["FAKE_CLAUDE_MODE"] = "plain"

    async def go():
        adapter = ClaudeAdapter(_cfg(tmp_path))
        await adapter.start()
        events = []

        async def emit(e):
            events.append(e)

        res = await adapter.run_turn("hello", emit)
        assert res["session_id"] == "fake-session-1"
        assert any(isinstance(e, TextEvent) for e in events)
        assert any(isinstance(e, DoneEvent) for e in events)
        await adapter.stop()

    asyncio.run(go())


def test_approval_allow_resolves_and_completes(tmp_path):
    os.environ["FAKE_CLAUDE_MODE"] = "approval"
    requested: list = []

    async def approval_cb(tool, inp):
        requested.append((tool, inp))
        return "allow"

    async def go():
        adapter = ClaudeAdapter(_cfg(tmp_path), approval_callback=approval_cb)
        await adapter.start()
        events = []

        async def emit(e):
            events.append(e)

        res = await adapter.run_turn("deploy it", emit)
        assert requested and requested[0][0] == "Bash"   # approval was raised
        assert res["session_id"] == "fake-session-1"
        assert any(isinstance(e, DoneEvent) for e in events)
        await adapter.stop()

    asyncio.run(go())


def test_approval_deny_stop_interrupts_turn(tmp_path):
    os.environ["FAKE_CLAUDE_MODE"] = "approval"

    async def approval_cb(tool, inp):
        return "deny_stop"

    async def go():
        adapter = ClaudeAdapter(_cfg(tmp_path), approval_callback=approval_cb)
        await adapter.start()
        events = []

        async def emit(e):
            events.append(e)

        res = await adapter.run_turn("nuke it", emit)
        # Stub emits a terminal result ("stopped") on interrupt; turn ends cleanly.
        assert any(isinstance(e, DoneEvent) for e in events)
        assert res["session_id"] == "fake-session-1"
        await adapter.stop()

    asyncio.run(go())
