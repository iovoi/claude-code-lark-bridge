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
