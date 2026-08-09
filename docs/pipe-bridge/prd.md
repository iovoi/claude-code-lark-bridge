# PRD: Pipe Bridge — unified streaming print-mode Feishu/Lark ↔ Claude Code bridge

- **Status:** Complete
- **Feature dir:** `docs/pipe-bridge/`
- **Created:** 2026-08-08 · **Last updated:** 2026-08-10

## 0. Resume protocol
If you are a new agent: read this `prd.md`, then `tasks.md`, then `log.md`, then resume from the
first unchecked task in `tasks.md`. This document is the source of truth for *what* and *why* — do
not re-derive the design. The hand-rolled control protocol is the riskiest piece; see §4.5 and the
open questions before touching `bridge/control.py`.

## 1. Overview
Replace the current interactive-TUI + PTY + MCP-channel-server bridge (`mcp_channel/`,
`mcp_channel/launcher.py` PTY keeper) with a single, unified bridge that drives Claude Code in
**non-interactive streaming mode**:

```
claude -p --input-format stream-json --output-format stream-json --verbose \
  [--session-id <uuid> | --resume <id>] [--permission-prompt-tool stdio] [mode flags]
```

One claude process is kept alive per scope (chat); the bridge writes NDJSON user-turn lines to its
stdin and reads NDJSON events from its stdout. The bridge **hand-rolls the bidirectional control
protocol** (initialize handshake + `control_request`/`control_response` correlation) so it can (a)
show **Lark approval cards** when Claude wants to run a tool off the allowlist, and (b) gracefully
**interrupt** a turn for `/stop`. All Lark interaction — reactions (`OnIt`→`Done`), streaming cards,
approval cards, replies — is owned by the bridge. No PTY, no tmux, no MCP channel server, identical
behavior on Windows / Mac / Linux. The agent layer sits behind an `AgentAdapter` interface so a
non-Claude (or SDK-backed) adapter can slot in later.

## 2. Goals and non-goals
**Goals**
- Identical UX and commands on Windows, Mac, Linux with **no PTY, no tmux**.
- Emoji cycle: stamp `OnIt` on arrival → swap to `Done` when the turn finishes.
- Conversation history + memory preserved: one long-lived claude session per scope; `--resume`
  continuity; CLAUDE.md + auto-memory load (we do **not** pass `--bare`).
- **Lark approval cards** for tools off the allowlist, via the hand-rolled control protocol.
- **Live streaming card** delivery (card created at turn start, updated as Claude works, finalized).
- **`/stop`** command to cancel the in-flight turn (graceful in-band interrupt; SIGTERM fallback).
- Single-flight per scope: a second message during work is rejected with a `/stop` hint.
- Pluggable agent layer (`AgentAdapter`); Claude implemented now.
- Reuse existing Lark primitives (`feishu_api.py`), inbound websocket (`feishu_ingest`), allowlist
  (`access`).

**Non-goals (v1)**
- OS service-manager supervision (systemd / launchd / schtasks) — detached spawn only.
- Inbound images / files / audio — text and rich-text (`post`) only; other types → placeholder.
- Multi-agent backends (Codex, etc.) — interface pluggable but only `ClaudeAdapter` shipped.
- QR onboarding / encrypted per-profile secrets — keep the existing `.env` credential flow.
- Topic-group deep UX — `thread_id` is folded into scope identity but no special thread rendering.
- Migrating old on-disk MCP session state — fresh session ids on first run.

## 3. Acceptance criteria
Each is independently verifiable.

1. **Cross-platform lifecycle:** on Windows, Mac, and Linux, `feishu-bridge up` starts the bridge as
   a detached background process (no PTY, no tmux); `feishu-bridge status` reports running + pid +
   session; `feishu-bridge stop` stops it cleanly. Same commands and output shape on all three.
2. **Emoji cycle + answer:** an allowed sender's message in a DM/group → bot stamps the `OnIt`
   reaction (within ~2 s) → Claude works → bot posts the final answer → `OnIt` is removed and `Done`
   is stamped on the user's original message.
