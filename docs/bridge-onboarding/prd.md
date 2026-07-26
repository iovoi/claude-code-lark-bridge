# PRD: bridge-onboarding

- **Status:** Draft
- **Feature dir:** `docs/bridge-onboarding/`
- **Created:** 2026-07-20 · **Last updated:** 2026-07-20 (D6 bypass+allowlist, D7 mode-respawn, D8 dev-fallback)

## 0. Resume protocol
New agent: read this `prd.md`, then `tasks.md`, then `log.md`; resume from the first
unchecked task. This doc is the source of truth — do not re-derive the design.

## 1. Overview
Smooth onboarding + one-command bring-up for the Feishu/Lark MCP channel built in
`docs/mcp-bridge/`. Removes the manual `venv`/`pip`, the `.env` editing (when the
plugin path is allowed), and the long `claude --dangerously-load-development-channels
server:feishu` command. Adds (1) `uvx` so deps are auto-fetched (no venv/pip/clone),
(2) `userConfig` so credentials are prompted at enable (no `.env`, when plugin-
installed), (4) a `doctor` that validates the Feishu app config and fails loudly with
the exact fix, and a "bring up the bridge" action that launches a **detached headless**
bridge session (no tmux, no terminal window, cross-platform) plus status/stop.

## 2. Goals and non-goals
- **Goals:**
  - `uvx` one-command deps (no venv/pip) via `uvx --from git+https://github.com/iovoi/claude-code-lark-bridge`.
  - `userConfig` in `plugin.json` for APP_ID/SECRET/allowlist → no `.env` when plugin-installed.
  - `doctor` validation (creds, websocket connect, app scopes/events), auto at bring-up + on-demand `/feishu:doctor`.
  - `/feishu:up` launches a **detached headless** bridge session B (PTY + log), cross-platform; `/feishu:status`, `/feishu:stop` manage it.
  - B self-cleans: no orphan `mcp_channel` when B exits (parent-death signal + scoped SessionEnd hook).
  - B runs in `bypassPermissions` by default (headless must not hang) but **only if an allowlist is set** (mandatory allowlist = the trust boundary).
  - `/feishu:mode <mode>` respawns B with the **same session id** (`--resume <id> --permission-mode <new>`), preserving conversation history across mode changes.
- **Non-goals:**
  - QR PersonalAgent auto-provision (explicitly excluded).
  - Always-on OS service (systemd/launchd) — B runs while launched; user stops it.
  - A visible terminal window for B (headless + log only).
  - The allowlist fix itself (Anthropic-side; we still use `--dangerously-load-development-channels` for org accounts).

## 3. Acceptance criteria
1. `uvx --from git+https://github.com/iovoi/claude-code-lark-bridge feishu-channel --help` runs (package + console entry point exist; deps fetched by uvx). No venv/pip.
2. `plugin.json` declares `userConfig` for the 4 keys; enabling the plugin prompts for them; values reach the server as `CLAUDE_PLUGIN_OPTION_*` (verified the server reads them as the creds when `.env` is absent).
3. `/feishu:doctor` (and auto-run at bring-up) reports, per check, PASS/FAIL: creds present; websocket connects to `wss://msg-frontier.feishu.cn`; if ws fails, a hint that scopes/`im.message.receive_v1`/long-connection mode may be missing.
4. `/feishu:up` launches B detached-headless (no terminal window, no tmux); B's process survives A (`/exit` on A leaves B running); output is teed to the log; a PID file records B.
5. `/feishu:status` reports B up/down + last log lines; `/feishu:stop` stops B cleanly.
6. On B exit (clean or killed), no `mcp_channel`/`feishu-channel` orphan remains (`pgrep` clean within ~3s).
7. Works on Linux/macOS (POSIX `pty` detach) and the launcher has a Windows code path (`pywinpty` / `CREATE_NEW_PROCESS_GROUP`).
8. `/feishu:up` refuses to launch B in `bypassPermissions` when no allowlist is set (mandatory-allowlist rule).
9. `/feishu:mode plan` (after a prior exchange) respawns B with the **same session id**; B's history still contains the pre-change exchange; new messages continue that session.
10. `install.py` (via `install.sh`) creates `~/.chat_bridge/{venv,repo}`, `pip install uv` into the venv, fetches the repo (git-clone if git present, else curl tarball), rewrites `.mcp.json` to the venv uvx, and installs the `feishu-bridge` skill to `~/.claude/skills/`.
11. `run-bridge.sh` launches the bridge (calls `launcher up` with the venv python + repo cwd).
12. AC7: the launcher `keeper()` dispatches on `platform.system()` — a Windows pywinpty code path exists (py_compile clean); runtime verification on Windows pending.

