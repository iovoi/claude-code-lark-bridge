"""Offline unit tests for the working-digest update-vs-send policy
(mcp_channel/digest_tracker.py).

Pure logic, no Feishu / no I/O. Asserts the guarantee: a digest is only updated
in place while it remains the bot's last word; any inbound user message, final
reply, stuck alert, or edit failure forces the next digest to be a new message.

Run:  python3 tests/test_digest_tracker.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_channel.digest_tracker import DigestTracker

CHAT = "oc_test"


def _check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ok:", msg)


def test_first_digest_is_new() -> None:
    print("[test] first digest for a chat is a new send")
    t = DigestTracker()
    decision, target = t.plan(CHAT)
    _check(decision == "send", "first plan -> send")
    _check(target is None, "first plan -> no target")


def test_remember_then_update() -> None:
    print("[test] after remembering a send, next plan updates it in place")
    t = DigestTracker()
    t.remember(CHAT, "om_1")
    decision, target = t.plan(CHAT)
    _check(decision == "update", "second plan -> update")
    _check(target == "om_1", "update targets the remembered id")


def test_inbound_clears() -> None:
    print("[test] a new user message forces the next digest to be new")
    t = DigestTracker()
    t.remember(CHAT, "om_1")
    _check(t.plan(CHAT)[0] == "update", "before inbound -> update")
    t.on_inbound(CHAT)
    decision, target = t.plan(CHAT)
    _check(decision == "send", "after inbound -> send")
    _check(target is None, "after inbound -> no target")


def test_reply_clears() -> None:
    print("[test] the final reply forces the next digest to be new")
    t = DigestTracker()
    t.remember(CHAT, "om_1")
    t.on_reply(CHAT)
    _check(t.plan(CHAT) == ("send", None), "after reply -> send")


def test_stuck_clears() -> None:
    print("[test] a stuck alert forces the next digest to be new")
    t = DigestTracker()
    t.remember(CHAT, "om_1")
    t.on_stuck(CHAT)
    _check(t.plan(CHAT) == ("send", None), "after stuck -> send")


def test_drop_clears() -> None:
    print("[test] an edit failure (drop) forces the next digest to be new")
    t = DigestTracker()
    t.remember(CHAT, "om_1")
    t.drop(CHAT)
    _check(t.plan(CHAT) == ("send", None), "after drop -> send")


def test_independent_chats() -> None:
    print("[test] chats are tracked independently")
    t = DigestTracker()
    t.remember("oc_a", "om_a")
    t.on_inbound("oc_b")  # inbound in B must not affect A
    _check(t.plan("oc_a") == ("update", "om_a"), "A unaffected by B's inbound")
    _check(t.plan("oc_b") == ("send", None), "B has no digest")


def test_full_task_sequence() -> None:
    print("[test] full sequence: new task -> send, update, update, reply -> send")
    t = DigestTracker()
    # task 1: first digest is new
    _check(t.plan(CHAT) == ("send", None), "task1 digest#1 -> send")
    t.remember(CHAT, "om_d1")
    # subsequent digests within task 1 update in place
    _check(t.plan(CHAT) == ("update", "om_d1"), "task1 digest#2 -> update om_d1")
    _check(t.plan(CHAT) == ("update", "om_d1"), "task1 digest#3 -> update om_d1")
    # task completes
    t.on_reply(CHAT)
    # task 2: a new user message -> first digest is new again, new id
    t.on_inbound(CHAT)
    _check(t.plan(CHAT) == ("send", None), "task2 digest#1 -> send")
    t.remember(CHAT, "om_d2")
    _check(t.plan(CHAT) == ("update", "om_d2"), "task2 digest#2 -> update om_d2")


def main() -> None:
    for fn in (
        test_first_digest_is_new,
        test_remember_then_update,
        test_inbound_clears,
        test_reply_clears,
        test_stuck_clears,
        test_drop_clears,
        test_independent_chats,
        test_full_task_sequence,
    ):
        fn()
    print("\nDIGEST TRACKER TESTS OK")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("FAILED:", e, file=sys.stderr); sys.exit(1)