3. **Conversation continuity:** a follow-up message in the same chat, sent after the first turn
   finished, is answered with memory of the first turn (same claude session id; no re-explanation).
4. **Single-flight reject:** a second message arriving while Claude is working in the same chat
   receives an auto-reply `"(still working on your last message — send /stop to cancel)"` plus a
   `Done` reaction, and its content is **dropped** (not executed).
5. **`/stop`:** sending `/stop` in the same chat during an active turn cancels it (graceful interrupt
   via the control protocol; SIGTERM+respawn fallback), and the bot posts `"(stopped)"` + `Done`.
6. **Approval cards:** when Claude attempts a tool **not** on the auto-approve allowlist, the bot
   posts an interactive card `Approve <tool>? <summary of input>` with **four** buttons —
   **Approve** / **Approve all (turn)** / **Deny** / **Deny + stop**. The turn pauses until a
   button is tapped or a reply is received (else auto-deny on timeout). **Approve** → Claude
   continues; **Approve all (turn)** → allows this tool AND auto-allows every subsequent tool in
   the same turn (resets next turn); **Deny** → tool denied, Claude continues; **Deny + stop** →
   tool denied + the whole turn is interrupted. The same verdicts work by **reply** in chat
   (`approve` / `all` / `deny` / `stop`, or y/n/1/2/3). On resolution the card re-renders to keep
   only the clicked button. Allowlisted tools run with no card.
7. **Progress card + result:** if a turn runs longer than `FEISHU_CARD_DEFER_SEC` (default 10s),
   a **"Working…" card** appears and updates every `FEISHU_CARD_INTERVAL_SEC` (default 10s) with a
   status/tool-log excerpt; turns finishing under the defer threshold send **no** card. The progress
   card is a **status indicator only**: on completion it flips to "Done" and the **actual result is
   delivered as a separate bot text message** (always). The card carries a **Stop** button while running.
8. **Stuck watchdog:** a turn that emits no events for `FEISHU_STUCK_TIMEOUT` seconds (default 180)
   and has no pending approval is auto-interrupted and reported as `"(no activity — stopped)"`.
9. **Memory loads:** a run in the configured workdir sees the project `CLAUDE.md` and auto-memory
   (verify via a prompt asking Claude to recall a fact placed in CLAUDE.md). No `--bare` is passed.
10. **No PTY deps:** `pip install -e .` on all three platforms pulls **no** `mcp` and **no**
    `pywinpty`; the package runs on Python ≥ 3.10.
11. **Old layer removed:** the `mcp_channel/` package and its tests are deleted; `bridge/` is the
    sole implementation. README, `.env.example`, `skills/feishu-bridge/SKILL.md`,
    `.claude-plugin/commands/feishu/*`, `install.py`, `run-bridge.*`, `.mcp.json` are updated.
12. **Stub integration test passes:** a fake-claude stub (speaks stream-json + the control protocol)
    drives the adapter end-to-end — streaming card updates, an approval card raised and resolved by a
    simulated button, and `/stop` interrupt — without any real API calls.

## 4. Detailed specification

### 4.1 Inputs
- **Feishu messages** via the existing websocket long-connection (`feishu_ingest.start_ws`). Each
  inbound event is the flat dict already produced: `{message_id, chat_id, chat_type, message_type,
  open_id, text, create_time, ts}`. **New:** also capture `thread_id` (add to the flattened dict) so
  topic-group messages get a distinct scope.
- **Card action callbacks** (new): interactive-card button taps (Approve/Deny/Stop) arrive as
  `card.action.trigger` events (`P2CardActionTriggerV1`) on the same websocket. The dispatcher must
  register a handler for these in addition to messages.
- **Slash commands** parsed from message text: `/stop` (cancel current turn). Unknown `/<x>` is
  passed through to Claude as ordinary text.
