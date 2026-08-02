"""Feishu/Lark MCP channel server.

An MCP server (stdio transport) that plugs into a running Claude Code session via
`claude --channels plugin:feishu`. It:

  * receives Feishu/Lark messages over the lark_oapi websocket long-connection
    (background daemon thread) and PUSHES each allowed message into the Claude
    session as a new user turn via the experimental
    `notifications/claude/channel` notification;
  * exposes `reply` / `react` tools that Claude calls to respond, which turn into
    Feishu REST API calls (reusing `feishu_api.py`).

Design notes (validated by the T0.1 spike, see docs/mcp-bridge/log.md):
  * We replicate `Server.run()`'s body so we can pass `experimental_capabilities`
    AND capture the `ServerSession` (needed to send notifications from the
    background drain task — `Server.run()` hides the session).
  * Inbound messages arrive on the ws thread; we hand them to the MCP asyncio loop
    via `loop.call_soon_threadsafe(queue.put_nowait, evt)`.
  * The drain task waits for the MCP `initialized` handshake (gated on the first
    incoming client message) before pushing, so nothing is sent pre-handshake.
"""
from __future__ import annotations
import asyncio
import os
import sys
import threading
import time
from contextlib import AsyncExitStack
from typing import Any, Literal

import anyio
from pydantic import BaseModel

from mcp.server.lowlevel.server import Server
from mcp.server.session import ServerSession
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

import feishu_api as api  # import first: its load_env() populates os.environ from .env
from . import access
from . import feishu_ingest
from . import bridgestate

BOOT_TIME = time.time()

# --- working/done emoji cycle ---
# chat_id -> (incoming_message_id, working_reaction_id). Bounded LRU.
_REACTION_LOCK = threading.Lock()
_REACTIONS: dict[str, tuple[str | None, str | None]] = {}
_REACTIONS_MAX = 256


def _stamp_working(chat_id, message_id) -> None:
    """On inbound message: stamp the working emoji and remember it so the reply
    tool can later flip it to done. Fail-soft (missing reaction_id is fine)."""
    if not (chat_id and message_id):
        return
    working_rid = None
    try:
        working_rid = api.add_reaction(message_id, api.EMOJI_WORKING)
    except Exception as e:
        print(f"[react] working stamp failed: {e}", file=sys.stderr)
    with _REACTION_LOCK:
        if len(_REACTIONS) >= _REACTIONS_MAX:
            _REACTIONS.pop(next(iter(_REACTIONS)))
        _REACTIONS[chat_id] = (message_id, working_rid)
    print(f"[react] working on {message_id} ({api.EMOJI_WORKING})", file=sys.stderr)


def _finish_working(chat_id) -> None:
    """On reply: stamp done on the originating message and clear the working emoji."""
    if not chat_id:
        return
    # Task is done -> tell the watchdog to stop monitoring it.
    try:
        bridgestate.clear_active()
    except Exception as e:
        print(f"[state] clear_active failed: {e}", file=sys.stderr)
    with _REACTION_LOCK:
        entry = _REACTIONS.pop(chat_id, None)
    if not entry:
        return
    message_id, working_rid = entry
    if message_id:
        try:
            api.add_reaction(message_id, api.EMOJI_DONE)
            print(f"[react] done on {message_id} ({api.EMOJI_DONE})", file=sys.stderr)
        except Exception as e:
            print(f"[react] done stamp failed: {e}", file=sys.stderr)
        if working_rid:
            try:
                api.delete_reaction(message_id, working_rid)
            except Exception:
                pass



# --- the custom channel notification (plain BaseModel; see log T0.1 caveat #3) ---
class ChannelParams(BaseModel):
    content: str
    meta: dict[str, Any] = {}


class ChannelNotification(BaseModel):
    method: Literal["notifications/claude/channel"] = "notifications/claude/channel"
    params: ChannelParams


