"""Feishu/Lark websocket long-connection receiver.

Extended from the old mcp_channel/feishu_ingest.py: now also (a) captures ``thread_id``
for topic-group scope isolation and (b) registers a ``card.action.trigger`` handler so
interactive-card button taps (approval Approve/Deny/Deny+stop, streaming-card Stop)
reach the runtime via ``on_card_action``.

``start_ws(on_message, on_card_action)`` runs ``lark.ws.Client`` in a daemon thread.
lark_oapi is imported INSIDE the thread so importing this module is fast.
"""
from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timezone
from typing import Callable, Optional

import feishu_api as api  # APP_ID/APP_SECRET only; does NOT import lark at top

OnMessage = Callable[[dict], None]
OnCardAction = Callable[[dict], None]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_stale(create_time, boot_time: float) -> bool:
    """True if the message was created before boot. Feishu's create_time may be s or ms."""
    ct = create_time
    if not ct:
        return False
    try:
        ct = int(ct)
    except (TypeError, ValueError):
        return False
    if ct > 1_000_000_000_000:  # milliseconds
        ct //= 1000
    return ct < int(boot_time)


def extract_text(message_type: str, content: dict) -> str:
    """Best-effort plain text from a Feishu message body (text / post). Dependency-free."""
    if not isinstance(content, dict):
        return ""
    if message_type == "text":
        return str(content.get("text", "") or "")
    if message_type == "post":
        return _post_to_text(content)
    return ""


_POST_LOCALES = ("zh_cn", "en_us", "ja_jp", "ko_kr", "en", "zh")


def _post_locale(content: dict) -> dict:
    for k in _POST_LOCALES:
        v = content.get(k)
        if isinstance(v, dict):
            return v
    if isinstance(content, dict) and ("content" in content or "title" in content):
        return content
    for v in content.values():
        if isinstance(v, dict):
            return v
    return {}


def _node_text(node: dict) -> str:
    if not isinstance(node, dict):
        return ""
    tag = node.get("tag")
    if tag in ("text", "a"):
        return str(node.get("text", "") or "")
    if tag in ("at", "mention"):
        return str(node.get("name") or node.get("text") or "")
    return ""


def _post_to_text(content: dict) -> str:
    locale = _post_locale(content)
    if not locale:
        return ""
    parts: list[str] = []
    title = locale.get("title")
    if title:
        parts.append(str(title))
    rows = locale.get("content")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, list):
                continue
            line = "".join(_node_text(n) for n in row if isinstance(n, dict))
            if line:
                parts.append(line)
    return "\n".join(parts).strip()


def start_ws(
    on_message: OnMessage,
    on_card_action: Optional[OnCardAction] = None,
) -> threading.Thread:
    """Start the Feishu websocket client in a daemon thread; return the thread."""
    if feishu_api.cred("FEISHU_DISABLE_WS") == "1":
        print("[ws] FEISHU_DISABLE_WS=1 — websocket not started", file=sys.stderr)
        t = threading.Thread(target=lambda: None, daemon=True, name="feishu-ws-disabled")
        t.start()
        return t

    def run() -> None:
        import lark_oapi as lark
        from lark_oapi.api.im.v1 import P2ImMessageReceiveV1  # noqa: F401

        api.route_lark_logs_to_stderr()

        def on_receive(data) -> None:
            try:
                msg = data.event.message
                sender = data.event.sender.sender_id.open_id
                try:
                    content = json.loads(msg.content or "{}")
                except Exception:
                    content = {}
                text = extract_text(getattr(msg, "message_type", ""), content)
                evt = {
                    "message_id": msg.message_id,
                    "chat_id": getattr(msg, "chat_id", None),
                    "chat_type": getattr(msg, "chat_type", None),
                    "message_type": msg.message_type,
                    "open_id": sender,
                    "text": text,
                    "thread_id": getattr(msg, "thread_id", None),
                    "create_time": getattr(msg, "create_time", None),
                    "ts": _now_iso(),
                }
                on_message(evt)
            except Exception as e:
                print(f"[ws] message handler error: {e}", file=sys.stderr)

        def on_card(data) -> None:
            try:
                ev = data.event
                action = getattr(ev, "action", None)
                value = getattr(action, "value", None) if action else None
                # value is a JSON string or dict depending on SDK version
                if isinstance(value, str):
                    try:
                        value = json.loads(value)
                    except Exception:
                        pass
                context = getattr(ev, "context", None)
                open_id = getattr(getattr(context, "open_id", None), "open_id", None) if context else None
                message_id = getattr(context, "open_message_id", None) if context else None
                on_card_action({
                    "value": value or {},
                    "message_id": message_id,
                    "open_id": open_id,
                    "ts": _now_iso(),
                })  # type: ignore[misc]
            except Exception as e:
                print(f"[ws] card-action handler error: {e}", file=sys.stderr)

        try:
            builder = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(on_receive)
            if on_card_action is not None:
                try:
                    builder = builder.register_p2_card_action_trigger_v1(on_card)
                except AttributeError:
                    print("[ws] card.action.trigger registration unavailable in this lark_oapi",
                          file=sys.stderr)
            dispatcher = builder.build()
            client = lark.ws.Client(
                api.APP_ID, api.APP_SECRET,
                event_handler=dispatcher,
                log_level=lark.LogLevel.INFO,
            )
            print("[ws] feishu websocket starting…", file=sys.stderr)
            client.start()  # blocks; lark reconnects internally
        except Exception as e:
            print(f"[ws] thread exited: {e}", file=sys.stderr)

    t = threading.Thread(target=run, daemon=True, name="feishu-ws")
    t.start()
    return t