- **Config** via `.env` / `CLAUDE_PLUGIN_OPTION_*` (reuse `feishu_api.cred`): `FEISHU_APP_ID`,
  `FEISHU_APP_SECRET`, `FEISHU_ALLOWED_OPEN_IDS`, `FEISHU_ALLOWED_CHAT_IDS`, `FEISHU_EMOJI_WORKING`
  (`OnIt`), `FEISHU_EMOJI_DONE` (`Done`), plus new keys (§6).

### 4.2 Outputs
- **Reactions:** `OnIt` on the user's message at intake; removed + `Done` stamped at finalize.
- **Progress card (status only):** deferred — created only if a turn runs past
  `FEISHU_CARD_DEFER_SEC` (default 60s), then updated every `FEISHU_CARD_INTERVAL_SEC` (default
  30s) with a compact status/tool-log excerpt. On completion it flips to a **"Done"** status
  ("✅ Done — result in the reply below.") — it never holds the full answer. Interactive cards are
  updated via the Feishu **PATCH** endpoint (`message.patch`); PUT `/update` only supports text/post.
- **Result message:** the actual answer is **always** delivered as a normal bot text message
  (separate from the progress card), whether or not a card was shown.
- **Approval card:** an interactive card with **four** buttons — **Approve** / **Approve all
  (turn)** / **Deny** / **Deny + stop** — raised when a non-allowlisted tool is requested; resolved
  by a button tap or a chat reply (`approve`/`all`/`deny`/`stop`), else auto-denied on timeout. On
  resolution the card re-renders to keep only the clicked button. Approve ⇒ allow; Approve all ⇒
  allow + auto-allow the rest of the turn; Deny ⇒ deny tool, continue; Deny + stop ⇒ deny + interrupt.
- **Auto-replies:** single-flight rejection text; `(stopped)`; `(no activity — stopped)`;
  `(approval timed out)`.

### 4.3 Behavior — per-scope state machine
A **scope** = `chat_id` for p2p/regular groups, `chat_id:thread_id` for topic groups. The runtime
keeps one `ScopeRunner` per scope.

```
IDLE
 │  inbound message (allowed sender)
 ▼
[stamp OnIt] ──► RUNNING (single-flight lock taken)
 │                  │ claude turn: stream events → update card
 │                  │ if tool off-allowlist ─► post approval card, AWAIT_APPROVAL
 │                  │                          │ Approve/Deny/timeout ◄── card.action
 │                  │                          ▼
 │                  │ ◄────────────────────────┘ continue/deny
 │                  │ /stop or stuck ─► interrupt
 │                  ▼
 │             [finalize: post/hold final card, OnIt→Done, release lock, persist session_id]
 ▼
IDLE

Second message while RUNNING (same scope):
  → stamp Done on it, auto-reply "(still working… send /stop)", DROP content, stay RUNNING.
```

Notes:
- The single-flight lock is per-scope. Different scopes run concurrently (bounded by a global
  `ProcessPool`-like cap, default `FEISHU_MAX_CONCURRENT=4`).
- `/stop` is only honored from the **same scope** that owns the running turn.
- Each scope owns **one long-lived claude subprocess** (lazy-started on first turn, reused for
  subsequent turns via stdin user-turn lines). The subprocess is the session; `session_id` is
  captured from the first `system/init` and persisted so it can be `--resume`d after a crash/restart.

### 4.4 Data model
**Session store** — `conversation/sessions.json` (atomic write via `feishu_api.atomic_write_json`):
```json
{
  "<scope>": {"session_id": "<uuid>", "updated_at": "2026-08-08T12:00:00Z", "cwd": "<workdir>"}
}
```
**Scope state (in-memory)** — per scope: `phase` (IDLE/RUNNING/AWAIT_APPROVAL), `claude` (the live
`Transport`/process), `card_message_id` (current streaming card), `working_reaction_id`,
`pending_approval` (`{request_id, tool, input, card_message_id, future}` or None), `last_event_ts`.

**Approval pending map** — keyed by the card's callback value (a per-approval token), pointing at the
`pending_approval` entry so the card-action handler can resolve it.

