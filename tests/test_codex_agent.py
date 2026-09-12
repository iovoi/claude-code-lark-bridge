"""Unit tests for codex-agent support: config keys, adapter dispatch, argv
construction, codex JSONL event mapping, and a run_turn driven against a fake
subprocess (no real codex spawned). Mirrors the fake-stream style of
test_transport.py; plain sync test functions with asyncio.run.
"""
from __future__ import annotations

import asyncio
import json
import os
import types

from bridge.agent import make_adapter
from bridge.agent.claude_adapter import ClaudeAdapter
from bridge.agent.codex_adapter import (
    CodexAdapter,
    _build_codex_argv,
    _map_event,
)
from bridge.config import BridgeConfig
from bridge.__main__ import _build_parser


def _cfg(**over):
    cfg = BridgeConfig.load()
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


# ---- argv ---------------------------------------------------------------------

def test_codex_argv_fresh_turn():
    argv = _build_codex_argv("codex", sandbox="workspace-write", extra_args=[], resume=None)
    assert argv[:2] == ["codex", "exec"]
    assert "--json" in argv
    # sandbox goes via -c config override (exec resume rejects -s)
    assert "-s" not in argv
    assert argv[argv.index("-c") + 1] == 'sandbox_mode="workspace-write"'
    assert argv[-1] == "-"  # prompt rides on stdin
    assert "resume" not in argv


def test_codex_argv_resume_and_extra_args():
    argv = _build_codex_argv(
        "codex", sandbox="read-only", extra_args=["-c", "model=x"], resume="sess-1"
    )
    assert argv[:4] == ["codex", "exec", "resume", "sess-1"]
    assert "--json" in argv
    assert "-s" not in argv  # regression: -s broke every resumed turn (rc=2)
    cfgs = [argv[i + 1] for i, a in enumerate(argv) if a == "-c"]
    assert 'sandbox_mode="read-only"' in cfgs
    assert "model=x" in cfgs
    assert argv[-1] == "-"


# ---- event mapping ------------------------------------------------------------

def _run_map(events):
    ref = {"session_id": None}
    out, done_flags = [], []
    for evt in events:
        evts, done = _map_event(evt, ref)
        out.extend(evts)
        done_flags.append(done)
    return out, done_flags, ref


def test_map_thread_started_and_turn_completed():
    out, done, ref = _run_map([
        {"type": "thread.started", "thread_id": "th-123"},
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}},
    ])
    assert ref["session_id"] == "th-123"
    names = [type(e).__name__ for e in out]
    assert names == ["SystemEvent", "UsageEvent", "DoneEvent"]
    assert out[0].session_id == "th-123"
    assert (out[1].input_tokens, out[1].output_tokens) == (10, 5)
    assert done == [False, True]
    assert out[-1].reason == "normal"


def test_map_agent_message_and_reasoning():
    out, _, _ = _run_map([
        {"type": "item.completed", "item": {"type": "agent_message", "text": "hello"}},
        {"type": "item.completed", "item": {
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "thinking…"}],
        }},
    ])
    assert type(out[0]).__name__ == "TextEvent" and out[0].text == "hello"
    assert type(out[1]).__name__ == "ThinkingEvent" and "thinking" in out[1].text


def test_map_command_execution_pair():
    out, _, _ = _run_map([
        {"type": "item.started", "item": {"id": "i1", "type": "command_execution", "command": "ls"}},
        {"type": "item.completed", "item": {
            "id": "i1", "type": "command_execution", "command": "ls",
            "aggregated_output": "a\nb", "exit_code": 0, "status": "completed",
        }},
    ])
    use, result = out
    assert type(use).__name__ == "ToolUseEvent" and use.name == "shell"
    assert use.input == {"command": "ls"}
    assert type(result).__name__ == "ToolResultEvent"
    assert result.output == "a\nb" and not result.is_error


