# Implementation log: Pipe Bridge

> Append-only record of anything that could NOT be known at planning time and that a fresh agent needs
> in order to rebuild or resume the *real* feature. Newest entry at the TOP (most recent first) so a
> returning agent sees it first. One entry per event. Keep entries factual and specific.

## How to add an entry
Copy the template below, fill it in, and insert it at the top of "Entries".

### Template
### YYYY-MM-DD HH:MM — <short title>
- **Task:** T#.# (or "planning")
- **What happened:** <observation / action>
- **Discovery / blocker:** <what was unexpected — e.g. disk full, tool rate-limited, dependency
  version conflict, planned API doesn't exist, perf worse than expected>
- **Resolution / workaround:** <what you did, concretely>
- **PRD impact:** none | amended §X (describe the change)

## Entries

### 2026-08-08 — Phase 6 (install/docs/old-layer removal) complete
- **Task:** T6.1–T6.3
- **What happened:** Reworked `install.py` (uv-based, no `.mcp.json`/`[windows]`/pywinpty,
  5 phases, `_platform_note` replaces the native-Windows warning), `run-bridge.sh`/`.bat`
  (→ `feishu-bridge up`), `requirements.txt` (→ `-e .`, dropped `mcp`+transitives),
  `README.md`, `.env.example` (all new keys), `SKILL.md`, `plugin.json` (v0.3.0, no
  mcp/reply-react), and the slash commands (`up`/`status`/`stop`/`doctor` → `feishu-bridge`;
  removed `mode.md`). **Deleted** `mcp_channel/`, the old tests
  (`test_digest_tracker/test_ingest_post/test_launcher_*/test_watchdog/stdio_smoke`),
  `.mcp.json`. Updated the `feishu_api.py` docstring (now `bridge/`, not `mcp_channel/`).
  Full suite: **32 passed** (old mcp tests gone); `install.py` compiles.
- **Discovery / blocker:** Stray `mcp_channel` refs remain only in root scratch notes
  (`nested-munching-stream.md`, `tidy-pondering-walrus.md`), an old worktree under
  `.claude/worktrees/`, and one stale allow-rule in `.claude/settings.local.json` — none are
  bridge source/docs; left as-is. A full `install.py` run (clone+venv) is part of the live
  smoke (Phase 8), not unit-tested.
- **Resolution / workaround:** as above.
- **PRD impact:** none.

### 2026-08-08 — Phases 5 & 7 (orchestration core + end-to-end) complete
- **Task:** T5.1–T5.5, T7.1–T7.2
- **What happened:** Wrote `bridge/session_store.py`, `approvals.py` (3-button card + timeout +
  resolve), `watchdog.py` (stuck detection, paused while approval pending), `scope.py`
  (ScopeRunner: single-flight reject+/stop-hint, OnIt→Done cycle, streaming card, lazy resumed
  adapter, approval delegation, stuck interrupt — with an injectable `adapter_factory` for tests),
  `runtime.py` (asyncio loop, thread-safe ingest trampolines, /stop + card-action routing, global
  concurrency semaphore), `supervisor.py` (cross-platform detached up/status/stop, pidfile,
  ctypes Win32 liveness). Tests: `test_scope.py`, `test_adapter_e2e.py` (vs `fake_claude.py` stub:
  streaming, approval allow, deny+stop interrupt), `test_session_and_watchdog.py`,
  `test_runtime_supervisor.py`. **Full suite: 40 passed** (32 new + 8 pre-existing mcp_channel).
- **Discovery / blocker:** Runtime test initially dropped all messages because the repo's real
  ``.env`` allowlist rejects the synthetic sender — fixed by monkeypatching ``access.allowed`` in
  that test. Live behavior of (a) the hand-rolled control protocol against *real* claude
  (especially the `initialize` handshake and exact `can_use_tool` shapes), (b) the websocket
  card-action dispatch, and (c) supervisor up/stop on each OS is **still pending the T8.1 smoke
  test** — unit tests use the stub and injected fakes.
- **Resolution / workaround:** as above; smoke deferred.
- **PRD impact:** none.

### 2026-08-08 — Phases 3 & 4 (agent adapter + Lark layer) complete
- **Task:** T3.1–T3.3, T4.1–T4.3
- **What happened:** `bridge/agent/__init__.py` (AgentEvent union + AgentAdapter protocol) +
  `bridge/agent/claude_adapter.py` (frame→event mapping, session-id capture, allowlist +
  approval-callback routing). `bridge/access.py` (moved), `bridge/ingest.py` (thread_id +
  `register_p2_card_action_trigger_v1`, guarded), `bridge/cards.py` (streaming + 3-button
  approval renderers, throttled StreamingCard), `bridge/lark.py` (wrapper over feishu_api).
  Tests: `tests/test_phase34.py` (mapper, throttle, ingest text/stale). Full suite **27 passed**.
