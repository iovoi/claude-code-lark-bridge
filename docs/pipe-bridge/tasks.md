# Task list: Pipe Bridge

> Sequenced build plan. Work top to bottom. Check a task (`- [ ]` → `- [x]`) only when its
> **acceptance** check passes. Whenever you check one, append a one-line note to `log.md`. Tasks are
> atomic and self-contained: a fresh agent can do any single task from the PRD alone. Long-running
> commands (real claude, the bridge itself) go through the dedicated tmux pane per workspace rules.

## Phase 1 — Scaffolding & packaging
- [x] **T1.1** Create the `bridge/` package skeleton.
  - Files: `bridge/__init__.py`, `bridge/__main__.py` (stub `run()`), `bridge/config.py` (`BridgeConfig` dataclass + `load()` reading `.env`/`cred` incl. new keys in PRD §6).
  - Acceptance: `python -c "import bridge, bridge.config"` succeeds; `BridgeConfig.load()` returns populated defaults from `.env.example`.
  - Depends on: —
- [x] **T1.2** Extend `feishu_api.py` with card primitives.
  - Files: `feishu_api.py` (edit) — add `send_card(chat_id, card_json) -> msg_id|None` (`msg_type="interactive"`) and `update_card(msg_id, card_json) -> bool` (PUT `im/v1/messages/:id`, interactive content).
  - Acceptance: unit test posts a fixed card JSON via a mocked `client().im.v1.message.create` and asserts the request `msg_type=="interactive"`; `update_card` calls `.update`.
  - Depends on: T1.1
- [x] **T1.3** Rewrite `pyproject.toml` for the new package.
  - Files: `pyproject.toml` (edit) — package `bridge`; deps `lark-oapi==1.7.1` only (drop `mcp`, drop `[windows]`/`pywinpty`); script `feishu-bridge = "bridge.__main__:run"`; wheel packages/force-include updated to `bridge` + `feishu_api.py`.
  - Acceptance: `pip install -e .` installs with no `mcp`/`pywinpty`; `feishu-bridge --help` runs.
  - Depends on: T1.1

## Phase 2 — Transport & hand-rolled control protocol
- [x] **T2.1** Implement NDJSON line framing + subprocess spawn.
  - Files: `bridge/transport.py` (new) — `_LineFramer` (buffer + split on `\n`), `Transport.__init__`/`_spawn`: `asyncio.create_subprocess_exec(claude, "-p","--input-format","stream-json","--output-format","stream-json","--verbose","--permission-prompt-tool","stdio", ...)` with piped stdio, env per PRD §4.5.
  - Acceptance: unit test feeds split chunks through `_LineFramer`, asserts complete JSON lines emitted and partial lines buffered.
  - Depends on: T1.1
- [x] **T2.2** Implement the control protocol: initialize + request/response correlation.
  - Files: `bridge/transport.py` (edit) — `initialize()` sends `{subtype:"initialize"}` and awaits its `control_response`; `request(payload)` assigns `request_id`, stores a `Future`, writes `control_request`, awaits with `anyio.fail_after`; reader routes `control_response`→Future, `control_request`→handler hook, stream frames→`events()`.
  - Acceptance: unit test with two `asyncio.Queue`s faking the pipe: assert `initialize()` resolves on a matching `control_response`, and an unmatched/timeout request raises after the deadline.
  - Depends on: T2.1
- [x] **T2.3** Implement user-turn send, event stream, interrupt, teardown.
  - Files: `bridge/transport.py` (edit) — `send_user_turn(text)` (NDJSON `{type:"user",...}`); `events()` async iterator yielding parsed stream/control frames until `{type:"result"}` or EOF; `interrupt()` (`control_request {subtype:"interrupt"}`); `close()` (stdin EOF → wait 5s → SIGTERM → 5s → SIGKILL).
  - Acceptance: unit test asserts `send_user_turn` writes one valid NDJSON line; `interrupt` issues the control frame; `close` escalates to terminate when the proc doesn't exit (fake proc).
  - Depends on: T2.2

## Phase 3 — ClaudeAdapter (AgentAdapter)
- [x] **T3.1** Define `AgentAdapter` + the `AgentEvent` union.
  - Files: `bridge/agent/__init__.py` (new) — `AgentAdapter` Protocol (`start`, `run_turn`, `interrupt`, `stop`) and `AgentEvent` typed dict variants (`system`, `text`, `thinking`, `tool_use`, `tool_result`, `usage`, `done`, `error`).
  - Acceptance: `mypy`/import clean; an in-file dummy adapter implements it.
  - Depends on: T1.1
