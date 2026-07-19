# PRD: mcp-bridge (Feishu/Lark MCP channel)

- **Status:** Live-verified (messages flow end-to-end via `claude --channels`); emoji-cycle enhancement added (working→done reactions)
- **Feature dir:** `docs/mcp-bridge/`
- **Created:** 2026-07-19 · **Last updated:** 2026-07-19 (emoji-cycle enhancement)

## 0. Resume protocol
If you are a new agent: read this `prd.md`, then `tasks.md`, then `log.md`, then
resume from the first unchecked task in `tasks.md`. This document is the source
of truth — do not re-derive the design.

## 1. Overview
Replace the existing tmux-scraping Feishu↔Claude Code bridge with a **Feishu
MCP channel**: an MCP server (Python, stdio transport) that plugs into a running
Claude Code session via `claude --channels plugin:feishu`. The channel receives
Feishu/Lark messages over the official `lark_oapi` websocket long-connection and
**pushes** each allowed message into the Claude session as a new user turn (via
the experimental `notifications/claude/channel` MCP notification). Claude replies
by calling the channel's `reply` / `react` tools, which the server turns into
Feishu REST API calls (reusing the existing `feishu_api.py`).

This deletes the entire tmux paste → capture-pane → marker-scrape → poll/timeout
machinery (and its bug class: empty replies, premature timeouts, capture races)
and puts Feishu on the same architecture Anthropic blesses for its official
Telegram/Discord/iMessage channels.

## 2. Goals and non-goals
- **Goals:**
  - A runnable Python MCP channel server that Feishu users can chat with through
    a Claude Code session started with `--channels`.
  - Inbound: Feishu message → (allowlist) → injected as a Claude user turn.
  - Outbound: Claude calls `reply(chat_id, text)` / `react(message_id, emoji)` →
    message/reaction appears in Feishu.
  - Reuse `feishu_api.py` (REST send/react, `lark_oapi` client, `.env` loading).
  - Static allowlist via `.env` (`FEISHU_ALLOWED_OPEN_IDS` / `FEISHU_ALLOWED_CHAT_IDS`).
  - Remove the obsolete v1 tmux/polling bridge files.
