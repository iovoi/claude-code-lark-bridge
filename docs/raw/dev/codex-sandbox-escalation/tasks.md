# Task list: Codex sandbox escalation card

> Sequenced build plan; check a task only when its acceptance passes.
> Phase B review gate: covered by the user's explicit approach decision
> ("先按 exec + 沙箱升级卡近似来推进", 2026-09-13) — see log.md.

## Phase 1 — Detection
- [x] **T1.1** Denial signatures + `_looks_sandbox_denied`
  - Files: `bridge/agent/codex_adapter.py` (edit), `bridge/config.py` (edit:
    `codex_deny_patterns` from `FEISHU_CODEX_DENY_PATTERNS`)
  - What: PRD §4.1.
  - Acceptance: `test_denial_detection_hit` (Windows verified string) and
    `test_denial_detection_ordinary_failure_miss` (command-not-found) pass.
  - Depends on: —

## Phase 2 — Escalation flow
- [x] **T2.1** End-of-turn escalation + single rerun
  - Files: `bridge/agent/codex_adapter.py` (edit)
  - What: PRD §4.2 — effective `self._sandbox`, `_escalated` persistence on
    approve_all, one rerun per turn with the appended retry instruction.
  - Acceptance: `test_escalation_flow` (allow), `test_escalation_flow_deny`,
    `test_escalation_flow_approve_all`, `test_escalation_no_loop` pass.
  - Depends on: T1.1

## Phase 3 — Verify & ship
- [x] **T3.1** Full suite green
  - Acceptance: `.venv/bin/python -m pytest tests/ -q` all pass.
- [x] **T3.2** Ship: stacked branch → PR → deploy installed copy → restart
  bridge; log the deploy in log.md.
- [x] **T3.3** Live smoke: bot asked to write outside the workdir shows an
  approval card; Allow → retry succeeds. (Needs the user in Feishu; record
  result in log.md.)