- [x] **T3.2** Implement `ClaudeAdapter` event mapping + turn loop.
  - Files: `bridge/agent/claude_adapter.py` (new) — translate raw stream frames to `AgentEvent` (per the stream-json translator: `system/init`→system, assistant blocks→text/thinking/tool_use, user→tool_result, `result`→usage+done); `run_turn(prompt, emit)` calls `send_user_turn` and pumps `events()` to `emit`. Capture `session_id` from `system/init`.
  - Acceptance: unit test with the fake-claude stub (T7.2) emits the expected `AgentEvent` sequence ending in `done`; `session_id` captured.
  - Depends on: T3.1, T2.3
- [x] **T3.3** Wire approvals: route inbound `can_use_tool` → allowlist → approval callback.
  - Files: `bridge/agent/claude_adapter.py` (edit) + `bridge/approvals.py` (new) — on `control_request can_use_tool`, check `FEISHU_AUTO_APPROVE_TOOLS`; if allowed reply `allow` immediately, else call an injected `approval_callback(tool, input)` (the scope runner supplies it) and reply allow/deny; return `control_response` with `{behavior, [message], [interrupt]}`.
  - Acceptance: unit test: allowlisted tool auto-allowed (no callback); off-allowlist tool invokes the callback and forwards its verdict as the control response.
  - Depends on: T3.2

## Phase 4 — Lark layer
- [x] **T4.1** Move + extend inbound ingest (thread_id + card actions).
  - Files: `bridge/ingest.py` (new, from `mcp_channel/feishu_ingest.py`) — add `thread_id` to the flattened event; register `P2CardActionTriggerV1` handler delivering `{action_value, message_id, ...}` to an `on_card_action` callback. Move `access.py` → `bridge/access.py`.
  - Acceptance: unit test (`tests/test_ingest.py`) asserts `thread_id` present and `extract_text`/`is_stale` unchanged vs old; a fake card-action event reaches `on_card_action`.
  - Depends on: T1.1
- [x] **T4.2** Implement the Lark wrapper + emoji cycle.
  - Files: `bridge/lark.py` (new) — `stamp_onit(msg_id)->reaction_id`, `swap_to_done(msg_id, onit_reaction_id)`, `send_card`, `update_card`, `send_approval_card(tool, input, token)` posting a **three-button** card (Approve / Deny / Deny+stop) whose button values carry `{"v":"approve"|"deny"|"deny_stop","t":<token>}`. All fail-soft.
  - Acceptance: unit test with mocked `feishu_api` asserts `OnIt` add then `Done` add + `OnIt` delete ordering.
  - Depends on: T1.2
- [x] **T4.3** Implement the streaming-card renderer.
  - Files: `bridge/cards.py` (new) — build interactive-card JSON from `CardState` (status line, recent assistant text, current tool); `StreamingCard` object: `create()`→msg_id, `update(state)` throttled by `FEISHU_CARD_THROTTLE_MS`, `finalize()`. Card carries a **Stop** button while running.
  - Acceptance: unit test asserts two rapid `update()` calls within the throttle window coalesce to one `update_card`; the card JSON contains a Stop button value.
  - Depends on: T4.2

## Phase 5 — Runtime, scopes, supervisor
- [x] **T5.1** Implement session store.
  - Files: `bridge/session_store.py` (new) — `get(scope)`, `set(scope, session_id, cwd)` over `conversation/sessions.json` using `feishu_api.atomic_write_json`.
  - Acceptance: unit test round-trips two scopes and survives a corrupt file (returns `{}`).
  - Depends on: T1.1