- **Non-goals (v1):**
  - Streaming/progress cards (no `edit_message` / live card updates — one `reply`
    per turn, like the current bridge).
  - Pairing-code access flow (the official channel's `/access pair` UX).
    Static allowlist only.
  - Permission relay (`claude/channel/permission`) — surfacing Claude's tool-
    approval prompts into Feishu chat.
  - Images / file attachments in either direction (text only for v1).
  - `fetch_messages` / `download_attachment` tools.
  - A systemd/launchd/Windows-Task service wrapper (always-on is achieved by
    keeping the `claude --channels` session running; documented, not packaged).

## 3. Acceptance criteria
Each is independently verifiable.
1. `pip install -r requirements.txt` into a clean venv succeeds (adds the `mcp`
   - **Result:** PASS — `mcp==1.28.1` installed; `python -m mcp_channel` runs (smoke spawns it).
   SDK) and `python -m mcp_channel --help` (or the chosen entry point) runs
   without import error.
2. With valid `FEISHU_APP_ID`/`FEISHU_APP_SECRET` in `.env`, starting the channel
   - **Result:** PASS — stdio smoke asserts `claude/channel` capability + `reply`/`react` tools; channel logs `[boot]` on stderr.
   logs a Feishu websocket "connected to wss://..." line AND completes the MCP
   `initialize` handshake over stdio (verified by a stdio smoke test or by
   Claude Code spawning it).
3. A Feishu DM from an allowlisted open_id, sent while a `claude --channels
   - **Result:** PENDING live — ws ingest (feishu_ingest.py) mirrors the proven v1 bot.py receive handler; structural correctness verified, end-to-end push unverified without a live `claude --channels` session.
   plugin:feishu` session is running, appears in that Claude session as an
   injected user turn (the model sees the message content).
4. When Claude calls the `reply` tool with `{chat_id, text}`, the text is
   - **Result:** PASS (dispatch) — smoke proves the `reply` tool calls `feishu_api.send_text`; live delivery to a real chat pending.
   delivered to that Feishu chat (verified in the Feishu client).
5. When Claude calls the `react` tool with `{message_id, emoji}`, the emoji
   - **Result:** PASS (dispatch) — `react` tool wired to `feishu_api.add_reaction`; live pending.
   reaction appears on the originating Feishu message.
6. A Feishu DM from an open_id **not** in the allowlist (when an allowlist is
   - **Result:** PASS (logic) — `access.allowed()` deny-path is covered; live drop pending.
   set) is dropped: no user turn is injected, and a `[access] denied` line is
   logged.
7. The v1 files (`bot.py`, `deliver_to_claude.py`, `bridgectl.py`,
   - **Result:** PASS — `git ls-files` shows none of the v1 files; `feishu_api.py` imports cleanly (160 lines).
   `mark_handled.py`, `pending_messages.py`, `react.py`, `send_message.py`) are
   deleted; `git ls-files` no longer lists them; `feishu_api.py` still imports
   cleanly.

8. Emoji cycle: on receipt the channel stamps the working emoji (`OnIt`) on the
   incoming message; when Claude's `reply` succeeds it stamps `Done` and removes
   `OnIt`. (Live-observed via `[react] working on …` / `[react] done on …` logs.)

## 4. Detailed specification

### 4.1 Inputs
- **Feishu credentials:** `FEISHU_APP_ID`, `FEISHU_APP_SECRET` in `.env` (loaded
  via `feishu_api.load_env`). Same custom-app credentials the v1 bridge used.
- **Inbound messages:** arrive via `lark_oapi` websocket
  (`P2ImMessageReceiveV1` events). Each carries `message.content` (JSON,
  `{"text": "..."}` for text type), `message.message_id`, `message.chat_id`,
  `message.chat_type` (`p2p`|`group`), `message.create_time`, and
  `event.sender.sender_id.open_id`.
- **MCP stdio:** the server is spawned by Claude Code as a child process; it
  speaks JSON-RPC 2.0 over stdin/stdout (the MCP protocol). Claude Code sends
  `initialize`, `tools/list`, `tools/call`.

### 4.2 Outputs
- **Injected user turns:** an MCP notification
  `notifications/claude/channel` with params:
  `{ "content": "<message text>", "meta": { "chat_id": "...", "message_id": "...",
  "user": "<sender name or open_id>", "user_id": "<open_id>", "ts": "<ISO8601>",
  "chat_type": "p2p|group" } }`.
- **Feishu sends:** via `feishu_api.send_text(chat_id, text)` and
  `feishu_api.add_reaction(message_id, emoji_code)`.
- **Logs:** human-readable lines on stderr: `[boot]`, `[ws] connected`,
  `[access] allowed|denied <message_id>`, `[push] <message_id>`,
  `[tool] reply <chat_id>`, `[tool] react <message_id>`.

### 4.3 Behavior (state machine)
1. **Boot:** load `.env`; build REST client (`feishu_api.client()`) and the
   `lark.ws.Client` websocket with a `P2ImMessageReceiveV1` handler registered.
2. **MCP initialize:** declare server capabilities incl. experimental
   `claude/channel`. Start the MCP stdio loop (main asyncio loop).
3. **Feishu websocket** runs in a daemon thread; on each inbound message the
   handler (thread) pushes the event onto a thread-safe `asyncio.Queue` owned by
   the MCP loop via `loop.call_soon_threadsafe(queue.put_nowait, event)`.
4. A draining task on the MCP loop pops events and for each:
   a. **Access check** (`access.allowed(open_id, chat_id)`). Deny → log + drop.
   b. **Stale guard:** drop messages with `create_time` < server start time
      (sent while the channel was down) — port the v1 `is_stale_message` logic.
   c. **Build + send** the `notifications/claude/channel` notification through
      the captured `ServerSession` (see 4.5). Log `[push]`.
5. **Outbound (tool calls):** Claude calls `reply`/`react`; the tool handlers
   invoke `feishu_api.send_text` / `feishu_api.add_reaction` and return a short
   confirmation string.

### 4.4 Data model
- `.env` keys (added): `FEISHU_ALLOWED_OPEN_IDS` (comma-sep open_ids),
  `FEISHU_ALLOWED_CHAT_IDS` (comma-sep chat_ids, optional).
- No persistent ledger required for v1 (the MCP session + Feishu itself hold
  state). The old `conversation/.delivered.json`, `.claude_busy.json`,
  `.reactions.json`, `.handled.json` ledgers are **removed** with the v1 code.

### 4.5 Interfaces (exact)
- **Entry point:** `python -m mcp_channel` → `mcp_channel/__main__.py` calls
  `mcp_channel.server.main()`.
- `mcp_channel/server.py`:
  - `def main() -> None` — build server, start websocket thread, run MCP loop.
  - Tools registered (FastMCP decorators or low-level handlers):
    - `reply(chat_id: str, text: str) -> str` — calls
      `feishu_api.send_text(chat_id, text)`; returns `"sent"` or `"send failed"`.
    - `react(message_id: str, emoji: str) -> str` — calls
      `feishu_api.add_reaction(message_id, emoji)`; returns `"reacted"` /
      `"failed"`. `emoji` is a Feishu UPPER_SNAKE code (e.g. `THUMBSUP`) or a
      unicode emoji.
  - Capability declaration: experimental `{"claude/channel": {}}`.
  - Notification send: capture the live `ServerSession` at init and call
    `session.send_notification("notifications/claude/channel", params)` from the
    drain task. (Exact SDK call validated by the T1 spike — see tasks.)
- `mcp_channel/feishu_ingest.py`:
  - `def start_ws(on_message: Callable[[dict], None]) -> threading.Thread` —
    builds `lark.ws.Client(APP_ID, APP_SECRET, event_handler=dispatcher)` where
    the dispatcher's receive handler extracts fields and calls `on_message(evt)`
    on the ws thread; returns a daemon `Thread` running `client.start()`.
  - `def is_stale(create_time, boot_time) -> bool` — ported from v1 bot.py.
- `mcp_channel/access.py`:
  - `def allowed(open_id: str|None, chat_id: str|None) -> bool` — reads the two
    env lists once (module load) into sets. If **both** lists are unset/empty →
    allow all but the server logs a prominent `[access] WARNING: no allowlist
    set — open to everyone`. If either list is set, require a match in the
    relevant list (open_id for p2p sender, chat_id for the chat).
- `.mcp.json` (project root):
  ```json
  { "mcpServers": { "feishu": { "command": ".venv/bin/python", "args": ["-m", "mcp_channel"] } } }
  ```
- `.claude-plugin/plugin.json`: minimal plugin manifest so the server can be
  loaded as a channel (name `feishu`, version, the `.mcp.json` reference). Exact
  schema validated by T2.

### 4.6 Error handling & edge cases
- **Allowlist unset:** allow-all + WARN (preserves v1 behavior; see 4.5).
- **Deny:** log `[access] denied <message_id> user=<open_id>`; no injection.
- **Stale message** (`create_time` < boot): log + drop (no injection).
- **Non-text message** (image/sticker/etc.): inject
  `content="[<message_type> message]"` (non-goal to fully support; do not crash).
- **`reply` send failure** (`feishu_api.send_text` returns None): tool returns
  `"send failed"` so Claude can react; server logs `[tool] reply failed`.
- **MCP session not yet ready** when a ws message arrives: the drain task awaits
  session readiness (an `asyncio.Event` set on the MCP `initialized` notification)
  before sending; messages received before init are queued, not dropped.
- **WebSocket disconnect:** `lark.ws.Client` reconnects internally; log on
  reconnect. If the ws thread dies, log `[ws] thread exited` (do not crash MCP).

### 4.7 Security & permissions
- `.env` is gitignored (already). Never log `APP_SECRET`.
- Access control is the only auth layer for v1: without an allowlist the bot
  answers anyone who can reach the Feishu app — document this loudly.
- The channel inherits whatever tool permissions the launching `claude` session
  has; the channel itself only exposes `reply`/`react` (no filesystem/exec tools).

## 4.8 Working → done emoji cycle
Ports the v1 bridge's reaction bookends into the channel (which is otherwise a
thin push+tools layer with no lifecycle of its own):
- **On receipt** (`_push`, after access/stale checks, before the notification):
  `_stamp_working(chat_id, message_id)` calls `feishu_api.add_reaction(
  message_id, EMOJI_WORKING)` and records `(message_id, working_reaction_id)`
  keyed by `chat_id` in a bounded LRU (`_REACTIONS`, cap 256, `_REACTION_LOCK`).
