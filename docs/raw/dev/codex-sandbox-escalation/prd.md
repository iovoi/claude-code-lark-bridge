# PRD: Codex sandbox escalation card (approval parity, exec-mode approximation)

- **Status:** In Progress
- **Feature dir:** `docs/raw/dev/codex-sandbox-escalation/`
- **Created:** 2026-09-13 · **Last updated:** 2026-09-13
- **Parent feature:** `docs/raw/dev/codex-agent-support/` (§8 parity matrix —
  this closes the biggest gap, approximately)

## 0. Resume protocol
Read this `prd.md`, then `tasks.md`, then `log.md`; resume at the first
unchecked task. Do not re-derive the design.

## 1. Overview
`codex exec` has no interactive approval channel, so codex chats lack the
approval-card UX claude gets. This feature approximates it inside the
**CodexAdapter interpreter**: when a command fails because of the sandbox,
the adapter routes an escalation request through the SAME middle-layer
`ApprovalCallback` (→ the same Lark approval cards); on approval it escalates
the sandbox for that thread and retries the turn.

## 2. Goals and non-goals
- **Goals:**
  - Sandbox-denied commands surface as an approval card in chat (same card
    component, same verdicts: Approve / Approve-all / Deny / Deny+stop).
  - On allow: retry the turn once with a higher sandbox tier, same thread.
  - On approve_all: keep the escalated tier for the adapter's lifetime
    (subsequent turns of the chat skip asking).
  - On deny: the turn continues with the failure codex already produced.
- **Non-goals:**
  - Per-tool granularity (sandbox tiers are per-process, not per-command —
    exec mode cannot do better).
  - read-only base tier (the model preemptively refuses writes without
    emitting command events — undetectable; see log).
  - Mid-turn interruption to escalate (v1 escalates after the turn ends).
  - The full solution (app-server protocol migration) — separate future
    feature.

## 3. Acceptance criteria
1. A `command_execution` item with `status="failed"` whose output matches a
   denial signature is detected; ordinary failures (e.g. command-not-found)
   are not. ✅ `test_denial_detection_*`
2. Detected denial + `approval_callback` present → callback invoked with
   tool `"sandbox-escalation"` and `{"command", "error"}`; the turn result is
   unaffected while waiting (approval pending counts as activity).
   ✅ `test_escalation_flow`
3. Verdict `allow` → adapter reruns the turn once with the next sandbox tier
   (workspace-write → danger-full-access), same thread id, appending a retry
   instruction. ✅ same test
4. Verdict `approve_all` → same as allow, plus the tier stays escalated for
   later turns (no second card). ✅ `test_escalation_flow_approve_all`
5. Verdict `deny`/`deny_stop` or no callback → no rerun; turn ends normally
   (deny_stop also kills any remaining process). ✅ same tests
6. At most ONE escalation rerun per `run_turn` call (loop-proof).
   ✅ `test_escalation_no_loop`
7. Claude adapter behavior unchanged. ✅ full suite.

## 4. Detailed specification

### 4.1 Detection
`_looks_sandbox_denied(item: dict) -> bool` — True iff `item.type ==
"command_execution"` AND `item.status == "failed"` AND lowercased
`aggregated_output` contains one of the built-in signatures:
- `"access to the path"` + `"is denied"` (Windows workspace-write, verified live)
- `"sandbox policy"` / `"blocked by the sandbox"` (codex's own denial strings)
- `"operation not permitted"` (Linux Landlock EPERM)
- `"operation not allowed"` (generic)
Extra signatures: `FEISHU_CODEX_DENY_PATTERNS` (comma-separated substrings,
OR-ed in).

### 4.2 Escalation flow (end of turn)
After the turn's stream ends (normal `turn.completed` OR eof), and a denial
was detected during it, and the tier is not already escalated:
1. `verdict = await approval_callback("sandbox-escalation",
   {"command": <first denied command>, "error": <output snippet>})`
2. `"deny"` → return the turn result as-is.
   `"deny_stop"` → as deny (nothing left to kill; stream already ended).
3. `"allow"` / `"approve_all"` → escalate tier
   (`_ESCALATION_ORDER = ("read-only", "workspace-write", "danger-full-access")`,
   next entry; at top → stays), set `self._escalated = (verdict == "approve_all")`,
   and rerun ONCE: same thread (`exec resume`), prompt =
   original prompt + `"\n\n[sandbox escalated to <tier>. Retry the previously
   blocked operation now."`; the rerun's events are emitted on the same
   `emit`; its result replaces the first.
4. No approval_callback → no card, no rerun (headless/CI behavior).

### 4.3 Interfaces
- `CodexAdapter.__init__` unchanged (already takes `approval_callback`).
- New module-level: `_ESCALATION_ORDER`, `_looks_sandbox_denied(item)`.
- `CodexAdapter` new state: `self._sandbox` (effective tier, starts at
  `cfg.codex_sandbox`), `self._escalated: bool`.
- `run_turn` gains the end-of-turn escalation step; argv builder receives
  the effective `self._sandbox` (no signature change).
- Config: `FEISHU_CODEX_DENY_PATTERNS: list[str]` (default empty).

### 4.4 Error handling
- Escalation rerun failing again with a denial: no further cards (one rerun
  per turn); the failure surfaces in the turn result as usual.
- Approval timeout (`FEISHU_APPROVAL_TIMEOUT`, default 300s) auto-denies →
  behaves as deny.
- Detection must never raise: wrap output slicing in try/except.

### 4.5 Security
- Escalation NEVER goes above `danger-full-access` (top of order), and only
  after an explicit human approval (card tap or reply).
- `deny` never changes the tier.

## 5. Architecture and file layout
- `bridge/agent/codex_adapter.py` (edit) — detection + escalation flow.
- `bridge/config.py` (edit) — `codex_deny_patterns` field.
- `tests/test_codex_agent.py` (edit) — new tests.
- `docs/raw/dev/codex-sandbox-escalation/` — this documentation.

## 6. Dependencies
None new. Verified against Windows codex.exe (codex-cli 0.147 family);
Linux denial strings included per codex docs but NOT live-verified (noted).

## 7. Testing strategy
Unit tests with fake subprocesses (existing FakeProc pattern) + a scripted
approval callback; full suite in the repo venv via the session tmux pane
(workspace CLAUDE.md). Live smoke: after deploy, ask the bot to write a file
outside the workdir → expect an approval card, then success on allow.

## 8. Open questions
None blocking. (Live Linux denial strings unverified — patterns best-effort.)

## Appendix A — Decision log

| # | Decision | Options considered | Chosen | Rationale | Date |
|---|----------|--------------------|--------|-----------|------|
| D1 | Escalation trigger | (a) read-only base + write-denial (b) workspace-write base + out-of-workdir/network denial | (b) | live probe: read-only makes the model refuse preemptively — no command events, undetectable; workspace-write denials ARE observable | 2026-09-13 |
| D2 | Escalation timing | (a) kill mid-turn on denial (b) ask after the turn ends, rerun once | (b) | exec is one-shot per turn; mid-turn kill loses the thread; codex already finishes gracefully after a denial | 2026-09-13 |
| D3 | UX surface | (a) new card type (b) reuse ApprovalManager via ApprovalCallback | (b) | exactly the middle-layer contract the user required: same feature, interpreter-specific implementation | 2026-09-13 |
| D4 | Rerun prompt | (a) bare re-send (b) append explicit retry instruction | (b) | thread already contains the failed attempt; an explicit instruction makes the retry deterministic | 2026-09-13 |