- [x] **T5.2** Implement `ScopeRunner` (single-flight + phase machine).
  - Files: `bridge/scope.py` (new) — per-scope: lazy-start one `ClaudeAdapter` (resume by stored `session_id`), `handle_message`: if RUNNING → reject+/stop-hint+Done+drop (AC #4); else stamp OnIt, run a turn streaming into a `StreamingCard`, wire `approval_callback` to `approvals.request_approval`, finalize (Done, persist session_id). Honor `/stop`.
  - Acceptance: unit test: two rapid messages in one scope → second yields the reject auto-reply and the first's turn runs once; `/stop` calls `adapter.interrupt()`.
  - Depends on: T3.3, T4.3, T5.1
- [x] **T5.3** Implement approvals end-to-end + stuck watchdog.
  - Files: `bridge/approvals.py` (edit) + `bridge/watchdog.py` (new) — `request_approval` posts the card and awaits a card-action resolve or `FEISHU_APPROVAL_TIMEOUT` (auto-deny); `watchdog` marks stuck when no events for `FEISHU_STUCK_TIMEOUT` and no pending approval → interrupt + `(no activity — stopped)`.
  - Acceptance: unit test: simulated Approve button resolves a pending approval (allow); Deny denies the tool but the turn continues; **Deny+stop** denies and interrupts the turn (`behavior:"deny", interrupt:true`); timeout auto-denies; stuck detector fires interrupt after the deadline.
  - Depends on: T5.2, T4.1
- [x] **T5.4** Implement the runtime event loop + CLI.
  - Files: `bridge/runtime.py` (new), `bridge/__main__.py` (edit) — asyncio loop: `start_ws(on_message, on_card_action)`, dispatch to `ScopeRunner`s under a global concurrency cap (`FEISHU_MAX_CONCURRENT`); CLI `run` (foreground), delegating `up/status/stop` to the supervisor.
  - Acceptance: `feishu-bridge run --help` works; with `FEISHU_DISABLE_WS=1` the loop starts and routes a synthetic intake event to a scope runner in a test.
  - Depends on: T5.3
- [x] **T5.5** Implement cross-platform supervisor (up/status/stop).
  - Files: `bridge/supervisor.py` (new) — detached spawn (`CREATE_NEW_PROCESS_GROUP|DETACHED` on Windows, `start_new_session=True` + `stdin=DEVNULL` on POSIX), pidfile under `~/.chat_bridge/bridge.pid`; `status` reads pidfile + liveness (ctypes `OpenProcess`/`GetExitCodeProcess` on Windows, `os.kill(pid,0)` on POSIX); `stop` = terminate process tree (`taskkill /T /F` Windows / SIGTERM→SIGKILL POSIX). No PTY, no tmux.
  - Acceptance: unit test (monkeypatched `Popen`) asserts Windows vs POSIX flag selection and pidfile write/read; manual: `up`→`status` shows running→`stop` exits it.
  - Depends on: T5.4

## Phase 6 — Install, docs, plugin, old-layer removal
- [x] **T6.1** Rework installers for the new package.
  - Files: `install.py`, `install.sh`, `install.bat`, `install.ps1`, `run-bridge.sh`, `run-bridge.bat` (edit) — drop `.mcp.json` writing and `[windows]` extra; point venv script at `feishu-bridge`; keep cross-platform venv-bin handling.
  - Acceptance: `install.sh`/`install.bat` create a venv, `pip install -e .`, and emit a working `run-bridge.*` on the current OS.
  - Depends on: T5.5
- [x] **T6.2** Update docs + plugin surface.
  - Files: `README.md`, `.env.example`, `skills/feishu-bridge/SKILL.md`, `.claude-plugin/commands/feishu/{up,status,stop,doctor}.md` (drop/replace `mode.md` per OQ4), `.claude-plugin/plugin.json`, `.mcp.json` (delete).
  - Acceptance: README describes the streaming/pipe architecture, approval cards, `/stop`, and cross-platform lifecycle; `.env.example` documents all new keys; SKILL reflects new commands.
  - Depends on: T6.1
- [x] **T6.3** Remove the old MCP/PTY layer.
  - Files: delete `mcp_channel/**`, `tests/{test_digest_tracker,test_ingest_post,test_launcher_discover,test_launcher_session,test_watchdog}.py`, `tests/stdio_smoke.py`; keep/grow ingest coverage in `tests/test_ingest.py`.
  - Acceptance: `grep -rn mcp_channel .` returns nothing in source; `pytest` collection has no broken imports.
  - Depends on: T6.2

## Phase 7 — Tests: fake-claude stub + cross-platform
- [x] **T7.1** Build the fake-claude stub.
  - Files: `tests/fake_claude.py` (new) — reads NDJSON user turns; emits `system/init`→`assistant`(text)→`control_request can_use_tool`→on `allow`: `tool_result`+`result`; honors `interrupt` (emits `result` with termination). Configurable script of events.
  - Acceptance: stub runs standalone reading a scripted turn from stdin and producing the expected NDJSON on stdout.
  - Depends on: T2.3
- [x] **T7.2** End-to-end adapter test via the stub (acceptance #12).
  - Files: `tests/test_claude_adapter_e2e.py` (new) — point `ClaudeAdapter` at the stub binary; assert streaming `AgentEvent`s, one approval raised+resolved (simulated allow), and `/stop` interrupt all work with zero real API calls.
  - Acceptance: test passes offline.
  - Depends on: T7.1, T5.3

## Phase 8 — Verify & wrap up
- [ ] **T8.1** Run every acceptance criterion from PRD §3 (1–12); record results in `log.md`.
  - Acceptance: all 12 pass; manual ones (1, 7 live, smoke) executed in the tmux pane with results noted.
  - Depends on: all above
- [ ] **T8.2** Update `prd.md` Status → Complete; write final `log.md` summary; commit on `feat/pipe-bridge` and open PR.
  - Acceptance: PRD marked Complete; PR opened against `main`.
  - Depends on: T8.1