app = Server("feishu")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="reply",
            description=(
                "Send your reply to the user in this Feishu/Lark chat. This is the "
                "ONLY way the user receives your response — any text you type in the "
                "conversation is NOT delivered to them. You MUST call this tool to "
                "reply, using the chat_id from the incoming message's meta. Call it "
                "exactly once per response, with your full answer as `text`."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["chat_id", "text"],
            },
        ),
        Tool(
            name="react",
            description="Add an emoji reaction to a Feishu/Lark message.",
            inputSchema={
                "type": "object",
                "properties": {
                    "message_id": {"type": "string"},
                    "emoji": {
                        "type": "string",
                        "description": "Feishu UPPER_SNAKE code (e.g. THUMBSUP) or a unicode emoji.",
                    },
                },
                "required": ["message_id", "emoji"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "reply":
        chat_id = arguments.get("chat_id")
        text = arguments.get("text", "")
        if not chat_id:
            return [TextContent(type="text", text="missing chat_id")]
        try:
            mid = api.send_text(chat_id, text)
            ok = mid is not None
        except Exception as e:
            ok = False
            print(f"[tool] reply raised: {e}", file=sys.stderr)
        print(f"[tool] reply chat={chat_id} -> {'sent ' + mid if ok else 'failed'}",
              file=sys.stderr)
        if ok:
            _finish_working(chat_id)
        return [TextContent(type="text", text="sent" if ok else "send failed")]
    if name == "react":
        message_id = arguments.get("message_id")
        emoji = arguments.get("emoji")
        if not (message_id and emoji):
            return [TextContent(type="text", text="missing message_id or emoji")]
        try:
            rid = api.add_reaction(message_id, emoji)
            ok = rid is not None
        except Exception as e:
            ok = False
            print(f"[tool] react raised: {e}", file=sys.stderr)
        print(f"[tool] react msg={message_id} {emoji} -> {'ok' if ok else 'failed'}",
              file=sys.stderr)
        return [TextContent(type="text", text="reacted" if ok else "failed")]
    return [TextContent(type="text", text=f"unknown tool: {name}")]


def _build_notification(evt: dict) -> ChannelNotification:
    content = evt.get("text") or f"[{evt.get('message_type', '?')} message]"
    meta = {
        "chat_id": evt.get("chat_id") or "",
        "message_id": evt.get("message_id") or "",
        "user": evt.get("open_id") or "",
        "user_id": evt.get("open_id") or "",
        "ts": evt.get("ts", ""),
        "chat_type": evt.get("chat_type") or "",
    }
    return ChannelNotification(params=ChannelParams(content=content, meta=meta))


async def _push(session: ServerSession, evt: dict) -> None:
    mid = evt.get("message_id")
    chat_id = evt.get("chat_id") or ""
    if feishu_ingest.is_stale(evt.get("create_time"), BOOT_TIME):
        print(f"[push] stale {mid} dropped", file=sys.stderr)
        return
    if not access.allowed(evt.get("open_id"), chat_id):
        print(f"[access] denied {mid} user={evt.get('open_id')}", file=sys.stderr)
        return
    # Keystroke forwarding: if the watchdog has the bridge in "awaiting keystroke"
    # mode (Claude is stuck on an interactive prompt), route the user's reply to
    # the keystroke queue instead of injecting it as a new Claude turn. The keeper
    # drains the queue and types it into the PTY.
    text = evt.get("text") or ""
    try:
        if bridgestate.is_awaiting_keystroke():
            bridgestate.push_keystroke(text)
            print(f"[push] intercepted as keystroke {mid} chat={chat_id} text={text[:60]!r}",
                  file=sys.stderr)
            return
    except Exception as e:
        print(f"[state] keystroke-intercept check failed: {e}", file=sys.stderr)
    notification = _build_notification(evt)
    _stamp_working(chat_id, mid)
    # Record the active task so the keeper's watchdog knows something is in flight.
    try:
        bridgestate.write_active(chat_id, mid or "", time.time(),
                                 content_preview=(notification.params.content or "")[:80])
    except Exception as e:
        print(f"[state] write_active failed: {e}", file=sys.stderr)
    await session.send_notification(notification)
    print(f"[push] {mid} chat={chat_id} content={notification.params.content[:60]!r}",
          file=sys.stderr)


async def main() -> None:
    if not api.APP_ID or not api.APP_SECRET:
        print("ERROR: set FEISHU_APP_ID and FEISHU_APP_SECRET (or put them in .env).",
              file=sys.stderr)
        sys.exit(1)

    init_opts = app.create_initialization_options(
        experimental_capabilities={"claude/channel": {}}
    )
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    initialized = asyncio.Event()

    def on_message(evt: dict) -> None:
        # called from the ws thread -> thread-safe handoff to the MCP loop
        loop.call_soon_threadsafe(queue.put_nowait, evt)

    if os.environ.get("FEISHU_DISABLE_WS") == "1":
        print("[boot] FEISHU_DISABLE_WS=1 -- websocket ingest DISABLED (offline/test mode)",
              file=sys.stderr)
    else:
        feishu_ingest.start_ws(on_message)
    print(f"[boot] feishu channel starting (boot={int(BOOT_TIME)}, "
          f"allowlist open_ids={len(access.ALLOWED_OPEN_IDS)} "
          f"chat_ids={len(access.ALLOWED_CHAT_IDS)})", file=sys.stderr)

    async def drain(session: ServerSession) -> None:
        await initialized.wait()
        while True:
            evt = await queue.get()
            try:
                await _push(session, evt)
            except Exception as e:
                print(f"[push] error: {e}", file=sys.stderr)

    async def outbox_drain() -> None:
        """Send watchdog messages (progress/stuck alerts) queued by the keeper.
        The keeper runs under the system python (no lark_oapi), so it can only
        write the outbox; this server (uvx env, has lark) does the actual send."""
        await initialized.wait()
        while True:
            try:
                for msg in bridgestate.drain_outbox():
                    try:
                        mid = await anyio.to_thread.run_sync(
                            api.send_text, msg["chat_id"], msg["text"])
                        ok = mid is not None
                    except Exception as e:
                        ok = False
                        print(f"[outbox] send failed: {e}", file=sys.stderr)
                    print(f"[outbox] {msg['chat_id']} -> {'sent' if ok else 'failed'}: "
                          f"{msg['text'][:50]!r}", file=sys.stderr)
            except Exception as e:
                print(f"[outbox] drain error: {e}", file=sys.stderr)
            await anyio.sleep(2.0)

    async with stdio_server() as (read_stream, write_stream):
        print("[handshake] stdio transport ready, waiting for client initialize…",
              file=sys.stderr)
        async with AsyncExitStack() as stack:
            session = await stack.enter_async_context(
                ServerSession(read_stream, write_stream, init_opts)
            )
            async with anyio.create_task_group() as tg:
                tg.start_soon(drain, session)
                tg.start_soon(outbox_drain)
                try:
                    async for message in session.incoming_messages:
                        if not initialized.is_set():
                            # First message routed here => the MCP initialize
                            # handshake completed at the transport/session layer.
                            initialized.set()
                            print(f"[handshake] complete (first msg: "
                                  f"{getattr(message, 'root', message).__class__.__name__})",
                                  file=sys.stderr)
                        tg.start_soon(app._handle_message, message, session, None, False)
                finally:
                    tg.cancel_scope.cancel()
