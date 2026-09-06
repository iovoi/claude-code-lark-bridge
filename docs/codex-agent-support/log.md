# Implementation log: Codex agent support

> Append-only record of anything that could NOT be known at planning time and
> that a fresh agent needs in order to rebuild or resume the *real* feature.
> Newest entry at the TOP (most recent first) so a returning agent sees it first.
> One entry per event. Keep entries factual and specific.

## How to add an entry
Copy the template below, fill it in, and insert it at the top of "Entries".

### Template
### YYYY-MM-DD HH:MM — <short title>
- **Task:** T#.# (or "planning")
- **What happened:** <observation / action>
- **Discovery / blocker:** <what was unexpected>
- **Resolution / workaround:** <what you did, concretely>
- **PRD impact:** none | amended §X (describe the change)

## Entries

### 2026-09-06 21:55 — Docs backfilled (formal-feature applied retroactively)
- **Task:** planning
- **What happened:** user asked whether the formal-feature skill had been used;
  it had not. The three docs were written after implementation from the actual
  code, session events, and test results.
- **Discovery / blocker:** the Phase A/B review gates were necessarily skipped
  (the software already existed); tasks were checked only where acceptance was
  demonstrably verified.
- **Resolution / workaround:** backfill note added to `prd.md` §0 and
  `tasks.md`; T4.3/T4.4 left unchecked as the honest resume point.
- **PRD impact:** none (docs created to match reality).

### 2026-09-06 21:35 — Mirror skill was stale
- **Task:** T3.3
- **What happened:** copying the updated `skills/feishu-bridge/SKILL.md` to the
  workspace mirror (`/mnt/c/Users/wade/Desktop/workspace/skills/skills/feishu-bridge/SKILL.md`)
  overwrote a copy that still described the removed `mcp_channel/`
  architecture (pre-dating the pipe-bridge reimplementation).
- **Discovery / blocker:** the mirror had diverged (not maintained since the
  mcp_channel era).
- **Resolution / workaround:** diffed first, confirmed mirror-side content was
  purely stale (no unique newer content), then overwrote.
- **PRD impact:** none.

### 2026-09-06 21:20 — Live codex probe: shapes confirmed, WSL auth revoked
- **Task:** T4.2
- **What happened:** ran `printf 'Reply with the single word: pong\n' | codex
  exec --json -s read-only --skip-git-repo-check -`. Exit 1 — every auth call
  401: `refresh_token_invalidated` / `token_revoked`.
- **Discovery / blocker:** WSL's `~/.codex` auth is revoked; the Windows Codex
  desktop app's credentials (separate `CODEX_HOME`) do NOT carry over to WSL.
  Even so, stdout carried the full JSONL skeleton: `thread.started`
  (field `thread_id`), `turn.started`, `error` (field `message`), `turn.failed`
  (`error.message` nested) — all matching the mapper.
- **Resolution / workaround:** mapper's shapes validated for the four
  confirmed events; `item.*` payloads follow the Responses-API schema and the
  mapper is defensive (unknown item types ignored, both `aggregated_output`
  and `output` checked). Live smoke deferred to T4.3 pending `codex login`.
- **PRD impact:** none; §8 records the pending verification.

### 2026-09-06 21:15 — Two test-authoring bugs fixed (not adapter bugs)
- **Task:** T4.1
- **What happened:** first full-suite run: 45 passed, 2 failed — both in
  `tests/test_codex_agent.py`: (1) `argv[:3] == ["codex","exec"]` (3 items vs
  2 — should be `argv[:2]`); (2) `events.append` (sync, returns None) passed
  as the `emit` callback that the adapter awaits → `TypeError`.
- **Discovery / blocker:** none in product code.
- **Resolution / workaround:** fixed both assertions/callbacks; rerun →
  `47 passed`.
- **PRD impact:** none.

### 2026-09-06 20:35 — No tmux server (workspace rule) deferred verification
- **Task:** planning/T4.1
- **What happened:** workspace CLAUDE.md routes test suites and network calls
  through a dedicated tmux pane; none was reachable mid-session, so coding
  proceeded first and the probe + suite ran only after the user started tmux.
- **Discovery / blocker:** also: repeated send-keys commands initially ran in
  the wrong pane cwd (job tmp dir) — several `.venv/bin/python: No such file
  or directory` errors before a standalone `cd` fixed the pane cwd.
- **Resolution / workaround:** implemented everything codeable without the
  pane; asked the user to start tmux; once up, claimed pane `%1`, primed it,
  and ran probe + suite there.
- **PRD impact:** none (§6 notes the tmux constraint).

### 2026-09-06 20:15 — Feature implemented directly, docs skipped
- **Task:** planning
- **What happened:** the feature (branch `feat/codex-agent-support`) was built
  code-first without the formal-feature skill's PRD/task-list/log.
- **Discovery / blocker:** none — process omission, surfaced by the user.
- **Resolution / workaround:** this documentation set backfills the trail;
  commit order in git shows the code-first reality.
- **PRD impact:** docs written 2026-09-06 describing the built software.
