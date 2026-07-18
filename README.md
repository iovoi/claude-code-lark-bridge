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

## Run

Register the channel as a Claude Code plugin (from the repo root), then launch a
session with the channel enabled:

```bash
claude plugin install .                 # registers the `feishu` plugin (.claude-plugin/ + .mcp.json)
claude --channels plugin:feishu         # start Claude Code with the Feishu channel
```

Inbound Feishu messages (from allowlisted senders) now appear in that Claude
session as user turns; Claude replies by calling the channel's `reply` tool.

## Access control

Without `FEISHU_ALLOWED_OPEN_IDS` / `FEISHU_ALLOWED_CHAT_IDS` set, the bot answers
**anyone** who can reach the Feishu app. Set at least one allowlist in `.env` for
anything beyond personal testing.

## Layout

- `mcp_channel/` — the MCP channel server (capabilities, tools, websocket ingest,
  drain loop, allowlist).
- `feishu_api.py` — Feishu REST client + send/react actions (reused by the tools).
- `.mcp.json` + `.claude-plugin/plugin.json` — register the server as a channel.
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
