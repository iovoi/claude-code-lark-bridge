"""Feishu/Lark websocket long-connection receiver (+ stale-message guard).

`start_ws(on_message)` runs `lark.ws.Client` in a daemon thread; each inbound
P2ImMessageReceiveV1 event is flattened to a dict and handed to `on_message`,
which (in server.py) forwards it onto the MCP loop's asyncio.Queue via
`loop.call_soon_threadsafe`. Stale messages (sent before this process started)
are dropped by the caller using `is_stale`.

Imports: lark_oapi is imported INSIDE the thread's run(), not at module top,
so importing this module (and thus starting the MCP server) is fast — the slow
lark import happens in the background thread, after the MCP `initialize`
handshake has already completed.
"""
from __future__ import annotations
import json
import sys
import threading
from datetime import datetime, timezone

import feishu_api as api  # APP_ID/APP_SECRET only; does NOT import lark at top


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_stale(create_time, boot_time: float) -> bool:
    """True if the message was created before boot (sent while the channel was down).
    Feishu's create_time may be seconds or milliseconds — normalize both."""
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
    """Best-effort PLAIN TEXT from a Feishu/Lark message body.

    Handles:
      * "text"  -> the text field
      * "post"  -> rich-text: {<locale>: {title, content: [[{tag,...}, ...], ...]}}
                   flattened to title + one line per content row, with text/a/
                   mention/at node text joined. Image/media nodes contribute no
                   text (an image-only post returns "" so the caller can say so).

    Other types (image, file, interactive card, ...) return "" — the caller falls
    back to a "[<type> message]" placeholder. Pure and dependency-free so it is
    unit-testable without lark_oapi."""
    if not isinstance(content, dict):
        return ""
    if message_type == "text":
        return str(content.get("text", "") or "")
    if message_type == "post":
        return _post_to_text(content)
    return ""


# Preferred locale keys in order; Feishu wraps the body as {"zh_cn": {...}} etc.
_POST_LOCALES = ("zh_cn", "en_us", "ja_jp", "ko_kr", "en", "zh")


def _post_locale(content: dict) -> dict:
    """Return the locale body dict of a post message, tolerating a missing locale
    wrapper (some clients send {title, content} directly)."""
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
    """Plain text for one rich-text node. Text/a carry .text; at/mention carry a
    user name; image/media/sticker carry none (they're not text)."""
    if not isinstance(node, dict):
        return ""
    tag = node.get("tag")
    if tag in ("text", "a"):
        return str(node.get("text", "") or "")
    if tag in ("at", "mention"):
        return str(node.get("name") or node.get("text") or "")
    # image / media / sticker / unknown: no extractable text
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


def start_ws(on_message) -> threading.Thread:
    """Start the Feishu websocket client in a daemon thread; return the thread.

    Returns immediately — the lark_oapi import + websocket connect happen inside
    the thread, so the caller (the MCP event loop) is never blocked by them."""
    def run() -> None:
        # Import lark INSIDE the thread so it doesn't block the MCP server startup.
        import lark_oapi as lark  # noqa: F401
        from lark_oapi.api.im.v1 import P2ImMessageReceiveV1  # noqa: F401 (type hint for handler)
        api.route_lark_logs_to_stderr()  # noqa: F401 (type hint for handler)

        def on_receive(data) -> None:
            print(f"[ws] on_receive FIRED: type={type(data).__name__}", file=sys.stderr)
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
                    "create_time": getattr(msg, "create_time", None),
                    "ts": _now_iso(),
                }
                on_message(evt)
            except Exception as e:
                print(f"[ws] handler error: {e}", file=sys.stderr)

        try:
            dispatcher = (
                lark.EventDispatcherHandler.builder("", "")
                .register_p2_im_message_receive_v1(on_receive)
                .build()
            )
            client = lark.ws.Client(
                api.APP_ID, api.APP_SECRET,
                event_handler=dispatcher,
                log_level=lark.LogLevel.INFO,
            )
            print("[ws] feishu websocket starting…", file=sys.stderr)
            client.start()  # blocks; lark reconnects internally on disconnect
        except Exception as e:
            print(f"[ws] thread exited: {e}", file=sys.stderr)

    t = threading.Thread(target=run, daemon=True, name="feishu-ws")
    t.start()
    return t
