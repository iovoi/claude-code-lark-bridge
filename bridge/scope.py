"""Per-scope turn orchestration.

A :class:`ScopeRunner` owns one chat's state: single-flight (reject a 2nd message with a
``/stop`` hint), the OnIt→Done emoji cycle, a streaming card, the lazy-started
:class:`ClaudeAdapter` (resumed by the stored session id), approval delegation, and a
stuck watchdog. The runtime creates one per scope.
"""
from __future__ import annotations

import sys
from typing import Any, Callable, Optional

from .agent import (
    AgentAdapter,
    DoneEvent,
    ErrorEvent,
    TextEvent,
    ThinkingEvent,
    ToolResultEvent,
    ToolUseEvent,
    UsageEvent,
)
from .agent.claude_adapter import ClaudeAdapter
from .approvals import ApprovalManager
from .cards import CardState, StreamingCard
from .config import BridgeConfig
from .lark import Lark
from . import session_store
from .watchdog import StuckWatchdog


class ScopeRunner:
    def __init__(
        self,
        scope: str,
        chat_id: str,
        cfg: BridgeConfig,
        lark: Lark,
        approvals: ApprovalManager,
        *,
        adapter_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.scope = scope
        self.chat_id = chat_id
        self.cfg = cfg
        self.lark = lark
        self.approvals = approvals
        self._adapter_factory = adapter_factory or self._default_adapter_factory

        self._busy = False
        self._stop_flag = False
        self._adapter: Optional[AgentAdapter] = None
        self._card: Optional[StreamingCard] = None
        self._state: Optional[CardState] = None
        self._watchdog: Optional[StuckWatchdog] = None

    def _default_adapter_factory(self) -> AgentAdapter:
        return ClaudeAdapter(
            self.cfg,
            resume=session_store.get_session_id(self.scope),
            approval_callback=self._approval_cb,
        )

    # ------------------------------------------------------------------ entry

    async def handle_message(self, evt: dict) -> None:
        if self._busy:
            await self._reject(evt)
            return
        self._busy = True
        try:
            await self._run_turn(evt)
        finally:
            self._busy = False

    async def request_stop(self) -> bool:
        """``/stop``: interrupt the active turn. Returns True if a turn was active."""
        if not self._busy:
            return False
        self._stop_flag = True
        if self._state is not None:
            self._state.status = "Stopping…"
        if self._card is not None and self._state is not None:
            await self._card.update(self._state)
        if self._adapter is not None:
            try:
                await self._adapter.interrupt()
            except Exception as e:  # never wedge a /stop
                print(f"[scope {self.scope}] interrupt error: {e!r}", file=sys.stderr)
        return True

    # ------------------------------------------------------------------ pieces

    async def _reject(self, evt: dict) -> None:
        # Stamp Done on the rejected message (no OnIt to remove) + hint /stop.
        self.lark.swap_to_done(evt.get("message_id", ""), None)
        self.lark.send_text(
            evt.get("chat_id", self.chat_id),
            "(still working on your last message — send /stop to cancel)",
        )

    async def _run_turn(self, evt: dict) -> None:
        message_id = evt.get("message_id", "")
        onit = self.lark.stamp_onit(message_id)
        prompt = (evt.get("text") or "").strip()

        self._state = CardState(prompt=prompt, phase="working", status="Starting…", scope=self.scope)
        self._card = StreamingCard(self.lark, evt.get("chat_id", self.chat_id), self.scope, self.cfg.card_throttle_ms)
        await self._card.create(self._state)

        if self._adapter is None:
            self._adapter = self._adapter_factory()
            await self._adapter.start()

        self._stop_flag = False
        wd = StuckWatchdog(
            self.cfg.stuck_timeout,
            self._on_stuck,
            is_approval_pending=lambda: self.approvals.has_pending,
        )
        wd.start()
        self._watchdog = wd
        result: dict = {}
        try:
            result = await self._adapter.run_turn(prompt, self._emit, on_frame=wd.bump)
        except Exception as e:
            self._state.phase = "error"
            self._state.status = f"error: {e}"
            await self._card.finalize(self._state)
            self.lark.swap_to_done(message_id, onit)
            return
        finally:
            wd.stop()
            self._watchdog = None
            if self._card is not None:
                self._card.flush_pending()

        if self._stop_flag:
            self._state.phase = "stopped"
            self._state.status = "Stopped"
        else:
            self._state.phase = "done"
        cost = result.get("cost_usd")
        if isinstance(cost, (int, float)):
            self._state.usage = f"💰 ${cost:.4f}"
        await self._card.finalize(self._state)
        self.lark.swap_to_done(message_id, onit)
        if self._adapter is not None and self._adapter.session_id:
            session_store.set_session_id(self.scope, self._adapter.session_id, str(self.cfg.workdir))

    async def _emit(self, event) -> None:
        if self._watchdog is not None:
            self._watchdog.bump()
        if self._state is None or self._card is None:
            return
        st = self._state
        if isinstance(event, TextEvent):
            st.answer += event.text
            st.status = "Writing…"
        elif isinstance(event, ThinkingEvent):
            st.status = "Thinking…"
        elif isinstance(event, ToolUseEvent):
            if event.name not in st.tools:
                st.tools.append(event.name)
            st.status = f"Using {event.name}"
        elif isinstance(event, ToolResultEvent):
            st.status = "Continuing…" if not event.is_error else "Tool error"
        elif isinstance(event, UsageEvent):
            pass
        elif isinstance(event, ErrorEvent):
            st.phase = "error"
            st.status = event.message
        elif isinstance(event, DoneEvent):
            pass
        await self._card.update(st)

    async def _approval_cb(self, tool: str, inp: dict) -> str:
        # Pause the turn on an approval card; resolved by a card-action tap or timeout.
        if self._state is not None:
            self._state.status = f"⏸ Waiting for approval: {tool}"
            await self._card.update(self._state) if self._card else None
        return await self.approvals.request(
            chat_id=self.chat_id,
            scope=self.scope,
            tool=tool,
            inp=inp,
            context=(self._state.prompt[:200] if self._state else ""),
        )

    async def _on_stuck(self) -> None:
        self._stop_flag = True
        if self._state is not None:
            self._state.status = "(no activity — stopping)"
            self._state.phase = "stopped"
            if self._card is not None:
                await self._card.update(self._state)
        if self._adapter is not None:
            try:
                await self._adapter.interrupt()
            except Exception:
                pass