- **On successful `reply`** (`call_tool` for `reply`, when `ok`):
  `_finish_working(chat_id)` pops the entry, calls `add_reaction(message_id,
  EMOJI_DONE)`, then `delete_reaction(message_id, working_reaction_id)`.
- **Mapping note:** `reply` takes a `chat_id`, but the emoji belong on the
  *incoming* `message_id`; the `_REACTIONS` map supplies it. One entry per chat
  (latest incoming wins) — exact for one-message-per-chat-at-a-time.
- **Config:** `FEISHU_EMOJI_WORKING` (default `OnIt`), `FEISHU_EMOJI_DONE`
  (default `Done`) in `feishu_api.py`.
- **Fail-soft:** a failed working stamp leaves `working_reaction_id=None`; done
  still stamps, removal is skipped. Neither breaks the reply.

## 5. Architecture and file layout
**New:**
- `mcp_channel/__init__.py` — package marker.
- `mcp_channel/__main__.py` — `from .server import main; main()`.
- `mcp_channel/server.py` — MCP server: capabilities, tools, ws wiring, drain loop.
- `mcp_channel/feishu_ingest.py` — `lark.ws.Client` wrapper + stale guard.
- `mcp_channel/access.py` — allowlist.
- `.mcp.json` — registers the `feishu` MCP server with Claude Code.
- `.claude-plugin/plugin.json` — channel plugin manifest.
**Modified:**
- `requirements.txt` — add `mcp` SDK (version pinned after T1 spike).
- `.env.example` — document `FEISHU_ALLOWED_OPEN_IDS` / `FEISHU_ALLOWED_CHAT_IDS`;
  remove `FEISHU_CLAUDE_PANE` (tmux) and the emoji keys (now tool-driven).
