# Task list: mcp-bridge (Feishu/Lark MCP channel)

> Sequenced build plan. Work top to bottom. Check a task (`- [ ]` → `- [x]`) only
> when its **acceptance** check passes. Whenever you check one, append a one-line
> note to `log.md`. Tasks are atomic and self-contained: a fresh agent can do any
> single task from the PRD alone.

## Phase 0 — Spike (de-risk the unknowns)
- [x] **T0.1** Spike: Python `mcp` SDK — capability + custom notification
  - Files: none (throwaway script under `$CLAUDE_JOB_DIR/tmp/`)
  - What: prove the `mcp` SDK can (a) declare an experimental capability
    (`claude/channel`) in the server `initialize` result, and (b) send an
    arbitrary notification method (`notifications/claude/channel`) to the client
    from OUTSIDE a tool handler (a background task/thread). Record the exact SDK
    version + the precise calls (FastMCP vs low-level `Server`/`ServerSession`).
    Resolves PRD OQ1.
  - Acceptance: a runnable snippet that, when driven by a stdio JSON-RPC client
    (or `mcp`'s own client), emits a `notifications/claude/channel` notification
    after `initialize`; documented exact API in `log.md`.
  - Depends on: —
- [x] **T0.2** Spike: channel plugin registration shape
  - Files: none (research)
  - What: determine the minimal `.claude-plugin/plugin.json` + `.mcp.json` (or
    settings) shape so `claude --channels plugin:feishu` discovers a LOCAL
    channel. Check Claude Code docs (`code.claude.com/docs/en/channels`) and the
    official discord plugin layout. Resolves PRD OQ2.
  - Acceptance: the exact files + JSON to register a local channel are written
    into `log.md` (and used in T2.4).
  - Depends on: —

## Phase 1 — Scaffolding
- [x] **T1.1** Create the `mcp_channel/` package
  - Files: `mcp_channel/__init__.py` (new), `mcp_channel/__main__.py` (new)
  - What: empty package + entry that calls `server.main()`. (PRD §4.5, §5)
  - Acceptance: `python -m mcp_channel` prints "mcp_channel: not implemented"
    and exits (placeholder), no import error.
  - Depends on: —
- [x] **T1.2** Add the `mcp` dependency
  - Files: `requirements.txt` (edit)
  - What: add the `mcp` SDK (version pinned per T0.1). Reinstall into `.venv`.
  - Acceptance: `.venv/bin/python -c "import mcp; print(mcp.__version__)"` works.
  - Depends on: T0.1

## Phase 2 — Core channel logic
- [x] **T2.1** `access.py` — allowlist
  - Files: `mcp_channel/access.py` (new)
  - What: implement `allowed(open_id, chat_id)` per PRD §4.5; read
    `FEISHU_ALLOWED_OPEN_IDS` / `FEISHU_ALLOWED_CHAT_IDS`; allow-all + WARN if
    both empty.
  - Acceptance: unit-style check — unset env → returns True; set env + matching
    id → True; set env + non-matching → False.
  - Depends on: T1.1
- [x] **T2.2** `feishu_ingest.py` — websocket receiver + stale guard
  - Files: `mcp_channel/feishu_ingest.py` (new)
  - What: `start_ws(on_message)` runs `lark.ws.Client` in a daemon thread with a
    `P2ImMessageReceiveV1` handler that extracts {text, message_id, chat_id,
    chat_type, open_id, create_time} and calls `on_message(evt)`; plus
    `is_stale(create_time, boot_time)`. (PRD §4.3 step 3, §4.5)
  - Acceptance: with real `.env`, `start_ws(print)` logs `connected to wss://`
    and prints an inbound DM's dict. (Manual/live.)
  - Depends on: T1.1
- [x] **T2.3** `server.py` — MCP server, capabilities, tools, drain loop
  - Files: `mcp_channel/server.py` (new)
  - What: build the MCP server using the API chosen in T0.1; declare
    `claude/channel`; register `reply`/`react` tools (call `feishu_api`); capture
    the session; start ws thread (T2.2) feeding an `asyncio.Queue`; drain task
    does access-check (T2.1) + stale guard + send
    `notifications/claude/channel`. Gate sending on an `initialized` Event.
    (PRD §4.3, §4.5, §4.6)
  - Acceptance: `tests/stdio_smoke.py` drives `initialize` → result includes the
    `claude/channel` capability; `tools/list` returns `reply` + `react`;
    `tools/call reply` returns `sent`/`send failed`.
  - Depends on: T0.1, T1.2, T2.1, T2.2
- [x] **T2.4** Channel registration files
  - Files: `.mcp.json` (new), `.claude-plugin/plugin.json` (new)
  - What: register the `feishu` MCP server per T0.2. (PRD §4.5, §5)
  - Acceptance: `cat .mcp.json` shows the feishu server entry; plugin.json valid
    JSON with name/version.
  - Depends on: T0.2, T2.3

## Phase 3 — Wire-up, cleanup, docs
- [x] **T3.1** `feishu_api.py` trim
  - Files: `feishu_api.py` (edit)
  - What: remove tmux-only dead code (`claude_is_busy`, `clean_line`,
    `_BORDER_CHARS`, `CLAUDE_PANE`, `VENV_PYTHON`, `HELP_TEXT`, `EMOJI_*`,
    `STALE_SEC`); keep `client`, `send_text`, `add_reaction`, `delete_reaction`,
    `load_env`, APP_ID/APP_SECRET config. (PRD §5)
  - Acceptance: `python -c "import feishu_api"` OK; channel still imports & sends.
  - Depends on: T2.3
- [x] **T3.2** Remove v1 bridge files + ledgers
  - Files: delete `bot.py`, `deliver_to_claude.py`, `bridgectl.py`,
    `mark_handled.py`, `pending_messages.py`, `react.py`, `send_message.py`;
    delete `conversation/.claude_busy.json`, `.delivered.json`, `.reactions.json`,
    `.handled.json`.
  - What: `git rm` the tracked files; rm the runtime ledgers. (PRD §5, AC7)
  - Acceptance: `git ls-files` lists none of the removed files; repo still has
    `feishu_api.py`, `requirements.txt`, `README.md`, `.env.example`, `.gitignore`.
  - Depends on: T3.1
- [x] **T3.3** Update `.env.example`
  - Files: `.env.example` (edit)
  - What: add `FEISHU_ALLOWED_OPEN_IDS` / `FEISHU_ALLOWED_CHAT_IDS`; remove
    `FEISHU_CLAUDE_PANE` and emoji keys; update the header comment. (PRD §4.4, §5)
  - Acceptance: `.env.example` documents only the channel-relevant keys.
  - Depends on: —
- [x] **T3.4** Update `README.md`
  - Files: `README.md` (edit)
  - What: replace tmux-bridge setup with channel setup: install, `.env`, register
    plugin, run `claude --channels plugin:feishu`. (PRD §5)
  - Acceptance: README run steps match what T2/T3 actually produce.
  - Depends on: T2.4, T3.3
- [x] **T3.5** `tests/stdio_smoke.py`
  - Files: `tests/stdio_smoke.py` (new)
  - What: a minimal MCP stdio client that spawns `python -m mcp_channel`, sends
    `initialize`, asserts `claude/channel` capability present and `tools/list`
    contains `reply`+`react`. (PRD §7 AC2)
  - Acceptance: `python tests/stdio_smoke.py` exits 0 against a real `.env`.
  - Depends on: T2.3

## Phase 4 — Verify & wrap up
- [x] **T4.1** Run every acceptance criterion (PRD §3); record results in `log.md`.
- [x] **T4.2** Update `prd.md` Status → Complete; write final `log.md` summary;
  commit on `feat/mcp-bridge`.


## Phase 5 — Working → done emoji cycle (post-complete enhancement)
Ports the v1 bridge's reaction bookends into the channel.

- [x] **T5.1** Emoji config in `feishu_api.py`
  - Files: `feishu_api.py`
  - What: add `EMOJI_WORKING`/`EMOJI_DONE` from env (defaults OnIt/Done). (PRD §4.8)
  - Acceptance: `python -c "import feishu_api; print(feishu_api.EMOJI_WORKING)"` → OnIt.
- [x] **T5.2** Cycle helpers + wire-in in `server.py`
  - Files: `mcp_channel/server.py`
  - What: `_REACTIONS` bounded LRU + `_stamp_working` (called in `_push` before the
    notification) + `_finish_working` (called in the `reply` tool when send ok).
    (PRD §4.8)
  - Acceptance: unit test of the map (stamp→remember→finish→done+remove+consume) passes.
- [x] **T5.3** Document emoji keys in `.env.example`
  - Files: `.env.example`
  - Acceptance: `FEISHU_EMOJI_WORKING`/`FEISHU_EMOJI_DONE` present.
- [x] **T5.4** Unit-test the cycle map logic (no real Feishu)
  - Acceptance: stamp→remember→finish removes working + stamps done + consumes entry.
- [x] **T5.5** Regression stdio smoke (dummy creds) still green
  - Acceptance: `tests/stdio_smoke.py` → SMOKE OK (emoji path only runs on successful send).
- [x] **T5.6** Update PRD/tasks/log for the enhancement
  - Acceptance: prd AC8 + §4.8 + D6; this Phase 5; log entry.
