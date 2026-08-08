"""Thin wrapper over feishu_api for the bridge's Lark interactions.

All methods are fail-soft (return None/False on error) so a transient Feishu API
failure never crashes a turn. Reuses feishu_api.send_text/send_card/update_card/
add_reaction/delete_reaction verbatim.
"""
from __future__ import annotations

from typing import Optional

import feishu_api

from .cards import render_approval_card
from .config import BridgeConfig


class Lark:
    def __init__(self, cfg: BridgeConfig) -> None:
        self.cfg = cfg

    # --- emoji cycle --------------------------------------------------------

    def stamp_onit(self, message_id: str) -> Optional[str]:
        return feishu_api.add_reaction(message_id, self.cfg.emoji_working)

    def swap_to_done(self, message_id: str, onit_reaction_id: Optional[str]) -> Optional[str]:
        if onit_reaction_id:
            feishu_api.delete_reaction(message_id, onit_reaction_id)
        return feishu_api.add_reaction(message_id, self.cfg.emoji_done)

    # --- messaging ----------------------------------------------------------

    def send_text(self, chat_id: str, text: str) -> Optional[str]:
        return feishu_api.send_text(chat_id, text)

    def send_card(self, chat_id: str, card: dict) -> Optional[str]:
        return feishu_api.send_card(chat_id, card)

    def update_card(self, message_id: str, card: dict) -> bool:
        return feishu_api.update_card(message_id, card)

    def send_approval_card(
        self, chat_id: str, *, tool: str, summary: str, context: str, token: str, scope: str
    ) -> Optional[str]:
        return self.send_card(
            chat_id,
            render_approval_card(tool=tool, summary=summary, context=context, token=token, scope=scope),
        )
