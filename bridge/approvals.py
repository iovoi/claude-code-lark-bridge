"""Tool-approval flow: post a 3-button Feishu card, await the user's tap (or timeout).

When Claude asks to use a tool off the allowlist, :meth:`ApprovalManager.request` posts
an approval card and blocks (awaiting a Future) until the runtime resolves it via
:meth:`resolve` (a card-action button tap) or the approval timeout auto-denies.

Verdicts: ``"allow"`` / ``"deny"`` / ``"deny_stop"`` (deny + interrupt the turn).
"""
from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass
from typing import Optional

from .cards import render_streaming_card
from .config import BridgeConfig
from .lark import Lark


@dataclass
class _Pending:
    future: asyncio.Future
    chat_id: str
    scope: str
    tool: str
    card_message_id: Optional[str] = None


def _summary(tool: str, inp: dict) -> str:
    """One-line, redaction-friendly summary of what the tool wants to do."""
    if tool in ("Bash", "BashOutput", "KillShell"):
        return str(inp.get("command") or inp)
    if tool in ("Edit", "Write", "NotebookEdit", "MultiEdit"):
        return str(inp.get("file_path") or inp)
    if tool == "WebFetch":
        return str(inp.get("url") or inp)
    if tool in ("WebSearch",):
        return str(inp.get("query") or inp)
    s = repr(inp)
    return s[:500]


class ApprovalManager:
    def __init__(self, lark: Lark, cfg: BridgeConfig) -> None:
        self._lark = lark
        self._cfg = cfg
        self._pending: dict[str, _Pending] = {}

    @property
    def has_pending(self) -> bool:
        return bool(self._pending)

    def pending_for_scope(self, scope: str) -> Optional[_Pending]:
        for p in self._pending.values():
            if p.scope == scope:
                return p
        return None

    async def request(self, *, chat_id: str, scope: str, tool: str, inp: dict, context: str) -> str:
        """Post the card and block until resolved/timeout. Returns a verdict string."""
        token = uuid.uuid4().hex
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        summary = _summary(tool, inp)
        card_msg = self._lark.send_approval_card(
            chat_id, tool=tool, summary=summary, context=context, token=token, scope=scope
        )
        pending = _Pending(future=fut, chat_id=chat_id, scope=scope, tool=tool, card_message_id=card_msg)
        self._pending[token] = pending
        try:
            return await asyncio.wait_for(fut, timeout=self._cfg.approval_timeout)
        except asyncio.TimeoutError:
            self._mark_card(pending, "⏱ Approval timed out (auto-denied)", "grey")
            return "deny"
        finally:
            self._pending.pop(token, None)

    def resolve(self, token: str, verdict: str) -> bool:
        """Resolve a pending approval from a card-action tap. Returns True if it matched."""
        pending = self._pending.get(token)
        if pending is None or pending.future.done():
            return False
        v = verdict if verdict in ("allow", "deny", "deny_stop") else "deny"
        if v == "allow":
            self._mark_card(pending, f"✓ Approved — continuing ({pending.tool})", "green")
        elif v == "deny_stop":
            self._mark_card(pending, f"✕ Denied + stopping turn ({pending.tool})", "grey")
        else:
            self._mark_card(pending, f"✕ Denied ({pending.tool})", "grey")
        pending.future.set_result(v)
        return True

    def _mark_card(self, pending: _Pending, text: str, template: str) -> None:
        if not pending.card_message_id:
            return
        card = {
            "config": {"wide_screen_mode": True, "update_multi": True},
            "header": {"title": {"tag": "plain_text", "content": text}, "template": template},
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": f"Tool: `{pending.tool}`"}}],
        }
        try:
            self._lark.update_card(pending.card_message_id, card)
        except Exception:
            pass
