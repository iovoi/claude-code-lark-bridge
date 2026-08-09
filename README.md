# Feishu/Lark ↔ Claude Code bridge (pipe bridge)

Chat with Claude Code from Feishu/Lark. The bridge drives Claude Code in **non-interactive
streaming mode** and relays the conversation over Feishu/Lark — no PTY, no tmux, identical
on Windows / Mac / Linux.

```
Feishu/Lark ──ws──▶ bridge ──stdin(NDJSON user turns)──▶ claude -p --input-format stream-json
            ◀──ws──        ◀──stdout(stream-json events + control protocol)──
                                OnIt→Done emoji · streaming card · approval cards · /stop
```

## How it works

- You DM the bot. The bridge stamps an **OnIt** reaction and sends your message to a
  long-lived `claude` subprocess (one per chat).
- Claude works autonomously — searching, editing, running commands. If a turn runs longer
  than 60s, a **"Working…" progress card** appears and updates every 30s (status + tool
  log only). Short turns send no card.
- When Claude wants a tool **not** on the auto-approve list, the bridge posts an
  **approval card** — **Approve / Approve all (turn) / Deny / Deny+stop** — and the turn
  pauses. Tap a button, or reply `approve` / `all` / `deny` / `stop` in chat. "Approve
  all (turn)" auto-approves the rest of that turn (re-confirms next turn).
- On completion the progress card flips to **"Done"** and the **result is delivered as a
  normal bot message**; the reaction swaps to **Done**.
- Conversation history + memory persist per chat (the same claude session is `--resume`d
  across turns and restarts). Send **`/stop`** (or the card's Stop button) to cancel a turn.

The agent layer is pluggable (`bridge/agent/`); Claude is implemented now.

## Prerequisites

- Python 3.10+
- A Feishu/Lark **Custom App** with `im:message` + `im:message:send_as_bot` + `im:resource`
  permissions and the **long-connection (websocket)** event mode enabled (Developer Console
  → Events & Callbacks), plus card-action events enabled for interactive buttons.
- Claude Code (`claude`) on PATH (must support `--input-format stream-json` and
  `--permission-prompt-tool`; validated live during bring-up).

## Setup

```bash
python -m venv .venv
uv pip install -e .           # or: .venv/bin/pip install -e .

cp .env.example .env
# edit .env: FEISHU_APP_ID, FEISHU_APP_SECRET, and (recommended) an allowlist
```

## Installation (one command)

Cross-platform installer (`install.py`) with thin wrappers (`install.sh`, `install.bat`,
`install.ps1`). Installs a venv + `uv`, the repo, and the `feishu-bridge` run skill under
`~/.chat_bridge/`.

**Linux / macOS / WSL2:**
```bash
curl -fsSL https://raw.githubusercontent.com/iovoi/claude-code-lark-bridge/main/install.py | python3
# or, after cloning:  ./install.sh
```
**Windows:**
```powershell
irm https://raw.githubusercontent.com/iovoi/claude-code-lark-bridge/main/install.py | python
# or:  .\install.ps1      (or:  install.bat)
```

After install, say **"run the feishu bridge"** in any Claude session (have your Feishu
APP_ID/SECRET ready), or launch directly: `~/.chat_bridge/claude-code-lark-bridge/run-bridge.sh`
(Linux/mac) / `run-bridge.bat` (Windows).

## Run / manage

```bash
feishu-bridge up        # start the detached bridge
feishu-bridge status    # is it running? (pid)
feishu-bridge stop      # stop it
feishu-bridge run       # foreground (debug); --no-ws to skip the websocket
```

Slash commands (in a Claude session with the plugin): `/feishu:up`, `/feishu:status`,
`/feishu:stop`, `/feishu:doctor`. Logs: `~/.chat_bridge/bridge.log`.

## Configuration (`.env`)

Credentials: `FEISHU_APP_ID`, `FEISHU_APP_SECRET`. Access control:
`FEISHU_ALLOWED_OPEN_IDS`, `FEISHU_ALLOWED_CHAT_IDS`. Behavior: `FEISHU_WORKDIR`,
`FEISHU_MAX_CONCURRENT`, `FEISHU_STUCK_TIMEOUT`, `FEISHU_APPROVAL_TIMEOUT`,
`FEISHU_AUTO_APPROVE_TOOLS`, `FEISHU_CARD_THROTTLE_MS`, `FEISHU_DEFAULT_PERMISSION_MODE`,
`FEISHU_EMOJI_WORKING` (`OnIt`), `FEISHU_EMOJI_DONE` (`Done`). See `.env.example`.

Without an allowlist, the bot answers **anyone** who can reach the app — set one for
anything beyond personal testing.

## Layout

- `bridge/` — the bridge: `transport.py` (subprocess + hand-rolled control protocol),
  `agent/` (`AgentAdapter`, `ClaudeAdapter`), `scope.py` (per-chat orchestration),
  `runtime.py` (loop + intake), `supervisor.py` (detached up/status/stop), `cards.py`,
  `approvals.py`, `ingest.py`, `lark.py`, `session_store.py`, `watchdog.py`, `config.py`.
- `feishu_api.py` — Feishu REST client (send/react/update + cards), reused by the bridge.
- `tests/` — unit + end-to-end tests, including `fake_claude.py` (a stub that speaks the
  streaming + control protocol) and `test_adapter_e2e.py`.
- `docs/pipe-bridge/` — PRD, task list, implementation log.

## Test (offline)

```bash
.venv/bin/python -m pytest tests/ -q
# exercises transport/control protocol, the adapter, scope orchestration, approvals,
# the supervisor, and a full adapter run against the fake-claude stub (no real API).
```

## Notes / limitations (v1)

- Text and rich-text (`post`) inbound; image/file attachments are not parsed.
- Topic-group threads are folded into scope identity (`chat_id:thread_id`) but render in the chat.
- **Tool approvals:** when Claude wants a tool off the auto-approve list, an interactive card with
  **Approve / Approve all (turn) / Deny / Deny+stop** buttons is posted. Tap a button, or reply in
  chat with `approve` / `all` / `deny` / `stop` (or y/n/1/2/3) — both work over the websocket
  long-connection. "Approve all (turn)" auto-approves subsequent tools in the same turn.
- **Progress vs result:** the progress card is a status indicator only; on completion it shows
  "Done" and the actual result is sent as a separate bot message. Interactive cards are updated
  via the Feishu **PATCH** endpoint (`message.patch`).
- Live-validated against real `claude` (print/streaming + hand-rolled control protocol) end-to-end.
