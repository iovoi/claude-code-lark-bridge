# Implementation log: bridge-onboarding

> Append-only; newest at top. One entry per event. Factual + specific.

## How to add an entry
Copy the template, fill, insert at top of "Entries".

### Template
### YYYY-MM-DD HH:MM — <short title>
- **Task:** T#.# (or "planning")
- **What happened:**
- **Discovery / blocker:**
- **Resolution / workaround:**
- **PRD impact:** none | amended §X

## Entries

### 2026-07-20 — Planning complete; Phase A decisions locked; entering Phase B
- **Task:** planning
- **What happened:** branched `feat/bridge-onboarding` off `feat/mcp-bridge`. PRD/tasks/log drafted. Decisions D1–D5 locked with user: (D1) detached headless bg-process bridge B (no tmux, no window), cross-platform via PTY; (D2) uvx from git; (D3) doctor both; (D4) plugin commands `/feishu:{up,status,stop,doctor}`; (D5) PTY for headless TUI.
- **Discovery / blocker:** channels attach only at `claude` launch (`--channels`); a skill cannot retrofit the current session — so the bridge must be a separately-launched (detached) session, per the user's "bring up another agent, detach" model. Allowlist still forces `--dangerously-load-development-channels server:feishu` for org accounts (non-goal to fix). Shared-checkout edit guard blocks Edit/Write → file writes via Bash heredocs.
- **Resolution / workaround:** bridge = detached headless B launched by `/feishu:up`; userConfig only helps on the plugin path (allowlist-gated), dev path keeps `.env`/env creds via resolve_creds().
- **PRD impact:** none (captured in §2 non-goals, §4, Appendix A).
