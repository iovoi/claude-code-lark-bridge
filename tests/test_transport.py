"""Unit tests for bridge.transport (framing + control protocol).

No real claude is spawned: Transport internals are driven against injected fake
stdin/stdout streams. Uses asyncio.run inside plain sync test functions so plain
pytest works without pytest-asyncio.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from bridge.transport import Transport, _LineFramer, _build_claude_argv


class FakeStdin:
    """Records writes; drain/close are no-ops."""

    def __init__(self) -> None:
        self.written: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        pass


def _attached_transport(**kwargs) -> Transport:
    """A Transport wired to fake streams with its reader loop running (no subprocess)."""
    t = Transport("claude", cwd=".", **kwargs)
    t._stdin = FakeStdin()
    t._stdout = asyncio.StreamReader()
    t._stream_q = asyncio.Queue()
    t._reader_task = asyncio.create_task(t._read_loop())
    return t


def _written_json(t: Transport) -> list[dict]:
    return [json.loads(w.decode()) for w in t._stdin.written]  # type: ignore[attr-defined]


def test_framer_splits_across_chunks():
    f = _LineFramer()
    assert f.feed('{"a":') == []          # partial, no newline yet
    assert f.feed('1}\n{"b":') == ['{"a":1}']
    assert f.feed('2}\n') == ['{"b":2}']
    assert f.feed("") == []               # leftover consumed


def test_framer_drops_blank_lines():
    f = _LineFramer()
    assert f.feed('{"x":1}\n\n{"y":2}\n') == ['{"x":1}', '{"y":2}']


def test_argv_built_with_flags():
    argv = _build_claude_argv(
        "claude",
        session_id="abc-123",
        resume=None,
        permission_prompt_tool=True,
        permission_mode=None,
        skip_permissions=False,
        add_dirs=["/repo"],
        extra_args=[],
    )
    assert argv[0] == "claude"
    assert "-p" in argv and "--verbose" in argv
    assert "--input-format" in argv and "stream-json" in argv
    assert "--output-format" in argv
    assert "--permission-prompt-tool" in argv
    assert "--session-id=abc-123" in argv      # equals form (anti-injection)
    assert "--add-dir=/repo" in argv
    assert "--resume" not in argv


def test_argv_resume_overrides_session_id():
    argv = _build_claude_argv(
        "claude", session_id="new", resume="old", permission_prompt_tool=False,
        permission_mode=None, skip_permissions=False, add_dirs=[], extra_args=[],
    )
    assert "--resume=old" in argv
    assert not any(a.startswith("--session-id") for a in argv)
    assert "--permission-prompt-tool" not in argv  # approvals off


def test_send_user_turn_writes_valid_ndjson():
    async def go():
        t = _attached_transport()
        await t.send_user_turn("hello world")
        obj = _written_json(t)[-1]
        assert obj["type"] == "user"
        assert obj["message"]["role"] == "user"
        assert obj["message"]["content"] == "hello world"
        assert obj["parent_tool_use_id"] is None
        t._reader_task.cancel()

    asyncio.run(go())


def test_request_response_correlation():
    async def go():
        t = _attached_transport()

        async def feed_response():
            await asyncio.sleep(0.03)  # let request() write + register the future
            sent = _written_json(t)[-1]
            rid = sent["request_id"]
            resp = {
                "type": "control_response",
                "response": {"subtype": "success", "request_id": rid, "response": {"ok": True}},
            }
            t._stdout.feed_data((json.dumps(resp) + "\n").encode())  # type: ignore[attr-defined]

        result = await asyncio.gather(t.request({"subtype": "initialize"}, timeout=2.0), feed_response())
        assert result[0]["response"] == {"ok": True}
        # the outbound frame was a control_request with the right subtype
        assert _written_json(t)[0]["request"] == {"subtype": "initialize"}
        t._reader_task.cancel()

    asyncio.run(go())


def test_request_times_out_when_no_response():
    async def go():
        t = _attached_transport()
        with pytest.raises(asyncio.TimeoutError):
            await t.request({"subtype": "initialize"}, timeout=0.2)
        assert t._pending == {}  # cleaned up
        t._reader_task.cancel()

    asyncio.run(go())


def test_interrupt_sends_control_frame():
    async def go():
        t = _attached_transport()

        async def feed_response():
            await asyncio.sleep(0.03)
            sent = _written_json(t)[-1]
            rid = sent["request_id"]
            resp = {"type": "control_response",
                    "response": {"subtype": "success", "request_id": rid, "response": {}}}
            t._stdout.feed_data((json.dumps(resp) + "\n").encode())  # type: ignore[attr-defined]

        await asyncio.gather(t.interrupt(), feed_response())
        sent = _written_json(t)
        assert any(m.get("request", {}).get("subtype") == "interrupt" for m in sent)
        t._reader_task.cancel()

    asyncio.run(go())


def test_inbound_can_use_tool_routed_to_handler():
    async def go():
        seen: dict = {}

        async def handler(req):
            seen["tool"] = req.get("tool_name")
            return {"behavior": "allow"}

        t = _attached_transport(permission_handler=handler)
        inbound = {
            "type": "control_request",
            "request_id": "cli_1",
            "request": {"subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": "ls"}},
        }
        t._stdout.feed_data((json.dumps(inbound) + "\n").encode())  # type: ignore[attr-defined]
        await asyncio.sleep(0.1)

        assert seen["tool"] == "Bash"
        out = _written_json(t)[-1]
        assert out["type"] == "control_response"
        assert out["response"]["request_id"] == "cli_1"
        assert out["response"]["response"] == {"behavior": "allow"}
        t._reader_task.cancel()

    asyncio.run(go())


def test_handler_deny_with_interrupt_passes_through():
    async def go():
        async def handler(req):
            return {"behavior": "deny", "message": "user denied", "interrupt": True}

        t = _attached_transport(permission_handler=handler)
        inbound = {"type": "control_request", "request_id": "cli_2",
                   "request": {"subtype": "can_use_tool", "tool_name": "Edit", "input": {}}}
        t._stdout.feed_data((json.dumps(inbound) + "\n").encode())  # type: ignore[attr-defined]
        await asyncio.sleep(0.1)
        out = _written_json(t)[-1]
        assert out["response"]["response"]["behavior"] == "deny"
        assert out["response"]["response"]["interrupt"] is True
        t._reader_task.cancel()

    asyncio.run(go())


def test_stream_frames_queued_until_result():
    async def go():
        t = _attached_transport()
        frames = [
            {"type": "system", "subtype": "init", "session_id": "s1"},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}},
            {"type": "result", "result": "hi"},
        ]
        t._stdout.feed_data(("".join(json.dumps(f) + "\n" for f in frames)).encode())  # type: ignore[attr-defined]
        seen = []
        async for fr in t.events():
            seen.append(fr)
        assert [f["type"] for f in seen] == ["system", "assistant", "result"]
        t._reader_task.cancel()

    asyncio.run(go())
