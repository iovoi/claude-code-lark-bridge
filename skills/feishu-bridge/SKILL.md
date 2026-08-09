---
name: feishu-bridge
description: Set up and run the Feishu/Lark <-> Claude Code bridge end-to-end. Trigger when the user asks to "set up", "run", "start", "bring up", "connect", or "launch" the Feishu/Lark bridge, or says they want to chat with Claude from Feishu/Lark, or to "stop"/"check" an already-running bridge. Gathers credentials, writes .env, starts the detached bridge via the `feishu-bridge` CLI, and reports how to use it. Do NOT trigger for general chat unrelated to the bridge.
---

# Feishu bridge — set up & run (agent-driven)

You bring up (or manage) the Feishu/Lark bridge for the user. Do ALL of this yourself;
the user only provides credentials/choices when asked. Never make the user type the launch
commands manually.

Repo (run all commands from here): `{{REPO}}`
(after install.py: `~/.chat_bridge/claude-code-lark-bridge`. During local dev: the
checkout you are in.) `{{PY}}` is the bridge's Python — on an installed setup the venv
interpreter (`~/.chat_bridge/venv/.../python`), during local dev your `python3`. The
installer substitutes both tokens for you. The CLI is `feishu-bridge` (installed into the
venv by `install.py`); in local dev use `{{PY}} -m bridge`.

## Architecture (one paragraph)
The bridge drives Claude Code in **non-interactive streaming mode**
(`claude -p --input-format stream-json --output-format stream-json`), one long-lived
process per chat. It hand-rolls the bidirectional control protocol so it can show
**Lark approval cards** (Approve / Approve all (turn) / Deny / Deny+stop) when Claude wants a
tool off the allowlist — tap a button or reply `approve`/`all`/`deny`/`stop` in chat; and a
**deferred "Working…" progress card** (only after 10s, status-only). No PTY, no tmux, identical on
Windows / Mac / Linux. On a message: stamp `OnIt` → run the turn → post the answer as a bot
message → swap to `Done`. Send `/stop` to cancel a turn. Conversation history + memory persist per chat
(claude session, `--resume`d across restarts).

## To SET UP / RUN the bridge (user says "set up/run/start/bring up/connect the bridge")
1. **Preflight** (report what is missing):
   - `{{PY}} --version` (>=3.10). `uv --version` (if missing: `curl -LsSf https://astral.sh/uv/install.sh | sh` on POSIX; `winget install astral-sh.uv` on Windows).
   - `claude --version` (if missing, tell the user to install Claude Code first).
   - Note: `install.py` provisions a venv with the `feishu-bridge` CLI + `lark-oapi`; no `mcp`/`pywinpty`.
2. **Gather credentials** from the user: `FEISHU_APP_ID` (starts with `cli_`) and
   `FEISHU_APP_SECRET`. Ask whether they want an allowlist (their Feishu `open_id`).
3. **Write `.env`** in the repo (merge; never overwrite keys the user did not provide;
   never echo the secret back):
   ```
   FEISHU_APP_ID=...
   FEISHU_APP_SECRET=...
   FEISHU_ALLOWED_OPEN_IDS=...   # only if provided
   ```
4. **Launch** (detached background process — no PTY/tmux):
   - POSIX / WSL / macOS / Linux: `{{REPO}}/run-bridge.sh`
   - native Windows: `{{REPO}}/run-bridge.bat`
   - or directly: `feishu-bridge up`  (local dev: `{{PY}} -m bridge` then `... up`, or `{{PY}} -m bridge run` for foreground/debug)
5. **Report**: "The bridge is UP. DM your bot on Feishu/Lark — your messages reach Claude
   here; Claude works on `FEISHU_WORKDIR`, shows a "Working…" card on long turns (>10s), asks for
   approval on risky tools, and replies as a bot message (OnIt→Done) in chat. Send `/stop` to cancel a
   turn." Logs: `~/.chat_bridge/bridge.log`.

## To MANAGE an already-running bridge (user says "stop" / "is it up?")
Run the CLI and report the output:
- start:  `feishu-bridge up`
- status: `feishu-bridge status`
- stop:   `feishu-bridge stop`
(local dev: `{{PY}} -m bridge <cmd>`)

## Before YOU exit (only if YOU launched the bridge via this skill)
If **you (the agent)** started the bridge in step 4, run `feishu-bridge stop` before the
session ends so no orphan lingers. This applies **only** when you started it; if the user
launched it manually, leave it running.

## Notes
- The bridge is a separate DETACHED process; it keeps running after this session ends.
  `stop` tears it down.
- The trust boundary is the allowlist (`FEISHU_ALLOWED_OPEN_IDS` / `FEISHU_ALLOWED_CHAT_IDS`)
  plus the tool-approval cards (tools off `FEISHU_AUTO_APPROVE_TOOLS` prompt in chat).
- To find a user's open_id: set a placeholder, `up`, have them DM, then the bridge log
  shows the denied `open_id`.