- `README.md` — replace tmux-bridge instructions with the channel run steps.
- `feishu_api.py` — remove dead tmux-only helpers (`claude_is_busy`, `clean_line`,
  `_BORDER_CHARS`, `CLAUDE_PANE`, `VENV_PYTHON`, `HELP_TEXT`, `EMOJI_*`,
  `STALE_SEC`); keep `client`, `send_text`, `add_reaction`, `delete_reaction`,
  `load_env`, config (`APP_ID`/`APP_SECRET`), ledger helpers only if reused.
**Removed (v1 tmux/polling bridge):**
- `bot.py`, `deliver_to_claude.py`, `bridgectl.py`, `mark_handled.py`,
  `pending_messages.py`, `react.py`, `send_message.py`.
- `conversation/.claude_busy.json`, `.delivered.json`, `.reactions.json`,
  `.handled.json` ledgers (runtime artifacts; stop creating them).

## 6. Dependencies
- **Add:** `mcp` (official Model Context Protocol Python SDK,
  `pip install mcp`; pin exact version after the T1 spike confirms the API).
- **Keep:** `lark-oapi==1.7.1` (websocket + REST), `requests`, `websockets`.
- **Python:** 3.10+ (the `mcp` SDK requirement); the project venv is 3.14.
- **Runtime:** Claude Code must be run with `--channels plugin:feishu` (research-
  preview feature; requires Anthropic auth via claude.ai or a Console API key).