- **Discovery / blocker:** (1) Fixed a potential circular import (cards↔lark) by gating the
  `Lark` type ref behind `TYPE_CHECKING`. (2) My first `is_stale` test used impossible epoch
  values — corrected; the production `is_stale` is unchanged from the verified original.
  (3) Card-action dispatch (`on_card`) is code-complete but its runtime parsing isn't unit-tested
  (it's an inner closure); **validate live in the T8.1 smoke** (tap an approval/stop button).
- **Resolution / workaround:** as above.
- **PRD impact:** none.

### 2026-08-08 — Phase 2 (transport + hand-rolled control protocol) complete
- **Task:** T2.1–T2.3
- **What happened:** Wrote `bridge/transport.py` — `_LineFramer`, `_build_claude_argv`,
  `Transport` (spawn one `claude -p --input-format stream-json --output-format stream-json`
  subprocess, NDJSON framing, `initialize`/`request` control correlation with `request_id`,
  `send_user_turn`, `interrupt`, inbound `can_use_tool` → `permission_handler`, `events()` until
  `result`, cross-platform teardown: stdin EOF → SIGTERM/`taskkill` → SIGKILL). Added
  `tests/test_transport.py` (11 tests, all pass) using injected fake streams — no real claude.
- **Discovery / blocker:** Control-message shapes are mirrored from the open-source Python SDK and
  are **semi-documented**; the `initialize` handshake payload in particular may need extra fields for
  real claude. Mitigation: `initialize()` is best-effort (timed out → log + continue). Real-claude
  validation deferred to the T8.1 smoke test and the T7 stub.
- **Resolution / workaround:** None needed for the stub-driven unit tests; flagged for smoke.
- **PRD impact:** none (matches §4.5/§4.6).

### 2026-08-08 — Phase 1 (scaffolding) complete
- **Task:** T1.1–T1.3
- **What happened:** Created `bridge/` (`__init__.py`, `__main__.py` CLI stub, `config.py` with
  `BridgeConfig.load()`); added `send_card`/`update_card` to `feishu_api.py`; rewrote `pyproject.toml`
  → `feishu-bridge`, deps `lark-oapi` only (dropped `mcp` + `[windows]`/`pywinpty`), script
  `feishu-bridge = bridge.__main__:run`. Installed editable.
- **Discovery / blocker:** System `python3` (3.14) has **no `pip` module**. The repo's intended
  toolchain is `uv` (per `install.py`), and a `.venv` already exists. **Dev loop: use `uv pip
  install -e .` and run via `.venv/bin/python` / `.venv/bin/feishu-bridge`.** Long-running commands
  go through the dedicated tmux pane (`%1`, session `claude-bridge`).
- **Resolution / workaround:** Adopted `uv` + `.venv`; verified `import bridge, feishu_api` and
  `feishu-bridge --version` (0.1.0). Update `install.py`/`install.*` in T6.1 to match (uv-based).
- **PRD impact:** none (toolchain detail; install task already covers it).

### 2026-08-08 — Approval card → three buttons; streaming card keeps compact tool log
- **Task:** planning (Phase B revision, pre-implementation)
- **What happened:** User reviewed the card mockups and resolved OQ2: the approval card is now
  **three buttons** — Approve / Deny / **Deny + stop**. Deny+stop maps to a deny with `interrupt:true`
  (cancels the whole turn). Streaming card keeps the **compact tool log + partial answer** (not
  minimal). Updated prd.md (AC #6, §4.2, §4.5 control mapping, §8 OQ2 closed, Appendix A D10/D11) and
  tasks.md (T4.2, T5.3).
- **Discovery / blocker:** none — design clarification only.
- **Resolution / workaround:** n/a.
- **PRD impact:** amended §4.2, §4.5, §8; added D10, D11.

### 2026-08-08 — Planning complete; spec drafted, awaiting Phase B review
- **Task:** planning (Phase A → Phase B)
- **What happened:** Decisions D1–D9 resolved with the user (see prd.md Appendix A). Branch
  `feat/pipe-bridge` created. `prd.md`, `tasks.md`, and this `log.md` written. No feature code yet.
- **Discovery / blocker:** The two highest-risk areas are (1) the hand-rolled control protocol
  (semi-documented; mirrors the open-source Python SDK; version-sensitive) and (2) the
  `--permission-prompt-tool stdio` flag, whose presence must be version-probed at startup with a
  documented fallback to `--dangerously-skip-permissions` (PRD §4.6). Feishu interactive cards need a
  card-action websocket handler (`P2CardActionTriggerV1`) that the current ingest does not register.
- **Resolution / workaround:** Captured both as explicit tasks (T2.2/T3.3 control protocol; T4.1 card
  actions; T5.3 approval/timeout). Open questions OQ1–OQ4 recorded in prd.md §8 for resolution during
  implementation.
- **PRD impact:** none (this is the baseline plan)
