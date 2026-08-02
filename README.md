# Feishu/Lark ↔ Claude Code MCP channel

A **Feishu/Lark channel** for Claude Code: an [MCP](https://modelcontextprotocol.io)
server that pushes inbound Feishu messages into a running Claude Code session and
exposes `reply` / `react` tools Claude uses to respond. Run Claude Code with
`--channels plugin:feishu` and chat with your codebase from Feishu/Lark.

This replaces the older tmux-scraping bridge with the architecture Anthropic uses
for its official Telegram/Discord/iMessage channels — no tmux, no screen scraping,
no marker protocol.

## How it works

```
Feishu/Lark ──ws──▶ mcp_channel (MCP server, stdio) ──▶ Claude Code session
                       │  declares experimental `claude/channel`
                       │  pushes inbound msg via notifications/claude/channel
                       │  exposes tools: reply / react
                       ◀── Claude calls reply/react to respond
```

## Prerequisites

- Python 3.10+ (project venv is 3.14)
- A Feishu/Lark **Custom App** with `im:message` + `im:message:send_as_bot` +
  `im:resource` permissions and the **long-connection (websocket)** event mode
  enabled (Developer Console → Events & Callbacks).
- Claude Code, run with `--channels plugin:feishu` (a research-preview feature;
  requires Anthropic auth via claude.ai or a Console API key).

## Setup

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env
# edit .env: set FEISHU_APP_ID, FEISHU_APP_SECRET, and (recommended) an allowlist
```

## Installation

The bridge ships a **cross-platform installer** (`install.py`) with thin platform wrappers
(`install.sh`, `install.bat`, `install.ps1`). One command per platform installs everything
under `~/.chat_bridge` — a venv + `uv`, the repo, and the `feishu-bridge` run skill (into the
agent).

**Linux / macOS / WSL2** — `install.sh` (or pipe `install.py` to python3):
```bash
curl -fsSL https://raw.githubusercontent.com/iovoi/claude-code-lark-bridge/main/install.py | python3
# or, after cloning:  ./install.sh
```

**Windows** — `install.ps1` (PowerShell) or `install.bat` (cmd):
```powershell
irm https://raw.githubusercontent.com/iovoi/claude-code-lark-bridge/main/install.py | python
# or, after cloning:  .\install.ps1      (or:  install.bat)
```
> **Native Windows** is supported via the pywinpty PTY path — see
> [`docs/NATIVE_WINDOWS.md`](docs/NATIVE_WINDOWS.md) for setup, start/stop, and config.
> **WSL2** (run `install.sh` inside Ubuntu) is also fully supported.

**All platforms** — the installer checks **Python ≥3.10** and **Claude Code** (`claude`); it
stops and tells you to install them if missing. It does **not** need git (falls back to curl)
nor any `pip install` of the bridge deps (`uvx` fetches them at runtime).

**After install**, in any Claude session just say **"run the feishu bridge"** (have your Feishu
APP_ID/SECRET ready) — the installed `feishu-bridge` skill configures `.env` and launches the
bridge for you. Or launch directly: `~/.chat_bridge/claude-code-lark-bridge/run-bridge.sh`
(Linux/mac) / `run-bridge.bat` (Windows).

## Quick start (bridge launcher — recommended)

Once deps are installed (`pip install -r requirements.txt` or via `uvx`) and `.env`
/ `userConfig` creds + an allowlist are set, you don't type the long `claude --channels`
command — use the launcher commands:

- `/feishu:up` — launch the bridge as a **detached, headless** `claude` session (PTY +
  auto-confirm of the dev-channels and bypass dialogs) and connect the Feishu websocket.
  Reports `bridge UP (pid …); Feishu websocket connected.` Default mode `auto`; opt into
  `--mode bypassPermissions` (**requires an allowlist** — the trust boundary); `/feishu:up`
  refuses without one.
- `/feishu:status` — is the bridge up? (discovered via `/proc`, not just the pid file) +
  last channel log lines.
- `/feishu:stop` — stop the bridge: discover the real bridge pids via `/proc`, SIGTERM
  (grace) then SIGKILL, and reap any orphan keeper.
- `/feishu:mode plan|auto|acceptEdits|bypassPermissions` — restart the bridge in a new
  permission mode, **resuming the same session** (conversation continuity preserved).
  Waits for the old bridge to fully exit first, so the resumed session never collides
  with a still-living one.
- `/feishu:doctor` — validate creds, allowlist, and the websocket connect; fails loudly
  with the exact fix (e.g. missing scope / event subscription).

Under the hood these run `python -m mcp_channel.launcher <cmd>`. The bridge (B) is a
separate, detached session — it keeps running independently of the session that launched
it; channel output is teed to `/tmp/feishu-channel.log`. See `docs/bridge-onboarding/`
for the full design.

## Run

Register the channel via the local **marketplace**, then launch a session with the
channel enabled. From the repo root, inside a `claude` session:

```
/plugin marketplace add .            # registers the 'feishu-local' marketplace (.claude-plugin/marketplace.json)
/plugin install feishu@feishu-local  # installs the feishu plugin
```

Then start Claude Code with the channel enabled (it spawns the MCP server as a
child process and connects the Feishu websocket for the life of the session):

```bash
claude --channels plugin:feishu@feishu-local
```

Inbound Feishu messages (from allowlisted senders) now appear in that Claude
session as user turns; Claude replies by calling the channel's `reply` tool.

## Local dev mode (`server:feishu`)

The channel-plugin **allowlist** (Anthropic-maintained) currently blocks
plugin-installed channels for organization accounts, so the working path today
is **local dev mode**, which bypasses the allowlist:

1. Make the venv python reachable by the project `.mcp.json`. For dev mode,
   `${CLAUDE_PLUGIN_ROOT}` isn't set, so point it at your venv (one-liner):
   ```bash
   .venv/bin/python - <<'PY'
   import json, pathlib
   py = str(pathlib.Path(".venv/bin/python").resolve())
   pathlib.Path(".mcp.json").write_text(json.dumps(
       {"mcpServers": {"feishu": {"command": py, "args": ["-m", "mcp_channel"]}}, indent=2))
   PY
   ```
   (This is a local, uncommitted override — the repo keeps the portable
   `${CLAUDE_PLUGIN_ROOT}/...` form above.)
2. Allow the tools (one-time, in `~/.claude/settings.json`):
   ```json
   "permissions": { "allow": ["mcp__feishu__reply", "mcp__feishu__react"] }
   ```
3. Launch (always exit the previous session first, so no orphan lingers):
   ```bash
   claude --dangerously-load-development-channels server:feishu
   ```
   Channel logs tee to `/tmp/feishu-channel.log`. Wait ~1-2 min for
   `connected to wss://msg-frontier.feishu.cn/…`, then DM the bot.

## Access control

Without `FEISHU_ALLOWED_OPEN_IDS` / `FEISHU_ALLOWED_CHAT_IDS` set, the bot answers
**anyone** who can reach the Feishu app. Set at least one allowlist in `.env` for
anything beyond personal testing.

## Layout

- `mcp_channel/` — the MCP channel server (capabilities, tools, websocket ingest,
  drain loop, allowlist).
- `feishu_api.py` — Feishu REST client + send/react actions (reused by the tools).
- `.mcp.json` + `.claude-plugin/plugin.json` + `.claude-plugin/marketplace.json` — register the server as a local channel plugin (`feishu@feishu-local`).
- `tests/stdio_smoke.py` — offline handshake smoke test.
- `docs/mcp-bridge/` — PRD, task list, implementation log.

## Test (offline)

```bash
.venv/bin/python tests/stdio_smoke.py
# asserts: claude/channel capability advertised, reply+react tools present, tool dispatch works.
# note: cold startup is slow (~1-2 min) because importing lark_oapi off the
# /mnt/c drvfs is slow on WSL; put the venv on native Linux FS to fix that.
```

## Notes / limitations (v1)

- Text only (no image/file attachments); one `reply` per turn (no streaming card).
- Static allowlist only (no pairing flow); no permission relay to Feishu.
- Always-on requires keeping the `claude --channels` session running.