### 4.5 Interfaces (exact module layout)
New top-level package **`bridge/`**. Reused: `feishu_api.py` (extended).

```
bridge/
  __main__.py        # console entry feishu-bridge = bridge.__main__:run ; CLI: up | status | stop | run
  config.py          # read .env/cred; dataclass BridgeConfig (paths, timeouts, allowlists, workdir)
  supervisor.py      # cross-platform detached spawn + pidfile; up/status/stop (replaces PTY keeper)
  runtime.py         # asyncio event loop: start ws ingest, route intake/card-actions, manage scopes
  scope.py           # ScopeRunner: single-flight lock, phase machine, owns one Transport
  ingest.py          # = moved feishu_ingest.start_ws + thread_id + P2CardActionTriggerV1 handler
  access.py          # = moved access.allowed
  lark.py            # thin wrapper over feishu_api: react_onit/react_done, send_card, update_card,
                     #   send_approval_card; fail-soft
  cards.py           # streaming-card builder/renderer (state → CardKit/interactive JSON), throttled
  approvals.py       # allowlist check, pending-approval map, timeout, resolve(approve|deny)
  session_store.py   # load/save conversation/sessions.json (atomic), get/set per scope
  watchdog.py        # stuck detection: no events for N s and no pending approval → interrupt
  agent/
    __init__.py      # AgentAdapter ABC: async run_turn(prompt, on_event, on_approval) -> TurnResult
    claude_adapter.py# ClaudeAdapter: owns one Transport per scope, maps events, wires approvals
  transport.py       # spawn claude, NDJSON line framing, initialize handshake, control correlation,
                     #   send_user_turn(), interrupt(), can_use_tool inbound dispatch, teardown
feishu_api.py        # + send_card(chat_id, card_json) -> msg_id ; update_card(msg_id, card_json) -> bool
```

**Key signatures**
```python
# bridge/agent/__init__.py
class AgentAdapter(Protocol):
    async def start(self, session_id: str | None) -> None: ...          # ensure live process
    async def run_turn(self, prompt: str, emit: Callable[[AgentEvent], Awaitable[None]]) -> TurnResult: ...
    async def interrupt(self) -> None: ...
    async def stop(self) -> None: ...

# bridge/transport.py
class Transport:
    async def initialize(self) -> None                                # send {subtype:"initialize"}, await ack
    async def send_user_turn(self, text: str) -> None                 # NDJSON {"type":"user",...}\n
    async def request(self, payload: dict) -> dict                    # control_request w/ request_id → response
    async def interrupt(self) -> None                                 # control_request {subtype:"interrupt"}
    async def events(self) -> AsyncIterator[dict]                     # stream + control frames from stdout
    async def close(self) -> None                                     # EOF → SIGTERM(5s) → SIGKILL

# bridge/approvals.py
async def request_approval(tool: str, inp: dict, scope) -> Approval:  # posts card, awaits button/timeout
```

**Hand-rolled control protocol** (mirror the open-source Python SDK; reference paths in §6):
- Spawn: `claude -p --input-format stream-json --output-format stream-json --verbose
  --permission-prompt-tool stdio [--session-id <id>|--resume <id>] [--permission-mode <m> |
  --dangerously-skip-permissions] --add-dir <workdir>`, env `CLAUDE_CODE_ENTRYPOINT=pipe-bridge`,
  stripped inherited `CLAUDECODE`. stdout/stderr piped (non-TTY ⇒ non-interactive; `-p` is explicit).
- Line framing: a `_LineFramer` reassembles complete `\n`-terminated lines; each line `json.loads`ed;
  non-`{` lines skipped.
- Outbound control: `{"type":"control_request","request_id":"req_<n>_<rand>","request":{...}}`; await
  matching `{"type":"control_response","response":{"request_id":...}}` via a `request_id→Future` map.
- Initialize first: `{"subtype":"initialize", ...}`; then user turns `{"type":"user","session_id":"",
  "message":{"role":"user","content":<text>},"parent_tool_use_id":null}`.
