"""Feishu/Lark websocket long-connection receiver (+ stale-message guard).

`start_ws(on_message)` runs `lark.ws.Client` in a daemon thread; each inbound
P2ImMessageReceiveV1 event is flattened to a dict and handed to `on_message`,
which (in server.py) forwards it onto the MCP loop's asyncio.Queue via
`loop.call_soon_threadsafe`. Stale messages (sent before this process started)
are dropped by the caller using `is_stale`.
"""
from __future__ import annotations
import json
import sys
import threading
from datetime import datetime, timezone

import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

import feishu_api as api


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


def start_ws(on_message) -> threading.Thread:
    """Start the Feishu websocket client in a daemon thread; return the thread."""
    def on_receive(data: P2ImMessageReceiveV1) -> None:
        try:
            msg = data.event.message
            sender = data.event.sender.sender_id.open_id
            try:
                content = json.loads(msg.content or "{}")
            except Exception:
                content = {}
            if msg.message_type == "text":
                text = content.get("text", "")
            else:
                text = ""  # non-text handled as "[<type> message]" by the caller
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

    def run() -> None:
        try:
            print("[ws] feishu websocket starting…", file=sys.stderr)
            client.start()  # blocks; lark reconnects internally on disconnect
        except Exception as e:
            print(f"[ws] thread exited: {e}", file=sys.stderr)

    t = threading.Thread(target=run, daemon=True, name="feishu-ws")
    t.start()
    return t
