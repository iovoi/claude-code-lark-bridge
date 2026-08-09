"""Tests for the event mapper (Phase 3), card throttling (Phase 4), and ingest text (Phase 4)."""
from __future__ import annotations

from bridge.agent.claude_adapter import _map_frame
from bridge.cards import CardState, StreamingCard
from bridge.ingest import extract_text, is_stale


# ---- _map_frame (T3.2) --------------------------------------------------------

def test_map_system_init_captures_session_id():
    ref: dict = {}
    evts = _map_frame({"type": "system", "subtype": "init", "data": {"session_id": "S1", "cwd": "/r"}}, ref)
    assert ref["session_id"] == "S1"
    assert evts[0].__class__.__name__ == "SystemEvent"
    assert evts[0].session_id == "S1"


def test_map_assistant_blocks():
    ref: dict = {}
    evts = _map_frame({
        "type": "assistant",
        "message": {"content": [
            {"type": "text", "text": "hello"},
            {"type": "thinking", "thinking": "pondering"},
            {"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "ls"}},
        ]},
    }, ref)
    kinds = [type(e).__name__ for e in evts]
    assert kinds == ["TextEvent", "ThinkingEvent", "ToolUseEvent"]
    assert evts[2].name == "Bash"


def test_map_user_tool_result():
    ref: dict = {}
    evts = _map_frame({
        "type": "user",
        "message": {"content": [{"type": "tool_result", "tool_use_id": "tu1", "content": "ok", "is_error": False}]},
    }, ref)
    assert len(evts) == 1 and evts[0].output == "ok"


def test_map_result_emits_usage_and_done():
    ref: dict = {}
    evts = _map_frame({
        "type": "result", "session_id": "S1",
        "usage": {"input_tokens": 10, "output_tokens": 20},
        "total_cost_usd": 0.01,
    }, ref)
    kinds = [type(e).__name__ for e in evts]
    assert kinds == ["UsageEvent", "DoneEvent"]
    assert evts[0].cost_usd == 0.01
    assert evts[1].reason == "normal"


# ---- StreamingCard throttle (T4.3) -------------------------------------------


class _FakeLark:
    def __init__(self):
        self.updates = 0
        self.creates = 0

    def send_card(self, chat_id, card):
        self.creates += 1
        return "msg-1"

    def update_card(self, message_id, card):
        self.updates += 1
        return True


def test_streaming_card_throttle_coalesces():
    import asyncio

    async def go():
        fl = _FakeLark()
        sc = StreamingCard(fl, "oc_x", "oc_x", throttle_ms=1000)
        await sc.create(CardState(phase="working", scope="oc_x"))
        # Two rapid updates within the throttle window coalesce to zero extra sends.
        await sc.update(CardState(phase="working", status="t1", scope="oc_x"))
        await sc.update(CardState(phase="working", status="t2", scope="oc_x"))
        assert fl.updates == 0  # throttled
        # finalize forces a flush.
        await sc.finalize(CardState(phase="done", answer="done", scope="oc_x"))
        assert fl.updates == 1

    asyncio.run(go())


# ---- ingest text extraction (T4.1) -------------------------------------------

def test_extract_text_text_and_post():
    assert extract_text("text", {"text": "hello world"}) == "hello world"
    post = {"zh_cn": {"title": "T", "content": [[{"tag": "text", "text": "a"}], [{"tag": "at", "name": "@u"}]]}}
    assert extract_text("post", post) == "T\na\n@u"


def test_extract_text_other_types_blank():
    assert extract_text("image", {}) == ""


def test_is_stale_handles_ms_and_s():
    boot = 1_700_000_000  # ~2023, seconds
    assert is_stale(1_500_000_000, boot) is True            # seconds, before boot
    assert is_stale(1_500_000_000_000, boot) is True        # ms, before boot (->1.5e9 < boot)
    assert is_stale(1_800_000_000, boot) is False           # seconds, after boot
    assert is_stale(None, boot) is False
