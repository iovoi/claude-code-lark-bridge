# Task list: Codex agent support

> Sequenced build plan. Work top to bottom. Check a task (`- [ ]` → `- [x]`) only
> when its **acceptance** check passes. Whenever you check one, append a one-line
> note to `log.md`. Tasks are atomic and self-contained: a fresh agent can do any
> single task from the PRD alone.
>
> **Backfill note:** the feature was built before this list existed; tasks are
> checked retroactively only where the acceptance check demonstrably passed
> during the verified implementation (PR #19, suite 47 passed).

## Phase 1 — Config & factory
- [x] **T1.1** Agent-selection config fields
  - Files: `bridge/config.py` (edit)
  - What: add `agent`, `codex_bin`, `codex_extra_args`, `codex_sandbox` to
    `BridgeConfig` + `load()` (PRD §4.1); unknown agent → `claude`.
  - Acceptance: `BridgeConfig.load()` returns the four fields with defaults;
    exercised by `tests/test_codex_agent.py::_cfg`.
  - Depends on: —

- [x] **T1.2** `make_adapter` factory
  - Files: `bridge/agent/__init__.py` (edit)
  - What: factory dispatching on `cfg.agent` with lazy imports (PRD §4.5).
  - Acceptance: `test_make_adapter_dispatch` passes.
  - Depends on: T1.1, T2.1

## Phase 2 — CodexAdapter
- [x] **T2.1** Adapter module: argv + event mapping + turn loop
  - Files: `bridge/agent/codex_adapter.py` (new)
  - What: `_build_codex_argv`, `_map_item`, `_map_event`, `CodexAdapter`
    (one-shot exec per turn, stdin prompt, resume by thread id, tree-kill
    interrupt) per PRD §4.3–§4.5.
  - Acceptance: `test_codex_argv_*`, `test_map_*`,
    `test_run_turn_maps_stream_and_resumes` pass.
  - Depends on: T1.1

- [x] **T2.2** Wire the factory into the runtime path
  - Files: `bridge/scope.py` (edit)
  - What: `_default_adapter_factory` calls `make_adapter(cfg, resume=…,
    approval_callback=…)`; docstring updated.
  - Acceptance: existing scope tests still pass (suite green).
  - Depends on: T1.2

## Phase 3 — Surfaces (CLI, installer, skill)
- [x] **T3.1** `feishu-bridge --agent` flag
  - Files: `bridge/__main__.py` (edit)
  - What: `--agent` on `up`/`run` subparsers; sets `FEISHU_AGENT` env before
    dispatch (PRD §4.1).
  - Acceptance: `test_cli_accepts_agent_flag`, `test_cli_agent_flag_sets_env`.
  - Depends on: T1.1

- [x] **T3.2** Installer agent selection
  - Files: `install.py` (edit)
  - What: `_AGENT` global, `_select_agent()` (prompt / `--agent` / env),
    `_validate_agent()`, PATH check per agent in `preflight()`,
    `.env` persistence (`collect_credentials` + `_persist_agent_only`),
    DONE banner line (PRD §4.1, §4.6).
  - Acceptance: `python3 install.py --agent bogus` → exit 1 with exact error;
    `--agent codex` selected when codex on PATH; manual check done in-session.
  - Depends on: —

- [x] **T3.3** Skill documentation
  - Files: `skills/feishu-bridge/SKILL.md` (edit) + mirror at
    `/mnt/c/Users/wade/Desktop/workspace/skills/skills/feishu-bridge/SKILL.md`
  - What: `--agent` usage + codex sandbox/login notes in the manage section.
  - Acceptance: both files contain the `--agent` line; mirror diff clean.
  - Depends on: T3.1

## Phase 4 — Verify & wrap up
- [x] **T4.1** Full test suite green
  - Acceptance: `.venv/bin/python -m pytest tests/ -q` → `47 passed`.
  - Depends on: all above

- [x] **T4.2** Probe real codex event shapes (partial)
  - Acceptance: live `codex exec --json` run confirmed `thread.started`,
    `turn.started`, `error`, `turn.failed` shapes; `item.*` pending a
    logged-in codex (see log). Mapper is defensive to unknown items.
  - Depends on: T2.1

- [ ] **T4.3** Live codex smoke test over the bridge
  - What: after `codex login` in WSL — `feishu-bridge up --agent codex`, send
    a chat message, confirm reply + session resume on a second message; if
    `item.*` field names differ, fix `_map_item` and update PRD §4.4.
  - Acceptance: two consecutive turns answered; `turn.completed` usage shown.
  - Depends on: T4.1, T4.2 (user action: `codex login` in WSL)

- [ ] **T4.4** Final wrap-up
  - What: after T4.3 — PRD Status already Complete; close out log with the
    smoke-test result.
  - Depends on: T4.3
