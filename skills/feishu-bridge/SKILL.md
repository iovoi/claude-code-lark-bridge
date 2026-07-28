---
name: feishu-bridge
description: Set up and run the Feishu/Lark <-> Claude Code bridge end-to-end. Trigger when the user asks to "set up", "run", "start", "bring up", "connect", or "launch" the Feishu/Lark bridge, or says they want to chat with Claude from Feishu/Lark, or to "stop"/"check"/"switch mode" of an already-running bridge. Gathers credentials, writes .env, launches the detached headless bridge via the launcher, and reports how to use it. Do NOT trigger for general chat unrelated to the bridge.
---

# Feishu bridge — set up & run (agent-driven)

You bring up (or manage) the Feishu/Lark bridge for the user. Do ALL of this yourself;
the user only provides credentials/choices when asked. Never make the user type the launch
commands manually.

Repo (run all commands from here): `{{REPO}}`
(after install.py: `~/.chat_bridge/claude-code-lark-bridge`. During local dev: the
checkout you are in.) `{{PY}}` is the bridge's Python — on an installed setup the venv
interpreter (`~/.chat_bridge/venv/.../python`), during local dev your `python3`. The
installer substitutes both tokens for you.

## To SET UP / RUN the bridge (user says "set up/run/start/bring up/connect the bridge")
1. **Preflight** (report what is missing):
   - `{{PY}} --version` (>=3.10). `uv --version` (if missing, install: `curl -LsSf https://astral.sh/uv/install.sh | sh` on POSIX; `winget install astral-sh.uv` on Windows).
   - `claude --version` (if missing, tell the user to install Claude Code first).
   - Note: `install.py` provisions the management venv (launcher/doctor deps) and `uvx`
     fetches the MCP *server* deps at runtime — no manual `pip install` is needed.
2. **Gather credentials** from the user (they offered to provide them): `FEISHU_APP_ID`
   (starts with `cli_`) and `FEISHU_APP_SECRET`. Ask whether they want an allowlist
   (their Feishu `open_id`). If they do not know their open_id, proceed with `--mode auto`
   (no allowlist needed) and offer to find it afterward.
3. **Write `.env`** in the repo (merge; never overwrite keys the user did not provide;
   never echo the secret back):
   ```
   FEISHU_APP_ID=...
   FEISHU_APP_SECRET=...
   FEISHU_ALLOWED_OPEN_IDS=...   # only if provided
   ```
4. **Launch** (this takes ~2-4 min on a slow /mnt/c or first-run uvx resolve — wait
   patiently for the line `bridge UP (pid ...); Feishu websocket connected.`). The
   installed wrapper picks the OS-correct venv interpreter for you:
   - POSIX / WSL:  `{{REPO}}/run-bridge.sh`            (defaults to `--mode auto`; bypass via `-- --mode bypassPermissions`)
   - native Windows: `{{REPO}}/run-bridge.bat`         (same flags after `--`)
   - or directly:  `{{PY}} -m mcp_channel.launcher up`              (auto, default)
                   `{{PY}} -m mcp_channel.launcher up --mode auto`  (no allowlist)
5. **Report**: "The bridge is UP. DM your bot on Feishu/Lark — your messages reach Claude
   here, and Claude replies (with OnIt->Done emoji) back to Feishu." Mention how to
   stop/check later. If `up` failed, run `{{PY}} -m mcp_channel.doctor` and report its
   PASS/FAIL lines (e.g. missing scope / event subscription). The log lives at
   `$TMPDIR/feishu-channel.log` (or `%TEMP%\feishu-channel.log` on Windows), overridable
   via `FEISHU_CHANNEL_LOG`.

## To MANAGE an already-running bridge (user says "stop" / "is it up" / "switch to plan mode")
Run the corresponding launcher command and report the output (use `{{PY}}`, or on Windows
the `.bat` / venv python so the deps resolve):
- stop:        `{{PY}} -m mcp_channel.launcher stop`
- status:      `{{PY}} -m mcp_channel.launcher status`
- switch mode: `{{PY}} -m mcp_channel.launcher mode plan|auto|acceptEdits|bypassPermissions`
- diagnose:    `{{PY}} -m mcp_channel.doctor`

## Before YOU exit (only if YOU launched the bridge via this skill)
If **you (the agent)** launched the bridge in step 4, then before you stop / the session ends,
run `{{PY}} -m mcp_channel.launcher stop` to tear it down cleanly — no orphan. This applies
**only** when the bridge was started by you via this skill; if the user launched it manually
(they ran `run-bridge.*` themselves), leave it running.

## Notes
- The bridge (B) is a separate DETACHED session; it keeps running after this session ends.
  `stop` tears it down (no orphan).
- If bypass mode: an allowlist is mandatory (the trust boundary); `up` refuses without one.
- To find the user open_id for the allowlist: set a placeholder, `up`, have them DM, then
  `grep access "$TMPDIR"/feishu-channel.log` (or the `FEISHU_CHANNEL_LOG` path) shows
  `denied ... user=ou_...`.
