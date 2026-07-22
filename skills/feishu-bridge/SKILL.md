---
name: feishu-bridge
description: Set up and run the Feishu/Lark <-> Claude Code bridge end-to-end. Trigger when the user asks to "set up", "run", "start", "bring up", "connect", or "launch" the Feishu/Lark bridge, or says they want to chat with Claude from Feishu/Lark, or to "stop"/"check"/"switch mode" of an already-running bridge. Gathers credentials, writes .env, launches the detached headless bridge via the launcher, and reports how to use it. Do NOT trigger for general chat unrelated to the bridge.
---

# Feishu bridge — set up & run (agent-driven)

You bring up (or manage) the Feishu/Lark bridge for the user. Do ALL of this yourself;
the user only provides credentials/choices when asked. Never make the user type the launch
commands manually.

Repo (run all commands from here): `/mnt/c/Users/wade/Desktop/workspace/lark-feishu-integration`

## To SET UP / RUN the bridge (user says "set up/run/start/bring up/connect the bridge")
1. **Preflight** (report what is missing):
   - `python3 --version` (>=3.10). `uv --version` (if missing, install: `curl -LsSf https://astral.sh/uv/install.sh | sh`).
   - `claude --version` (if missing, tell the user to install Claude Code first).
   - Note: no `pip install` is needed — `uvx` fetches the server deps at runtime.
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
4. **Launch** (this takes ~2-4 min on a WSL /mnt/c disk — wait patiently for the line
   `bridge UP (pid ...); Feishu websocket connected.`):
   - allowlist provided: `python3 -m mcp_channel.launcher up`            (bypass, default)
   - no allowlist:        `python3 -m mcp_channel.launcher up --mode auto`
5. **Report**: "The bridge is UP. DM your bot on Feishu/Lark — your messages reach Claude
   here, and Claude replies (with OnIt->Done emoji) back to Feishu." Mention how to
   stop/check later. If `up` failed, run `python3 -m mcp_channel.doctor` and report its
   PASS/FAIL lines (e.g. missing scope / event subscription).

## To MANAGE an already-running bridge (user says "stop" / "is it up" / "switch to plan mode")
Run the corresponding launcher command and report the output:
- stop:        `python3 -m mcp_channel.launcher stop`
- status:      `python3 -m mcp_channel.launcher status`
- switch mode: `python3 -m mcp_channel.launcher mode plan|auto|acceptEdits|bypassPermissions`
- diagnose:    `python3 -m mcp_channel.doctor`

## Notes
- The bridge (B) is a separate DETACHED session; it keeps running after this session ends.
  `stop` tears it down (no orphan).
- If bypass mode: an allowlist is mandatory (the trust boundary); `up` refuses without one.
- To find the user open_id for the allowlist: set a placeholder, `up`, have them DM, then
  `grep access /tmp/feishu-channel.log` shows `denied ... user=ou_...`.