- **Host convention:** long-running commands (installs, the channel process when
  tested standalone) go through a tmux pane per the workspace CLAUDE.md.

## 7. Testing strategy
- **AC1:** `python -m venv /tmp/mb_venv && /tmp/mb_venv/bin/pip install -r
  requirements.txt && /tmp/mb_venv/bin/python -m mcp_channel --help` → exit 0.
- **AC2 (stdio handshake):** drive the server over stdio with a tiny
  JSON-RPC `initialize` client (a `tests/stdio_smoke.py` script) and assert an
  `initialize` result containing the `claude/channel` capability; assert the ws
  thread logs `connected to wss://`.
- **AC3–6 (live):** manual via a real Feishu app + a `claude --channels` session;
  record observed results in `log.md`. (Automated live tests are a non-goal; the
  Feishu app + Claude session make them impractical to unit-test.)
- **AC7:** `git ls-files | grep -E 'bot\.py|deliver_to_claude|bridgectl|
  mark_handled|pending_messages|^react\.py|send_message'` → no matches;
  `python -c "import feishu_api"` → OK.

## 8. Open questions
- **OQ1 (T1 spike):** does the current `mcp` Python SDK let a server (a) declare
  an experimental capability in its `initialize` result, and (b) send an
  arbitrary notification method (`notifications/claude/channel`) to the client
  from outside a tool handler (background task)? Owned by: implementer. Resolved
  during T1 → recorded in Appendix A + log.md.
- **OQ2 (T2):** exact local-install/registration mechanism for a custom channel
  plugin so `claude --channels plugin:feishu` finds it (`.claude-plugin/
  plugin.json` + `.mcp.json` shape, or a settings entry). Owned by: implementer.

## Appendix A — Decision log
| # | Decision | Options considered | Chosen | Rationale | Date |
|---|---|---|---|---|---|
| D1 | Architecture | (a) MCP channel push #5 (b) pull-only tools MCP (c) both | (a) | future-proof, Anthropic-blessed, deletes the scraping bug class | 2026-07-19 |
| D2 | Language | (a) Python reuse feishu_api (b) TS/Bun mirror official | (a) | node/bun not installed; reuses lark_oapi + feishu_api | 2026-07-19 |
| D3 | v1 bridge fate | (a) coexist (b) replace | (b) | user choice; cleaner end state, removes dead tmux code | 2026-07-19 |
| D4 | Access control | (a) static .env allowlist (b) pairing+allowlist (c) none | (a) | simplest, fits .env style; pairing is v1 non-goal | 2026-07-19 |
| D5 | v1 tools exposed | reply + react only (no edit_message/streaming) | reply + react | matches one-reply-per-turn v1 behavior; streaming card is non-goal | 2026-07-19 |
| D6 | Working→done emoji | (a) auto in channel (_push/reply) (b) let Claude call react manually | (a) | faithful to v1 bridge; not dependent on Claude remembering to react | 2026-07-19 |

## Appendix B — Glossary
- **MCP** — Model Context Protocol; JSON-RPC 2.0 over stdio between an MCP client
  (Claude Code) and an MCP server (this channel).
- **Channel** — an MCP server that additionally declares the experimental
  `claude/channel` capability and pushes inbound chat via the
  `notifications/claude/channel` notification (injects a user turn).
- **`lark.ws.Client`** — Feishu/Lark websocket long-connection client (inbound).
- **open_id / chat_id** — Feishu user / conversation identifiers.