- Inbound control (CLI ⇒ bridge): `subtype:"can_use_tool"` ⇒ look up tool in allowlist ⇒ allow, or
  post an approval card and await the user's button/reply ⇒ reply as a `control_response`:
  Approve ⇒ `{"behavior":"allow"}`; Approve all (turn) ⇒ `{"behavior":"allow"}` + set a per-turn
  auto-allow flag (subsequent `can_use_tool` in the turn return allow with no card); Deny ⇒
  `{"behavior":"deny","message":"user denied"}`; Deny + stop ⇒
  `{"behavior":"deny","message":"user denied","interrupt":true}` (the `interrupt` flag also cancels
  the turn, mirroring the SDK's `PermissionResultDeny(interrupt=True)`). Timeout ⇒ deny. The verdict
  arrives either as a `card.action.trigger` button tap OR a chat reply (`approve`/`all`/`deny`/`stop`).
  `subtype:"interrupt"` receipts ignored.
- Interrupt: send `control_request {"subtype":"interrupt"}`; if no `result` within 5 s, `close()`
  (EOF → SIGTERM → SIGKILL) and mark the subprocess dead (next turn respawns with `--resume`).

### 4.6 Error handling & edge cases
- **Claude subprocess crashes / exits unexpectedly mid-session:** detect EOF/non-zero exit during a
  turn → post `"(Claude stopped unexpectedly — restarting)"`, respawn with `--resume <session_id>`,
  re-send the current prompt once. If it crashes twice in a row for the same prompt → `Done` + report,
  do not loop.
- **Control protocol mismatch / unknown frame:** log + ignore the frame; never crash the runtime.
- **Approval timeout:** `FEISHU_APPROVAL_TIMEOUT` (default 300 s) → auto-deny with
  `"(approval timed out)"`; Claude continues with the tool denied.
- **Card API failure:** `send_card`/`update_card` are fail-soft (like existing reactions); on create
  failure fall back to `send_text`; on update failure skip the update.
- **Websocket disconnect:** `lark.ws.Client` reconnects internally (existing behavior); the runtime
  stays up. Stale messages (created before boot) dropped via `feishu_ingest.is_stale`.
- **`--permission-prompt-tool stdio` unsupported by installed claude:** version-probe at startup
  (`claude --help` contains the flag; require ≥ the version that ships it); if absent, disable
  approval cards and fall back to `--dangerously-skip-permissions` with a loud warning.

### 4.7 Security & permissions
- Allowlist (`access.allowed`) gates who can reach the bot, unchanged.
- The **tool auto-approve allowlist** (§6) defaults read-only tools to auto-approve; mutating/execute
  tools always prompt. This is the human-in-the-loop safety boundary.
- `.env` secrets never logged; approval card summaries truncate tool `input` to avoid dumping secrets.
- Workspace trust: running non-interactively skips the trust dialog (claude behavior); the workdir is
  fixed by config, not user-controlled at runtime.

## 5. Architecture and file layout
Create/modify per §4.5. **Delete:** `mcp_channel/` (`server.py`, `launcher.py`, `__main__.py`,
`bridgestate.py`, `digest_tracker.py`, `watchdog.py`, `doctor.py`, `feishu_ingest.py`, `access.py`,
`__init__.py`) and its tests (`tests/test_digest_tracker.py`, `test_ingest_post.py`,
`test_launcher_discover.py`, `test_launcher_session.py`, `test_watchdog.py`, `stdio_smoke.py` — move
ingest-post coverage into a new `tests/test_ingest.py`). **Modify:** `pyproject.toml` (package
`bridge`, deps drop `mcp`+`[windows]`, script `feishu-bridge`), `feishu_api.py` (+card fns),
`install.py`/`install.sh`/`install.bat`/`install.ps1` (no `.mcp.json`, no pywinpty, new venv script),
`run-bridge.sh`/`run-bridge.bat`, `README.md`, `.env.example`, `skills/feishu-bridge/SKILL.md`,
`.claude-plugin/commands/feishu/{up,status,stop,mode,doctor}.md` (drop `mode` if no longer relevant),
`.mcp.json` (remove — no MCP channel).

## 6. Dependencies
- **Keep:** `lark-oapi==1.7.1` (websocket + REST). **Drop:** `mcp==1.28.1`, optional `[windows]`
  (`pywinpty`). **No new runtime deps** (hand-rolled; stdlib `asyncio`/`subprocess`/`json` only).
- **External:** the `claude` CLI on PATH (version-probed; must support `--input-format stream-json`
  and `--permission-prompt-tool`). `feishu_api.load_env`/`cred` unchanged.
- **New config keys** (`.env.example`): `FEISHU_WORKDIR` (default repo root), `FEISHU_MAX_CONCURRENT`
  (4), `FEISHU_STUCK_TIMEOUT` (180s), `FEISHU_APPROVAL_TIMEOUT` (300s),
  `FEISHU_AUTO_APPROVE_TOOLS` (`Read,Grep,Glob,WebSearch,WebFetch,TodoWrite`),
  `FEISHU_CARD_THROTTLE_MS` (1500), `FEISHU_CARD_DEFER_SEC` (10), `FEISHU_CARD_INTERVAL_SEC` (10),
  `FEISHU_DEFAULT_PERMISSION_MODE` (`bypassPermissions` — used only
  when approval cards are unavailable).
- **CLAUDE.md constraint:** long-running commands (running the bridge, real claude) go through the
  dedicated tmux pane per workspace rules; quick read-only commands inline.

## 7. Testing strategy
- **Unit** (`tests/`): NDJSON line framing + partial-line buffering; `control_request`/`response`
  correlation (incl. timeout); scope single-flight (reject path) + `/stop`; `session_store`
  atomic round-trip; streaming-card renderer state→JSON; approval allowlist + timeout; ingest flatten
  incl. `thread_id`; card-action routing.
- **Fake-claude stub** (`tests/fake_claude.py`): a small script that reads NDJSON user turns from
  stdin, emits `system/init`→`assistant`→(a `control_request can_use_tool`)→on `allow` emits
  `tool_result`+`result`, honoring `interrupt`. Drives `ClaudeAdapter` end-to-end without API calls
  (acceptance #12). Run via the tmux pane.
- **Cross-platform supervisor:** unit-test detached-spawn flag selection per `platform.system()`
  using monkeypatched `subprocess.Popen`.
- **Manual smoke (tmux):** real `claude` on the repo workdir — verify emoji cycle, continuity,
  approval card, `/stop`, streaming card on a live Feishu chat.

## 8. Open questions
1. Approval card UX detail: show full tool `input` or a redacted summary? *Owner: implementer —
   default redacted summary (truncate 500 chars), full on a tap-to-expand if CardKit supports it.*
2. ~~Should Deny also offer "Deny + stop turn"?~~ **Resolved (2026-08-08):** yes — the approval card
   has **three** buttons: Approve / Deny / Deny + stop (the last maps to a deny with `interrupt:true`,
   cancelling the whole turn). See D10.
3. On scope process idle (no turn for N minutes), tear down the claude subprocess to save resources?
   *Owner: implementer — default idle-shutdown after 15 min; respawn on next turn via `--resume`.*
4. Keep the `/mode` plugin command? With approval cards the permission model is richer than the old
   bypass/plan toggle. *Owner: user — likely remove for v1.*

## Appendix A — Decision log

| # | Decision | Options considered | Chosen | Rationale | Date |
|---|----------|--------------------|--------|-----------|------|
| D1 | Agent invocation model | (a) Python Agent SDK (b) raw `-p` per turn + `--resume` (c) hand-rolled `--input-format stream-json` long-lived client | (c) | User preference: no SDK dependency, one process/session, full control; base mechanism is documented in `claude --help` | 2026-08-08 |
| D2 | Control protocol (for approvals + graceful /stop) | (a) hand-roll it (b) get it from the SDK (c) drop approvals | (a) | User chose to hand-roll, accepting the maintenance/version-stability cost; open-source Python SDK is the reference | 2026-08-08 |
| D3 | Permissions policy | (a) full autonomy (b) Lark approval cards (c) acceptEdits+allowlist | (b) | Human-in-the-loop safety for risky tools; powered by D2 | 2026-08-08 |
| D4 | Concurrency (2nd msg during work) | (a) queue (b) reject+/stop hint (c) interrupt+merge | (b) | Predictable, no lost-work risk, simple; `/stop` gives the escape hatch | 2026-08-08 |
| D5 | Delivery | (a) final-only text (b) streaming card (c) final+nudge | (b) | Richer UX; card doubles as approval/stop UI surface | 2026-08-08 |
| D6 | Process supervision | (a) detached spawn (b) OS service mgrs (c) foreground | (a) | Uniform cross-platform, replaces PTY keeper, matches "no PTY/tmux" goal | 2026-08-08 |
| D7 | Inbound transport | reuse `lark_oapi` websocket (vs `@larksuite/channel` SDK) | reuse | Python; already integrated; `feishu_ingest` reusable | 2026-08-08 |
| D8 | Memory loading | pass `--bare`? | no | CLAUDE.md + auto-memory must load (AC #9); avoid `--bare` | 2026-08-08 |
| D9 | Old layer | refactor in place vs new package | new `bridge/` package, delete `mcp_channel/` | clean unification; old MCP/PTY design fully retired | 2026-08-08 |
| D10 | Approval card buttons | (a) Approve/Deny (b) three-button Approve/Deny/Deny+stop | (b) three-button | Deny+stop maps cleanly to `PermissionResultDeny(interrupt=true)`; gives the user an explicit "abort" without reaching for the streaming card's Stop button | 2026-08-08 |
| D11 | Streaming card content | (a) minimal status only (b) compact tool log + partial answer (c) full thinking/noise | (b) compact tool log + partial answer | enough signal that long turns don't look frozen, without dumping raw reasoning; the open-source bridge's CoT philosophy, trimmed | 2026-08-08 |
| D12 | Progress card timing + role | (a) card at turn start w/ full answer (b) deferred status-only card + result as a separate message | (b) | live-smoke feedback: early card + answer-in-card was noisy and never updated cleanly; defer 60s/30s, status-only on done, result as a bot message | 2026-08-10 |
| D13 | "Approve all (turn)" mode-change | (a) per-tool only (b) add an escalate button | (b) | tool-heavy tasks needed ~10 clicks; a per-turn escalation (resets each turn) cuts friction while preserving the per-turn safety re-confirm | 2026-08-10 |
| D14 | Interactive card update API | PUT `/update` vs PATCH `/patch` | PATCH | Feishu's PUT update only supports text/post; interactive cards require the PATCH endpoint (body = content only, no msg_type). Found via live error 230001 | 2026-08-10 |
| D15 | Approval resolution channels | (a) card buttons only (b) buttons + chat reply | (b) | card.action.trigger delivery depends on console subscription; replying approve/deny/stop works over the message channel with no setup — ship both | 2026-08-10 |

## Appendix B — Glossary
- **Scope:** the conversation unit the bridge tracks — `chat_id`, or `chat_id:thread_id` in topics.
- **Turn:** one user prompt → claude's autonomous agent loop → final `result`. One turn = one
  `run_turn()` call; many tool calls may happen inside it.
- **Control protocol:** the bidirectional NDJSON request/response layer (correlation IDs) over the
  claude subprocess's stdio, used for `initialize`, `can_use_tool`, `interrupt`.
- **Streaming card:** one Feishu interactive card per turn, updated in place as events stream.
- **Approval card:** an interactive card with Approve/Deny buttons, resolving a `can_use_tool` request.
- **Transport:** the bridge's object owning the claude subprocess + framing + control correlation.
