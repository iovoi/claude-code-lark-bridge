"""Regression tests for ApprovalManager verdict normalization.

Guards against the bug where a card-button tap carried v='approve' but _apply only
accepted 'allow', silently turning every Approve into a deny.
"""
from __future__ import annotations

import asyncio

from bridge.approvals import ApprovalManager, _Pending
from bridge.config import BridgeConfig
from bridge.lark import Lark


def _pending(mgr, scope="s1", token="tok"):
    fut = asyncio.get_event_loop().create_future()
    p = _Pending(future=fut, chat_id="c", scope=scope, tool="Bash")
    mgr._pending[token] = p
    return p, fut


def test_approve_button_verb_maps_to_allow():
    async def go():
        mgr = ApprovalManager(Lark(BridgeConfig.load()), BridgeConfig.load())
        p, fut = _pending(mgr)
        assert mgr.resolve("tok", "approve") is True   # card-button verb
        assert fut.result() == "allow"                  # not "deny"

    asyncio.run(go())


def test_allow_deny_deny_stop_pass_through():
    async def go():
        mgr = ApprovalManager(Lark(BridgeConfig.load()), BridgeConfig.load())
        for verb, expected in [("allow", "allow"), ("deny", "deny"), ("deny_stop", "deny_stop")]:
            p, fut = _pending(mgr, token=verb)
            assert mgr.resolve(verb, verb) is True
            assert fut.result() == expected

    asyncio.run(go())


def test_resolve_for_scope_finds_pending():
    async def go():
        mgr = ApprovalManager(Lark(BridgeConfig.load()), BridgeConfig.load())
        p, fut = _pending(mgr, scope="oc_x", token="t1")
        assert mgr.resolve_for_scope("oc_x", "approve") is True
        assert fut.result() == "allow"
        assert mgr.resolve_for_scope("oc_x", "allow") is False  # already resolved

    asyncio.run(go())
