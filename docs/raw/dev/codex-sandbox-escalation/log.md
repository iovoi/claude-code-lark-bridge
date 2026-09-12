# Implementation log: Codex sandbox escalation card

> Append-only; newest entry first. Facts a fresh agent cannot get from the PRD.

## Entries

### 2026-09-13 16:05 — T3.3 live smoke PASSED (post-fix retest)
- **Task:** T3.3
- **What happened:** after the semantics fix + bridge restart, the user
  re-tested in Feishu. Log shows three escalation cards in one chat: two
  resolved `allow` (each out-of-workdir write asked again — allow is no
  longer sticky) and one `deny` (refusal path: no rerun). All as designed.
- **Resolution / workaround:** feature complete; PRD status → Complete.
- **PRD impact:** none.

### 2026-09-13 15:40 — Live smoke T3.3 + fix: plain "allow" was sticky
- **Task:** T3.3
- **What happened:** user live-tested. Test 1 (write to C:\Users\wade\):
  card shown, resolved `allow`, escalated rerun wrote the file ✓. Test 2
  (write to Desktop, same chat): NO card, wrote directly — unexpected.
- **Discovery / blocker:** after a plain `allow` the adapter left
  `self._sandbox` at danger-full-access for the chat's lifetime; only the
  "don't re-ask" part was approve_all-gated. The tier itself leaked —
  claude's Approve is per-use, Approve-all persists.
- **Resolution / workaround:** plain `allow` now elevates only the rerun;
  after it, the tier reverts to the base (`cfg.codex_sandbox`). Only
  `approve_all` keeps the chat elevated. Tests updated (rerun argv still
  elevated; tier back to base after allow; stays elevated after
  approve_all), suite 58 passed. Redeployed.
- **PRD impact:** §4.2 tier semantics amended.

### 2026-09-13 15:05 — Implementation complete, suite 58 passed
- **Task:** T1.1, T2.1, T3.1
- **What happened:** detection (`_looks_sandbox_denied` + built-in signatures
  + `FEISHU_CODEX_DENY_PATTERNS`), end-of-turn escalation via
  `ApprovalCallback` (one rerun per turn, tier bump one step,
  approve_all persists), 6 new tests. Full suite 58 passed in the tmux pane.
- **PRD impact:** none (built to spec).
- Next: T3.2 deploy + T3.3 live smoke (needs the user in Feishu).

### 2026-09-13 14:20 — Live sandbox probes (design-defining)
- **Task:** planning (Phase A)
- **What happened:** three probes against the Windows codex.exe:
  read-only write request; workspace-write network command; workspace-write
  write OUTSIDE the workdir.
- **Discovery:** (1) read-only → the model refuses preemptively in text; NO
  command_execution events at all → escalation cannot be triggered from
  events at that tier. (2) workspace-write does NOT block network on Windows
  (curl ran; only `head` missing in PowerShell failed). (3) workspace-write
  DOES block out-of-workdir writes: command runs and fails with
  `Set-Content : Access to the path 'C:\…' is denied.` — item
  `status="failed"`, `exit_code=1` → cleanly detectable.
- **Resolution:** base tier stays workspace-write; trigger = failed
  command_execution whose output matches denial signatures (PRD §4.1, D1/D2).
- **PRD impact:** none (probe preceded the PRD write).

### 2026-09-13 14:15 — Phase B review gate covered by user decision
- **Task:** planning
- **What happened:** user selected the approach explicitly ("先按 exec +
  沙箱升级卡近似来推进"), which approves the design direction; docs were
  then written directly.
- **PRD impact:** gate skip recorded here per formal-feature convention.

### 2026-09-13 14:10 — Docs relocated (llm-wiki restructure)
- **Task:** planning
- **What happened:** user restructured `docs/` into an LLM wiki
  (`raw/schema/wiki`); feature docs now live under `docs/raw/dev/<feature>/`.
  This feature's docs were created directly at the new location. The
  restructure is uncommitted user work — feature commits must `git add` only
  their own paths.
- **PRD impact:** none.