def test_map_command_execution_error():
    out, _, _ = _run_map([
        {"type": "item.completed", "item": {
            "id": "i1", "type": "command_execution", "command": "boom",
            "aggregated_output": "err", "exit_code": 2, "status": "completed",
        }},
    ])
    assert out[0].is_error


def test_map_error_event():
    out, done, _ = _run_map([{"type": "error", "message": "auth failed"}])
    assert type(out[0]).__name__ == "ErrorEvent" and out[0].message == "auth failed"
    assert done == [True] and out[-1].reason == "error"


# ---- factory ------------------------------------------------------------------

def test_make_adapter_dispatch():
    assert isinstance(make_adapter(_cfg(agent="codex")), CodexAdapter)
    assert isinstance(make_adapter(_cfg(agent="claude")), ClaudeAdapter)
    # unknown values fall back to claude at config load; factory defaults too
    assert isinstance(make_adapter(_cfg(agent="whatever")), ClaudeAdapter)


def test_cli_accepts_agent_flag():
    up = _build_parser().parse_args(["up", "--agent", "codex"])
    assert up.agent == "codex"
    run = _build_parser().parse_args(["run", "--agent", "claude"])
    assert run.agent == "claude"
    assert _build_parser().parse_args(["up"]).agent is None


def test_cli_agent_flag_sets_env(monkeypatch):
    import bridge.__main__ as m

    called = {}
    monkeypatch.setattr("bridge.supervisor.handle", lambda cmd: called.setdefault("cmd", cmd) or 0)
    monkeypatch.delenv("FEISHU_AGENT", raising=False)
    m.main(["up", "--agent", "codex"])
    assert os.environ.get("FEISHU_AGENT") == "codex"
    assert called["cmd"] == "up"


# ---- run_turn over a fake subprocess -------------------------------------------

class FakeStdout:
    def __init__(self, lines: list[str]) -> None:
        self._lines = [l.encode() for l in lines]

    async def readline(self):
        return self._lines.pop(0) if self._lines else b""


class FakeStdin:
    def __init__(self) -> None:
        self.written = b""

    def write(self, data): self.written += data

    async def drain(self): pass

    def close(self): pass


class FakeProc:
    def __init__(self, lines, rc: int = 0) -> None:
        self.stdin = FakeStdin()
        self.stdout = FakeStdout(lines)
        self.stderr = None
        self.returncode = rc

    async def wait(self):
        return self.returncode


def test_run_turn_maps_stream_and_resumes(monkeypatch):
    lines = [
        json.dumps({"type": "thread.started", "thread_id": "th-9"}),
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "hi"}}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 2}}),
    ]
    spawned = {}

    def fake_exec(*argv, **kwargs):
        spawned["argv"] = argv
        return _ok(FakeProc(lines))

    async def _ok(proc):
        return proc

    monkeypatch.setattr("bridge.agent.codex_adapter.asyncio.create_subprocess_exec", fake_exec)
    cfg = _cfg(agent="codex")
    ad = CodexAdapter(cfg)
    events = []

    async def emit(evt):
        events.append(evt)

    async def go():
        await ad.start()
        info = await ad.run_turn("do it", emit)
        return info

    info = asyncio.run(go())
    assert spawned["argv"][1:3] == ("exec", "--json") or "--json" in spawned["argv"]
    assert b"do it" in ad._proc.stdin.written  # prompt went via stdin
    assert info["session_id"] == "th-9"
    # later turns resume the captured thread id
    monkeypatch.setattr(
        "bridge.agent.codex_adapter.asyncio.create_subprocess_exec",
        lambda *a, **k: _ok(FakeProc(["", json.dumps({"type": "turn.completed"})])),
    )
    async def noop(evt):
        pass

    asyncio.run(ad.run_turn("again", noop))
    assert ad._resume == "th-9"
    kinds = [type(e).__name__ for e in events]
    assert "TextEvent" in kinds and "DoneEvent" in kinds