## 4. Detailed specification

### 4.1 Inputs
- Feishu creds: `CLAUDE_PLUGIN_OPTION_FEISHU_APP_ID` / `..._APP_SECRET` (from `userConfig`, plugin path) OR `FEISHU_APP_ID` / `FEISHU_APP_SECRET` (env / `.env`, dev path). `feishu_api` reads CLAUDE_PLUGIN_OPTION_* first, then the classic names.
- Allowlist: `CLAUDE_PLUGIN_OPTION_FEISHU_ALLOWED_OPEN_IDS` / `..._CHAT_IDS` OR `FEISHU_ALLOWED_OPEN_IDS` / `FEISHU_ALLOWED_CHAT_IDS`.

### 4.2 Outputs
- B process: `claude --dangerously-load-development-channels server:feishu`, detached, PTY-backed, env `FEISHU_BRIDGE=1`.
- Log: `/tmp/feishu-channel.log` (existing) — B's stderr teed there.
- PID file: `~/.feishu-bridge/bridge.pid` (+ last-start timestamp).

### 4.3 Behavior — bring-up / lifecycle
- **`/feishu:up`** (command → `mcp_channel/launcher.py up`):
  1. Orphan check: `pgrep -f 'feishu-channel|python.*-m mcp_channel'`; kill strays not matching the recorded PID.
  2. Ensure creds present (run doctor's cred check); if missing → print which, abort.
  3. Detach-spawn B: POSIX → `os.setsid` + `pty.fork`/`subprocess.Popen(start_new_session=True)` with a PTY; Windows → `subprocess.Popen(creationflags=CREATE_NEW_PROCESS_GROUP|DETACHED_PROCESS)` + `pywinpty`. B's stdout/stderr → log. Env `FEISHU_BRIDGE=1`. cwd = repo root.
  3b. **Auto-confirm the dev-channels dialog:** the keeper sends `\r` (Enter) to the PTY on a fixed cadence (every 4s for ~30s) — confirms "I am using this for local development"; noop on the empty prompt. (Text-matching is unreliable: ANSI strips spaces.)
  4. Write `~/.feishu-bridge/bridge.pid`.
  5. Poll the log up to ~120s for `connected to wss://msg-frontier.feishu.cn`; report UP + chat_id hint, or the doctor failure if it doesn't connect.
- **`/feishu:status`**: read PID file; `os.kill(pid,0)`; tail last ~15 log lines.
- **`/feishu:stop`**: kill the PID (SIGTERM → SIGKILL after 5s); clear PID file; reap orphans.
- **`/feishu:doctor`**: §4.4.
- **B self-cleanup**: (a) the channel server sets `prctl(PR_SET_PDEATHSIG, SIGTERM)` on Linux so it dies if B dies; (b) a `SessionEnd` hook (scoped to `FEISHU_BRIDGE=1`) runs `pkill -f 'feishu-channel|python.*-m mcp_channel'` as a safety net.

### 4.3.1 Mode, session-id, and respawn
- **Session-id pinning:** on first `/feishu:up`, the launcher generates a UUID (or reads a stored one from `~/.feishu-bridge/bridge.session`) and launches B with `--session-id <uuid>`. It records that UUID.
- **Permission mode:** B launches with `--permission-mode bypassPermissions` by default. **Mandatory allowlist:** the launcher refuses `bypassPermissions` (and `bypassPermissions`-equivalent) unless `FEISHU_ALLOWED_OPEN_IDS`/`_CHAT_IDS` is non-empty. Other modes (`plan`, `auto`, `acceptEdits`) have no such requirement.
- **`/feishu:mode <mode>`:** stop B, then relaunch with `--resume <bridge-session-uuid> --permission-mode <mode>` plus the same channel/dev flags and cwd. Same UUID → Claude resumes the conversation; new messages continue the session. (T-spike verifies `--resume` works together with `--channels`/`--dangerously-load-development-channels`.)
- **Dev-flag auto-fallback (Q1):** the launcher first tries the clean `--channels plugin:feishu@feishu-local`; if the channel fails to register (allowlist), it falls back to `--dangerously-load-development-channels server:feishu`. Either way the user types neither.

### 4.4 doctor checks (each PASS/FAIL + hint)
1. Creds present (APP_ID starts `cli_`, SECRET non-empty) — from CLAUDE_PLUGIN_OPTION_* or env/.env.
2. Allowlist set (WARN, not fail, if unset).
3. Websocket connect: start `lark.ws.Client`, expect `connected to wss://msg-frontier.feishu.cn` within ~20s. FAIL → hint: "ensure the app has im:message + im:message:send_as_bot scopes, Events → Long-Connection mode, and im.message.receive_v1 subscribed."
4. Print a one-line summary + the exact fix for the first failing check.

### 4.5 Interfaces
- `pyproject.toml`: `[project.scripts] feishu-channel = "mcp_channel.__main__:run"`; `[project] dependencies = ["lark-oapi==1.7.1","mcp==1.28.1"]` (entry point so `uvx` builds the command).
- `mcp_channel/__main__.py`: add `def run(): anyio.run(main)` (console entry point; keeps `python -m mcp_channel` working).
- `mcp_channel/launcher.py`: `up()`, `status()`, `stop()`, `doctor()` — CLI `python -m mcp_channel.launcher <cmd>`; cross-platform detach (§4.3).
- `mcp_channel/doctor.py`: `run_doctor() -> int` (0 ok, 1 fail); prints PASS/FAIL per check.
- `feishu_api.py`: cred resolution helper `resolve_creds()` — CLAUDE_PLUGIN_OPTION_* first, classic names fallback; `APP_ID`/`APP_SECRET`/allowlist read via it.
- `.mcp.json`: `command: uvx`, `args: ["--from","git+https://github.com/iovoi/claude-code-lark-bridge","feishu-channel"]`.
- `.claude-plugin/plugin.json`: add `userConfig` (§4.1 keys; `sensitive:true` for the secret).
- `.claude-plugin/commands/feishu/{up,status,stop,doctor}.md`: plugin command stubs calling `python -m mcp_channel.launcher <cmd>` (or `uvx ... feishu-channel-ctl <cmd>`).
- `.claude-plugin/hooks.json` (or settings hook): SessionEnd → scoped orphan-kill.

### 4.6 Error handling & edge cases
- B already running → `/feishu:up` reports UP (idempotent), doesn't double-launch.
- Creds missing → `/feishu:up` refuses + points to doctor.
- `uvx`/`claude` not on PATH → clear error.
- Windows PTY missing (`pywinpty` not installed) → fall back to no-PTY detach; warn the TUI may not render (B still runs).
- Doctor ws check must clean up its probe ws (don't leak a connection that competes with B).

### 4.7 Security & permissions
- `userConfig` secret stored in secure storage (Claude Code), not settings.json.
- PID file under `~/.feishu-bridge/` (0600).
- `--dangerously-load-development-channels` is the user's explicit dev opt-in; documented.
- Doctor never prints the secret value (only PASS/FAIL).

## 5. Architecture and file layout
**New:** `pyproject.toml`; `mcp_channel/launcher.py` (POSIX + Windows pywinpty keeper); `mcp_channel/doctor.py`; `.claude-plugin/commands/feishu/{up,status,stop,doctor}.md`; `.claude-plugin/hooks.json`; `install.py` + `install.sh` + `install.bat` (installer); `run-bridge.sh` + `run-bridge.bat` (launch wrappers); `skills/feishu-bridge/SKILL.md` (agent-driven setup/run skill).
**Modified:** `mcp_channel/__main__.py` (run() entry point); `feishu_api.py` (resolve_creds + CLAUDE_PLUGIN_OPTION_*); `.mcp.json` (uvx); `.claude-plugin/plugin.json` (userConfig); `README.md` (new onboarding section).
Reuses: `feishu_api.client/send_text/add_reaction`, `mcp_channel/server.py`, `feishu_ingest.py`, `access.py`.

## 6. Dependencies
- **Add (build/runtime via uvx):** none user-installed — `uvx` fetches `lark-oapi`+`mcp` from the git package. `uv` must be on PATH (documented).
- **launcher PTY:** stdlib `pty` (POSIX); `pywinpty` (Windows) — declared optional in pyproject.
- **Host convention (CLAUDE.md):** long-running commands through a tmux pane — but B here is intentionally a detached background process (no tmux); the launcher is short-lived (spawns B, exits), so it runs inline.

## 7. Testing strategy
- **AC1:** `uvx --from git+… feishu-channel --help` exits 0.
- **AC2:** `python -c "import json; json.load(open('.claude-plugin/plugin.json'))['userConfig']"` shows the 4 keys; a unit test of `resolve_creds()` — set CLAUDE_PLUGIN_OPTION_FEISHU_APP_ID, assert it wins over env.
- **AC3:** `python -m mcp_channel.launcher doctor` (with good vs bad creds) → exit 0 vs 1 with the right FAIL line.
- **AC4/5:** `python -m mcp_channel.launcher up` then `status` shows UP; `/exit` the parent shell, B's PID still alive; `stop` kills it.
- **AC6:** after `stop`/kill, `pgrep -f 'feishu-channel|python.*-m mcp_channel'` empty.
- **AC7:** launcher detach code path selected by `platform.system()`; Windows path unit-checkable by importing the function (mock pywinpty).

## 8. Open questions
- **OQ1 (RESOLVED, T0):** claude needs a PTY (no-TTY → --print → exit) AND shows a dev-channels confirmation dialog that the keeper auto-confirms via fixed-cadence Enter. Headless works; B spawns mcp_channel + connects the ws.
- **OQ2:** confirm bridge actions as plugin **commands** vs skills — commands chosen (deterministic launchers); the agent can still invoke them. (User said "skill"; confirm at review.)
- **OQ3 (T-spike):** verify `claude --resume <uuid> --permission-mode <mode> --channels …` (or dev-flag form) actually resumes the session and applies the mode (D7). If `--resume`+`--channels` is incompatible, fall back to `--session-id <uuid>` re-pin.
- **Caveats (bugs):** permission relay is broken on the dev path (anthropics/claude-code#40064) → we must use a non-prompting mode (D6). `--dangerously-skip-permissions` can silently downgrade to acceptEdits after long runs (#43613) → B may need periodic restart; document.

## Appendix A — Decision log
| # | Decision | Options | Chosen | Rationale | Date |
|---|---|---|---|---|---|
| D1 | Bridge lifecycle | (a) tmux session (b) relaunch self (c) standalone SDK daemon (d) detached headless bg process | (d) | cross-platform (no tmux), no context loss, matches "detached from bringer" | 2026-07-20 |
| D2 | uvx source | (a) PyPI publish (b) git | (b) | no publish/account; uvx fetches from git | 2026-07-20 |
| D3 | doctor trigger | (a) on-demand (b) auto at bring-up (c) both | (c) | catch bad config early + reusable | 2026-07-20 |
| D4 | bridge actions primitive | (a) skills (b) plugin commands | (b) | deterministic launchers; agent-invokable | 2026-07-20 |
| D5 | headless TTY | bare-detach vs PTY | PTY (pywinpty on Win) | claude TUI needs a TTY; PTY gives one without a window | 2026-07-20 |
| D6 | B permission mode | (a) auto (b) bypassPermissions+allowlist (c) acceptEdits | (b) | matches wild bridges (zarazhangrui `full`/modelzen `never`); headless must not hang; allowlist is the trust boundary | 2026-07-20 |
| D7 | mode change | (a) restart fresh (b) respawn same session via --resume | (b) | preserves conversation continuity across mode switches | 2026-07-20 |
| D8 | dev-flag exposure | always dev vs auto-fallback to clean --channels | auto-fallback | clean where allowlist allows (non-org/admin); invisible to user either way | 2026-07-20 |
| D9 | installer shape | (a) install.py (b) install.sh+install.ps1 | install.py + thin install.sh/install.bat wrappers | python is a prereq anyway; one real cross-platform script | 2026-07-23 |
| D10 | install flow | (a) ~/.chat_bridge venv+pip-uv (b) global uv | (a) | isolation; uv via pip in venv; git-first (auto-update) else curl; **no lark/feishu check** (skill prompts for creds) | 2026-07-23 |
| D11 | agent-launched lifecycle | (a) detached+survives (D1) (b) stop on agent exit | detached BUT agent stops it on its own exit (skill-driven, agent-launched only); manual launches left alone | clean session for agent-launched; manual users keep always-on | 2026-07-23 |
| D12 | Windows PTY (AC7) | (a) WSL2 only (b) pywinpty port | (b) | native-Windows support; keeper dispatches on platform.system() -> pywinPTY on Windows; code present, **untested** (no Windows env) | 2026-07-23 |

## Appendix B — Glossary
- **B / the bridge**: the detached headless `claude --channels` session that hosts the Feishu channel.
- **A / bringer**: the interactive session that runs `/feishu:up` to launch B.
- **`uvx`**: uv's tool runner — fetches a package + deps into an isolated env on first run.
- **`userConfig`**: plugin-manifest option; Claude Code prompts at enable, injects as `CLAUDE_PLUGIN_OPTION_*` env to the server.
