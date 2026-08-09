"""Tests for runtime intake/card-action routing and supervisor detached spawn."""
from __future__ import annotations

import asyncio
import os

from bridge import runtime, supervisor
from bridge.config import BridgeConfig
from bridge.runtime import Runtime


def test_runtime_routes_message_stop_and_card_action(tmp_path, monkeypatch):
    async def go():
        cfg = BridgeConfig.load()
        cfg.workdir = tmp_path
        rt = Runtime(cfg)
        rt._loop = asyncio.get_event_loop()
        calls: list = []

        class FakeRunner:
            def __init__(self, *a, **k):
                pass

            async def handle_message(self, evt):
                calls.append(("msg", evt.get("text")))

            async def request_stop(self):
                calls.append(("stop", None))
                return True

        monkeypatch.setattr(runtime, "ScopeRunner", FakeRunner)
        # Bypass the real .env allowlist so the synthetic sender is accepted.
        monkeypatch.setattr(runtime.access, "allowed", lambda *a, **k: True)

        # /stop routes to request_stop
        await rt._handle_message({"message_id": "m0", "chat_id": "oc", "open_id": "ou", "text": "/stop"})
        # a normal (non-stale) message routes to handle_message
        await rt._handle_message({
            "message_id": "m1", "chat_id": "oc", "open_id": "ou", "text": "hi",
            "create_time": str(int(rt.boot_time) + 1000),
        })
        # stale message is dropped
        await rt._handle_message({
            "message_id": "m2", "chat_id": "oc", "open_id": "ou", "text": "stale",
            "create_time": str(int(rt.boot_time) - 1000),
        })

        assert ("stop", None) in calls
        assert ("msg", "hi") in calls
        assert ("msg", "stale") not in calls  # dropped

        # card action: approval -> approvals.resolve
        resolved: list = []
        monkeypatch.setattr(rt.approvals, "resolve", lambda token, v: resolved.append((token, v)) or True)
        await rt._handle_card_action({"value": {"v": "approve", "t": "tok1"}})
        assert resolved == [("tok1", "approve")]

        # card action: stop -> runner.request_stop
        await rt._handle_card_action({"value": {"v": "stop", "s": "oc"}})
        assert calls.count(("stop", None)) >= 2

    asyncio.run(go())


def test_supervisor_up_uses_detached_spawn_and_writes_pidfile(tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor, "RUN_DIR", tmp_path)
    monkeypatch.setattr(supervisor, "PIDFILE", tmp_path / "bridge.pid")
    monkeypatch.setattr(supervisor, "LOGFILE", tmp_path / "bridge.log")

    captured: dict = {}

    class FakeProc:
        pid = 12345

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured.update(kwargs)
        return FakeProc()

    monkeypatch.setattr(supervisor.subprocess, "Popen", fake_popen)

    assert supervisor.up() == 0
    assert captured["cmd"][-2:] == ["run"] or captured["cmd"][-1] == "run"
    assert captured["stdin"] is supervisor.subprocess.DEVNULL
    if os.name == "nt":
        assert captured.get("creationflags", 0) & 0x00000200  # CREATE_NEW_PROCESS_GROUP
    else:
        assert captured.get("start_new_session") is True
    assert (tmp_path / "bridge.pid").read_text() == "12345"


def test_supervisor_status_when_no_pidfile(tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor, "PIDFILE", tmp_path / "bridge.pid")
    assert supervisor.status() == 1  # not running