def test_run_turn_failed_exit_surfaces_error(monkeypatch):
    """codex dies on stderr with empty stdout (e.g. bad resume id) -> ErrorEvent,
    not a silent empty turn (the Done-emoji-only bug)."""
    async def _ok(proc):
        return proc

    monkeypatch.setattr(
        "bridge.agent.codex_adapter.asyncio.create_subprocess_exec",
        lambda *a, **k: _ok(FakeProc([], rc=1)),
    )
    ad = CodexAdapter(_cfg(agent="codex"))
    events = []

    async def emit(evt):
        events.append(evt)

    info = asyncio.run(ad.run_turn("hello", emit))
    assert info["exit_code"] == 1
    kinds = [type(e).__name__ for e in events]
    assert "ErrorEvent" in kinds
    assert events[-1].reason == "error"
    err = next(e for e in events if type(e).__name__ == "ErrorEvent")
    assert "rc=1" in err.message


# ---- agent-aware session store --------------------------------------------------

def test_session_store_gates_by_agent(tmp_path, monkeypatch):
    from bridge import session_store

    f = tmp_path / "sessions.json"
    monkeypatch.setattr(session_store, "_SESSIONS_FILE", f)
    session_store.set_session_id("s1", "codex-thread-1", "/w", agent="codex")
    assert session_store.get_session_id("s1", agent="codex") == "codex-thread-1"
    assert session_store.get_session_id("s1", agent="claude") is None  # other backend: no resume

    session_store.set_session_id("s1", "claude-sess-1", "/w")  # default agent=claude
    assert session_store.get_session_id("s1", agent="claude") == "claude-sess-1"
    assert session_store.get_session_id("s1", agent="codex") is None


def test_session_store_legacy_entry_is_claude(tmp_path, monkeypatch):
    """Entries written before the agent field existed are claude sessions."""
    from bridge import session_store

    f = tmp_path / "sessions.json"
    f.write_text('{"s1": {"session_id": "541ae274-9d7f", "cwd": "/w", '
                  '"updated_at": "2026-09-06T00:00:00+00:00"}}')
    monkeypatch.setattr(session_store, "_SESSIONS_FILE", f)
    assert session_store.get_session_id("s1") == "541ae274-9d7f"
    assert session_store.get_session_id("s1", agent="claude") == "541ae274-9d7f"
    assert session_store.get_session_id("s1", agent="codex") is None


# ---- fresh-thread default (FEISHU_RESUME_SESSIONS) ------------------------------

def test_resume_sessions_config_default_off(monkeypatch, tmp_path):
    import bridge.config as c

    monkeypatch.setattr(c, "_env", lambda key, default="": {
        "FEISHU_RESUME_SESSIONS": "", "FEISHU_AGENT": "codex",
    }.get(key, default))
    monkeypatch.setattr(c.feishu_api, "CONVERSATION_DIR", tmp_path)
    assert c.BridgeConfig.load().resume_sessions is False

    monkeypatch.setattr(c, "_env", lambda key, default="": {
        "FEISHU_RESUME_SESSIONS": "1", "FEISHU_AGENT": "codex",
    }.get(key, default))
    assert c.BridgeConfig.load().resume_sessions is True


def test_scope_factory_fresh_by_default(tmp_path, monkeypatch):
    from bridge import session_store
    from bridge.scope import ScopeRunner

    f = tmp_path / "sessions.json"
    f.write_text('{"sc1": {"session_id": "th-old", "cwd": "/w", "agent": "codex"}}')
    monkeypatch.setattr(session_store, "_SESSIONS_FILE", f)

    def make_runner(resume_sessions: bool):
        cfg = _cfg(agent="codex")
        cfg.resume_sessions = resume_sessions
        return ScopeRunner("sc1", "chat1", cfg, None, None)

    ad = make_runner(resume_sessions=False)._default_adapter_factory()
    assert ad._resume is None  # default: fresh thread, no resume

    ad2 = make_runner(resume_sessions=True)._default_adapter_factory()
    assert ad2._resume == "th-old"  # opt-in: cross-restart resume
