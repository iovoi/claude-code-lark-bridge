"""Per-chat policy for the watchdog's working-status digests: update-in-place
vs. send-a-new-message.

The bridge sends a "🟢 Working …" progress digest every ~60s while Claude is on a
task. To keep the chat tidy, successive digests within ONE task should UPDATE the
previous digest message instead of stacking new ones. But we must never overwrite
anything other than our own most-recent digest — specifically, once the user sends
a new message (or the bot posts its final reply, or a stuck alert lands), the next
digest must be a fresh message.

This module is PURE LOGIC + in-memory state (no Feishu, no I/O), so it is fully
unit-testable offline. The MCP server (`mcp_channel/server.py`) owns the single
instance and calls these methods from its message paths:

  on_inbound(chat_id)   a new user message arrived -> next digest is new
  on_reply(chat_id)     the bot posted its final reply (task done) -> next is new
  on_stuck(chat_id)     a stuck alert was sent -> next digest is new
  plan(chat_id)         -> ("update", mid) if we hold a live digest for this chat
                              that is still the bot's last word (update it in place),
                           ("send", None)   otherwise (post a new message)
  remember(chat_id, mid) after a successful NEW digest send, record its id
  drop(chat_id)          update failed / message gone -> next is new

Guarantee delivered (without any chat-history read scope): the held message-id is
cleared on every inbound user message, so "update" is only ever returned while no
user message has arrived since the digest was posted — i.e. the digest is still the
bot's last word and sits after the user's last message.
"""
from __future__ import annotations


class DigestTracker:
    def __init__(self) -> None:
        self._mids: dict[str, str] = {}   # chat_id -> last working-digest message_id

    def on_inbound(self, chat_id: str) -> None:
        self._mids.pop(chat_id, None)

    def on_reply(self, chat_id: str) -> None:
        self._mids.pop(chat_id, None)

    def on_stuck(self, chat_id: str) -> None:
        self._mids.pop(chat_id, None)

    def remember(self, chat_id: str, message_id: str) -> None:
        self._mids[chat_id] = message_id

    def drop(self, chat_id: str) -> None:
        self._mids.pop(chat_id, None)

    def plan(self, chat_id: str) -> tuple[str, str | None]:
        """Decide how to deliver the next working digest for chat_id.

        Returns ("update", <message_id>) to edit the held digest in place, or
        ("send", None) to post a new message."""
        mid = self._mids.get(chat_id)
        if mid:
            return ("update", mid)
        return ("send", None)
