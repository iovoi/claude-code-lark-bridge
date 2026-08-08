"""Bridge runtime: wires ingest -> scopes -> agent, all on one asyncio loop.

The websocket (in a daemon thread) calls :meth:`on_message` / :meth:`on_card_action`;
these are thread-safe trampolines that schedule the real work onto the loop. The loop
owns all async state (scopes, semaphore, approval futures). ``run`` is the foreground
entry used by the supervisor (``feishu-bridge run``).
"""
from __future__ import annotations

import asyncio
import sys
import time
from typing import Awaitable, Callable, Optional

from . import access, ingest, session_store  # noqa: F401 (session_store used by scope)
from .approvals import ApprovalManager
from .config import BridgeConfig
from .ingest import is_stale
from .lark import Lark
from .scope import ScopeRunner


class Runtime:
    def __init__(self, cfg: BridgeConfig) -> None:
        self.cfg = cfg
        self.lark = Lark(cfg)
        self.approvals = ApprovalManager(self.lark, cfg)
        self.scopes: dict[str, ScopeRunner] = {}
        self._sem = asyncio.Semaphore(cfg.max_concurrent)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.boot_time = time.time()

    # ---- thread-safe trampolines (called from the websocket thread) -----------

    def on_message(self, evt: dict) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._schedule, self._handle_message, evt)

    def on_card_action(self, action: dict) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._schedule, self._handle_card_action, action)

    def _schedule(self, coro_fn: Callable[[dict], Awaitable[None]], arg: dict) -> None:
        assert self._loop is not None
        asyncio.create_task(coro_fn(arg))

    # ---- handlers (on the loop) ----------------------------------------------

    async def _handle_message(self, evt: dict) -> None:
        if is_stale(evt.get("create_time"), self.boot_time):
            return
        if not access.allowed(evt.get("open_id"), evt.get("chat_id")):
            return
        text = (evt.get("text") or "").strip()
        scope = self._scope_of(evt)
        chat_id = evt.get("chat_id") or ""
        runner = self.scopes.get(scope)
        if runner is None:
            runner = ScopeRunner(scope, chat_id, self.cfg, self.lark, self.approvals)
            self.scopes[scope] = runner

        if text == "/stop":
            ok = await runner.request_stop()
            if not ok:
                self.lark.send_text(chat_id, "(nothing to stop)")
            return

        async with self._sem:
            await runner.handle_message(evt)

    async def _handle_card_action(self, action: dict) -> None:
        val = action.get("value") or {}
        if not isinstance(val, dict):
            return
        verb = val.get("v")
        token = val.get("t")
        scope = val.get("s")
        if verb == "stop":
            runner = self.scopes.get(scope) if scope else None
            if runner is not None:
                await runner.request_stop()
        elif verb in ("approve", "deny", "deny_stop") and token:
            self.approvals.resolve(token, verb)

    @staticmethod
    def _scope_of(evt: dict) -> str:
        cid = evt.get("chat_id") or "unknown"
        tid = evt.get("thread_id")
        return f"{cid}:{tid}" if tid else cid

    # ---- lifecycle -----------------------------------------------------------

    async def _main(self, no_ws: bool) -> None:
        self._loop = asyncio.get_running_loop()
        if not no_ws:
            print("[runtime] starting Feishu websocket…", file=sys.stderr)
            ingest.start_ws(self.on_message, self.on_card_action)
        else:
            print("[runtime] --no-ws: websocket not started", file=sys.stderr)
        await asyncio.Event().wait()  # run forever

    def run(self, *, no_ws: bool = False) -> int:
        try:
            asyncio.run(self._main(no_ws))
        except KeyboardInterrupt:
            pass
        return 0


def run_forever(*, no_ws: bool = False) -> int:
    """Console entry: load config, build a Runtime, run it."""
    return Runtime(BridgeConfig.load()).run(no_ws=no_ws)
