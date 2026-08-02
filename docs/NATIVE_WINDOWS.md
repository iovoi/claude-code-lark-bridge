# Native Windows support

The bridge runs on **native Windows** (Windows 10/11, no WSL required). All
native-Windows code paths are gated on `os.name == "nt"`; the POSIX/WSL paths
are unchanged.

> WSL2 (`install.sh` inside Ubuntu) remains fully supported — this document is
> the **native Windows** path.

## Requirements

- **Windows 10 or 11.**
- **Python 3.10+** (the installer auto-locates a suitable interpreter via the
  `py` launcher if the default `python` is older).
- **Claude Code** (`claude`) on `PATH`.
- A **Feishu/Lark Custom App** (see [Feishu app setup](#feishu-app-setup)).

## Install

PowerShell:
```powershell
irm https://raw.githubusercontent.com/iovoi/claude-code-lark-bridge/main/install.py | python
```
cmd:
```cmd
curl -fsSL https://raw.githubusercontent.com/iovoi/claude-code-lark-bridge/main/install.py | python
```
Or clone and run `install.ps1` / `install.bat`. Everything installs under
`%USERPROFILE%\.chat_bridge` (repo + venv + `uv` + the run skill).

The installer creates the venv, installs the bridge package (editable, with the
`[windows]` extra so `pywinpty` is present), writes `.mcp.json` pointing the
`feishu` MCP server at the venv's `python -m mcp_channel`, and installs the
`feishu-bridge` run skill into the agent.

## Configure

Edit `%USERPROFILE%\.chat_bridge\claude-code-lark-bridge\.env`:

| Variable | Required | Purpose |
|----------|----------|---------|
| `FEISHU_APP_ID` | yes | Feishu app id (`cli_…`) |
| `FEISHU_APP_SECRET` | yes | Feishu app secret |
| `FEISHU_ALLOWED_CHAT_IDS` | recommended | Comma-separated chat ids the bot will answer |
| `FEISHU_ALLOWED_OPEN_IDS` | recommended | Comma-separated sender open ids |

Without an allowlist the bot answers **anyone** who can reach the Feishu app.

Validate credentials + websocket connectivity:
```cmd
%USERPROFILE%\.chat_bridge\venv\Scripts\python.exe -m mcp_channel.doctor
```

## Run

### Interactive (you accept the dev-channel prompt; terminal stays open)
```cmd
cd /d %USERPROFILE%\.chat_bridge\claude-code-lark-bridge
claude --dangerously-load-development-channels server:feishu
```
Accept the workspace-trust / MCP-server / dev-channel prompts, then DM the bot.

### Daemon (detached, set-and-forget)
```cmd
%USERPROFILE%\.chat_bridge\venv\Scripts\python.exe -m mcp_channel.launcher up --mode bypassPermissions
```
`bypassPermissions` (fully autonomous, no per-action prompts — recommended for an
unattended daemon) **requires an allowlist**; `up` refuses without one. Other
modes: `auto`, `acceptEdits`, `plan`. The daemon survives the launching shell
exiting but **not a reboot** — re-run `up` after a restart, or add it to a logon
startup entry.

### Manage
```cmd
%USERPROFILE%\.chat_bridge\venv\Scripts\python.exe -m mcp_channel.launcher status
%USERPROFILE%\.chat_bridge\venv\Scripts\python.exe -m mcp_channel.launcher stop
%USERPROFILE%\.chat_bridge\venv\Scripts\python.exe -m mcp_channel.launcher mode auto
%USERPROFILE%\.chat_bridge\venv\Scripts\python.exe -m mcp_channel.doctor
```

### Logs
`%TEMP%\feishu-channel.log`. Look for `[ws] on_receive FIRED` (a message
arrived), `[push] …` (handed to Claude), `[tool] reply … -> sent` (Claude
replied).

## Feishu app setup

In the Feishu Developer Console (open.feishu.cn) → your Custom App:

1. **Permissions**: add `im:message`, `im:message:send_as_bot`, `im:resource`.
2. **Events & Callbacks** → set delivery to **long-connection (websocket)** mode.
3. **Add event** `im.message.receive_v1`. Without this the websocket connects
   but **no messages arrive**.
4. **Version management** → create and **publish** a new version (event changes
   only take effect after publishing).
5. **Availability scope** → include the account(s) that will DM the bot.

## Headless startup (first-run prompts)

On first run Claude Code shows workspace-trust and MCP-server-approval prompts.
For the daemon (no human at the keyboard) the Windows PTY keeper confirms them:
workspace trust and MCP-server approval get **Enter** (option 1), and the
dev-channel/dangerous prompt gets **`2`** (accept). You can also pre-accept them
per-project in `~/.claude.json` (`hasTrustDialogAccepted`, and
`enabledMcpjsonServers: ["feishu"]`) so they don't appear at all.

## How it differs from WSL/POSIX

- `.mcp.json` uses the venv's `python -m mcp_channel` (not `uvx --extra windows`,
  which uv >=0.7's `uvx` rejects — it has no `--extra` flag).
- The keeper runs Claude inside a **pywinpty** PTY (no POSIX `pty`/`select`).
- Logs tee to `%TEMP%\feishu-channel.log` (not `/tmp/...`).

## What was fixed for native Windows

1. `install.py make_venv` — set `VIRTUAL_ENV` so `uv` installs into the fresh
   venv (it otherwise aborts "No virtual environment found").
2. `install.py configure_mcp` — write the venv-python `.mcp.json` on Windows
   (the `uvx --extra` form is invalid on uv >=0.7).
3. `launcher.py _winpty_spawn` — pass `env` to pywinpty as a NUL-separated
   string (pywinpty >=2 rejects a dict).
4. `launcher.py _keeper_windows_pty` — confirm the first-run workspace-trust and
   MCP-server dialogs; stop the `No,exit` misfire on the dangerous dialog.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `bridge did not start`, no `%TEMP%\feishu-channel.log` | Channel server didn't start — check `.mcp.json` points at the venv python (re-run the installer). |
| `connected to wss://...` but DMs do nothing | Subscribe `im.message.receive_v1` and publish a new app version. |
| `'dict' object is not an instance of 'str'` in the log | Old `_winpty_spawn` — update the repo. |
| Daemon starts then exits (`No,exit`) | Old keeper misfire — update the repo. |
| `bypassPermissions requires an allowlist` | Set `FEISHU_ALLOWED_CHAT_IDS` / `FEISHU_ALLOWED_OPEN_IDS` in `.env`, or use `--mode auto`. |
