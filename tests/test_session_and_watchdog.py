"""Tests for session_store persistence and the stuck watchdog."""
from __future__ import annotations

import asyncio
from pathlib import Path

from bridge import session_store
from bridge.watchdog import StuckWatchdog


def test_session_store_roundtrip_and_corrupt(tmp_path, monkeypatch):
    f = tmp_path / "sessions.json"
    monkeypatch.setattr(session_store, "_SESSIONS_FILE", f)

    assert session_store.get_session_id("oc_a") is None
    session_store.set_session_id("oc_a", "sess-1", "/repo")
    assert session_store.get_session_id("oc_a") == "sess-1"

    # a second scope coexists
    session_store.set_session_id("oc_b", "sess-2", "/repo")
    assert session_store.get_session_id("oc_b") == "sess-2"
    assert session_store.get_session_id("oc_a") == "sess-1"  # unchanged

    # corrupt file -> reset to {} gracefully
    f.write_text("not json at all {{{")
    assert session_store.get_session_id("oc_a") is None


def test_watchdog_fires_when_idle():
    fired = asyncio.Event()

    async def on_stuck():
        fired.set()

    async def go():
        wd = StuckWatchdog(timeout=0.1, on_stuck=on_stuck,
                           is_approval_pending=lambda: False, tick=0.03)
        wd.start()
        await asyncio.wait_for(fired.wait(), timeout=1.0)
        wd.stop()

    asyncio.run(go())


def test_watchdog_paused_while_approval_pending():
    fired = asyncio.Event()

    async def on_stuck():
        fired.set()

    async def go():
        wd = StuckWatchdog(timeout=0.1, on_stuck=on_stuck,
                           is_approval_pending=lambda: True, tick=0.03)
        wd.start()
        await asyncio.sleep(0.3)
        wd.stop()
        assert not fired.is_set()  # paused -> never fires

    asyncio.run(go())


def test_watchdog_bump_prevents_fire():
    fired = asyncio.Event()

    async def on_stuck():
        fired.set()

    async def go():
        wd = StuckWatchdog(timeout=0.1, on_stuck=on_stuck, tick=0.03)

        async def bumper():
            for _ in range(10):
                wd.bump()
                await asyncio.sleep(0.03)

        wd.start()
        await bumper()
        wd.stop()
        assert not fired.is_set()

    asyncio.run(go())
