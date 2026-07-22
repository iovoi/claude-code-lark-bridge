# Task list: bridge-onboarding

> Work top→bottom. Check `- [ ]`→`- [x]` only when acceptance passes; append a
> log line each time. Atomic tasks; a fresh agent can do any one from the PRD.

## Phase 0 — Spike (de-risk)
- [x] **T0.1** Spike: does `claude --dangerously-load-development-channels server:feishu` run headless (PTY, no human) and survive parent `/exit`?
  - Files: throwaway in `$CLAUDE_JOB_DIR/tmp/`
  - What: spawn B detached with a PTY (`pty.fork`/`subprocess`+`start_new_session`), redirect to log; verify it stays alive after the spawner exits and the ws connects. Resolves PRD OQ1.
  - Acceptance: documented in log.md whether headless+PTY works as-is or needs a PTY-keeper; result recorded in Appendix A.
  - Depends on: —

## Phase 1 — uvx packaging (point 1)
- [x] **T1.1** `pyproject.toml` + console entry point
  - Files: `pyproject.toml` (new), `mcp_channel/__main__.py` (edit: add `run()`).
  - What: `[project.scripts] feishu-channel = "mcp_channel.__main__:run"`; deps `lark-oapi==1.7.1`, `mcp==1.28.1`, optional `pywinpty; platform_system=="Windows"`. `run()` = `anyio.run(main)`. (PRD §4.5)
  - Acceptance: `python -m mcp_channel` still works; `python -c "import mcp_channel.__main__ as m; assert hasattr(m,'run')"`.
  - Depends on: —
- [x] **T1.2** `.mcp.json` → uvx
  - Files: `.mcp.json`
  - What: `command: uvx`, `args: ["--from","git+https://github.com/iovoi/claude-code-lark-bridge","feishu-channel"]`. (PRD §4.5)
  - Acceptance: `cat .mcp.json` shows the uvx command.
  - Depends on: T1.1
- [x] **T1.3** Verify uvx fetches + runs the entry point
  - Acceptance: `uvx --from git+https://github.com/iovoi/claude-code-lark-bridge feishu-channel --help` (or import probe) exits 0. (PRD AC1)
  - Depends on: T1.1, T1.2 (and the commit pushed so git has pyproject)

## Phase 2 — userConfig + creds (point 2)
- [ ] **T2.1** `plugin.json` userConfig
  - Files: `.claude-plugin/plugin.json`
  - What: add `userConfig` for FEISHU_APP_ID (sensitive), FEISHU_APP_SECRET (sensitive), FEISHU_ALLOWED_OPEN_IDS, FEISHU_ALLOWED_CHAT_IDS with descriptions/types. (PRD §4.1, §4.5)
  - Acceptance: valid JSON; `userConfig` has the 4 keys; secret fields `sensitive:true`.
  - Depends on: —
- [ ] **T2.2** `feishu_api.resolve_creds()` reads CLAUDE_PLUGIN_OPTION_* first
  - Files: `feishu_api.py`
  - What: resolve APP_ID/SECRET/allowlist from `CLAUDE_PLUGIN_OPTION_FEISHU_*` first, then classic `FEISHU_*`. Unit-test the precedence. (PRD §4.1, §4.5)
  - Acceptance: unit test — set `CLAUDE_PLUGIN_OPTION_FEISHU_APP_ID`, assert it overrides `FEISHU_APP_ID`.
  - Depends on: —

## Phase 3 — doctor (point 4)
- [ ] **T3.1** `mcp_channel/doctor.py`
  - Files: `mcp_channel/doctor.py` (new)
  - What: `run_doctor() -> int` — creds check, allowlist WARN, ws-connect probe (≤20s, cleanup), PASS/FAIL + hint per check; never print the secret. (PRD §4.4)
  - Acceptance: good creds → exit 0; bad creds → exit 1 with the creds FAIL line; ws-fail hints the scopes/event fix.
  - Depends on: T2.2
- [ ] **T3.2** `/feishu:doctor` command + auto-at-bring-up
  - Files: `.claude-plugin/commands/feishu/doctor.md`; `launcher.up` calls doctor before spawn.
  - Acceptance: `/feishu:doctor` runs the doctor; `/feishu:up` aborts with doctor output if creds missing.
  - Depends on: T3.1, T4.1

## Phase 4 — launcher + commands (bring-up lifecycle)
- [ ] **T4.1** `mcp_channel/launcher.py` (cross-platform detach + PTY)
  - Files: `mcp_channel/launcher.py` (new)
  - What: `up/status/stop` functions; POSIX `pty`+`os.setsid` detach, Windows `CREATE_NEW_PROCESS_GROUP|DETACHED_PROCESS`+`pywinpty`; PID file `~/.feishu-bridge/bridge.pid`; env `FEISHU_BRIDGE=1`; output→log; orphan pre-kill; poll log for `connected to wss`. CLI `python -m mcp_channel.launcher <cmd>`. (PRD §4.3)
  - Acceptance: `up` launches B detached (survives spawner `/exit`); `status` shows UP + tail; `stop` kills it; PID file managed. (PRD AC4/5)
  - Depends on: T0.1
- [ ] **T4.2** commands `/feishu:up`, `/feishu:status`, `/feishu:stop`
- [ ] **T4.3** session-id pinning + `/feishu:mode` respawn + bypass-allowlist
  - Files: `mcp_channel/launcher.py`, `.claude-plugin/commands/feishu/mode.md`, `.claude-plugin/commands/feishu/up.md`
  - What: launcher pins B's session UUID (`--session-id`, stored in `~/.feishu-bridge/bridge.session`); `/feishu:up` refuses `bypassPermissions` when no allowlist set (PRD §4.3.1, AC8); `/feishu:mode <m>` stops B and relaunches with `--resume <uuid> --permission-mode <m>` (continuity). T-spike verifies `--resume`+`--channels` (PRD OQ3).
  - Acceptance: after an exchange, `/feishu:mode plan` respawns B with the same session UUID; history retained; bypass refused without allowlist.
  - Depends on: T4.1, T0.1
  - Files: `.claude-plugin/commands/feishu/{up,status,stop}.md`
  - What: command stubs that run `python -m mcp_channel.launcher <cmd>`. (PRD §4.5)
  - Acceptance: each command exists and invokes the launcher.
  - Depends on: T4.1

## Phase 5 — cleanup hardening (no orphan)
- [ ] **T5.1** parent-death signal in the channel server (Linux)
  - Files: `mcp_channel/__main__.py`
  - What: `prctl(PR_SET_PDEATHSIG, SIGTERM)` guarded to Linux so the server dies if B dies. (PRD §4.3)
  - Acceptance: code present + guarded; server still starts.
  - Depends on: —
- [ ] **T5.2** SessionEnd orphan-kill hook (scoped to FEISHU_BRIDGE=1)
  - Files: `.claude-plugin/hooks.json`
  - What: SessionEnd hook runs `pkill -f 'feishu-channel|python.*-m mcp_channel'` only when `$FEISHU_BRIDGE` set. (PRD §4.3)
  - Acceptance: hook JSON valid; matches FEISHU_BRIDGE guard.
  - Depends on: —

## Phase 6 — Docs & verify
- [ ] **T6.1** README onboarding section (uvx + userConfig + /feishu:up flow)
  - Files: `README.md`
  - Acceptance: README documents the new one-command path.
  - Depends on: T1–T5
- [ ] **T6.2** Run every PRD §3 acceptance criterion; record results in log.md.
- [ ] **T6.3** `prd.md` Status → Complete; final log.md summary; commit + push `feat/bridge-onboarding`.
