# Feishu/Lark ↔ Claude Code Bridge

A passive, event-driven bridge that lets you chat with **Claude Code** from
**Feishu / Lark**. You send a message to the bot in Feishu; the bot pastes it into
the Claude Code session running in a local tmux pane, scrapes Claude's reply, and
posts it back to Feishu — with emoji reactions showing progress.

```
 Feishu chat ──(WebSocket long-connection)──▶ bot.py ──(tmux send-keys)──▶ Claude Code pane
      ▲                                            │
      └──────────── reply text ◀──(scrape markers)─┘
```

Claude Code replies are wrapped in two markers that the bot looks for:

```
REPLY_FeiShu_Msg:<corrid>
  … the reply body …
READY4NextMsg:<corrid>
```

The `lark-feishu-bot` Claude Code skill is responsible for emitting those markers,
so this bridge only works when that skill is installed in the Claude Code session.

---

## What each file does

| File | Role |
|------|------|
| `bot.py` | **Main process.** Opens the WebSocket long-connection to Feishu, receives messages, applies a single-flight busy lock, pastes prompts into Claude's tmux pane, polls for the reply markers, and sends the reply + emoji reactions back. |
| `feishu_api.py` | Shared helpers: `.env` loader, config constants, Feishu REST calls (send text, add/delete reactions), atomic JSON ledger I/O, and the TUI-scrape helpers (`claude_is_busy`, `clean_line`). |
| `bridgectl.py` | Control script. `init` detects the Claude Code tmux pane, binds it in `.env`, and (re)starts the bot in a detached tmux session. `status` shows bindings + bot health. |
| `deliver_to_claude.py` | Standalone/legacy single-message deliverer (the same paste→poll→finalize flow as a child process). `bot.py` now does this in-process; this script remains as an alternate path. |
| `mark_handled.py` | Mark a message_id as handled (ledger helper). |
| `pending_messages.py` | List unreplied messages from `conversation/*.jsonl` (`--seed` marks all current ones handled). |
| `react.py` | Stamp/remove emoji reactions on a message. |

Runtime state lives under `conversation/` (JSON ledgers + one `.jsonl` per chat).
That directory is gitignored because it contains real message content and chat ids.

---

## Prerequisites

- **Python 3.10+** (developed/tested on 3.14)
- **tmux** — the bridge drives Claude Code through tmux panes
- **Claude Code** CLI, logged in, with the `lark-feishu-bot` skill installed
- A **Feishu/Lark account** with access to the Developer Console

---

## 1. Install dependencies

```bash
cd lark-feishu-integration

# create a virtualenv (any Python 3.10+)
python -m venv .venv

# install the pinned dependencies
.venv/bin/pip install -r requirements.txt
```

## 2. Create the Feishu app

1. Open the [Feishu Developer Console](https://open.feishu.cn/app) and create a
   **Custom App** (企业自建应用).
2. Under **Credentials & Basic Info**, copy the **App ID** (`cli_…`) and
   **App Secret**.
3. Under **Permissions & Scopes**, add at least:
   - `im:message` (read messages)
   - `im:message:send_as_bot` (send messages as the bot)
   - `im:message.reactions:write` (add/remove emoji reactions)
   - `im:chat` (resolve chats)
4. Under **Add capabilities / Bot**, enable the **Bot** capability so it can be
   messaged.
5. Under **Event Subscriptions**, switch to **Long Connection (长连接)** mode
   (NOT the webhook mode) and subscribe to the event:
   - `im.message.receive_v1`
6. Publish the app / version and add the bot to the chat(s) you want it to serve.

> The bridge uses Feishu's **WebSocket long-connection** for events, so no public
> webhook URL or inbound port is required — the bot connects out to Feishu.

## 3. Configure `.env`

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:

```
FEISHU_APP_ID=cli_your_real_app_id
FEISHU_APP_SECRET=your_real_app_secret
```

`.env` is gitignored. Leave `FEISHU_CLAUDE_PANE` for `bridgectl.py init` to fill in.

## 4. tmux + Claude Code setup (important)

The bot pastes prompts into the tmux pane where Claude Code is running, so Claude
must live inside tmux.

```bash
# 1. start a named tmux session
tmux new -s claude

# 2. inside tmux, start Claude Code
claude

# 3. (in any shell — can be inside or outside tmux) bind the bot to the Claude
#    pane and start the bot in its own detached tmux session
.venv/bin/python bridgectl.py init
```

`bridgectl.py init` does three things:

1. finds the pane whose current command is `claude` (no hardcoded pane id);
2. writes that pane id to `.env` as `FEISHU_CLAUDE_PANE`;
3. (re)starts the bot in a detached tmux session named **`feishu-bot`** so it
   survives Claude session restarts.

Verify everything is wired up:

```bash
.venv/bin/python bridgectl.py status
```

You should see the Claude pane detected, `.env` pane matches, and `bot running: True`.

**Notes about tmux:**
- The bot runs in the `feishu-bot` session. Attach to read its live log with
  `tmux attach -t feishu-bot` (detach with `Ctrl-b d` — do NOT kill the pane).
- If you restart Claude Code or switch panes, just re-run
  `bridgectl.py init` to re-bind and restart the bot.
- `init` is idempotent and safe to run at the start of every session.
- If no tmux server is running, start one first (`tmux new -s claude`) — the
  bridge cannot work without tmux.

## 5. Send a message

Message the bot directly in Feishu (p2p) or @-mention it in a group. You should
see the working emoji react, then the reply, then the done emoji.

**In-chat commands** (sent as normal messages, handled by the bot without Claude):

| Command | Effect |
|---------|--------|
| `command help` | Show help text |
| `command kill` | Interrupt Claude's current task and return to ready |
| `command mode plan\|auto\|manual` | Switch Claude Code permission mode (cycles Shift+Tab) |

---

## Troubleshooting

- **`ERROR: set FEISHU_APP_ID and FEISHU_APP_SECRET`** — `.env` is missing or the
  values are empty. Run `cp .env.example .env` and fill them in.
- **Bot not receiving messages** — confirm the app uses **Long Connection** mode,
  `im.message.receive_v1` is subscribed, the app version is published, and the bot
  is added to the chat. Check the bot log: `tmux attach -t feishu-bot`.
- **Reactions don't appear** — Feishu emoji codes vary by workspace. Edit
  `FEISHU_EMOJI_WORKING` / `FEISHU_EMOJI_DONE` in `.env` to a code known to work
  (e.g. `THUMBSUP`, `OK`, `THINKING`) and restart with `bridgectl.py init`.
- **`(Claude did not pick up the message …)`** — the prompt was pasted but no reply
  markers appeared. Ensure the `lark-feishu-bot` skill is installed in Claude Code
  and Claude was idle at the time.
- **`could not confirm switch to … mode`** — Shift+Tab isn't mapped in this
  terminal; switch modes manually in Claude Code.
- **Claude pane mismatch** — run `bridgectl.py init` again after restarting Claude
  or rearranging panes.

---

## Project layout

```
lark-feishu-integration/
├── bot.py                 # main ingest + orchestrator (WebSocket client)
├── feishu_api.py          # shared REST/config/scrape helpers
├── bridgectl.py           # bind-to-pane + start/stop bot
├── deliver_to_claude.py   # standalone single-message deliverer
├── mark_handled.py        # ledger helper
├── pending_messages.py    # list unreplied messages
├── react.py               # emoji reaction helper
├── requirements.txt       # pinned Python dependencies
├── .env.example           # copy to .env and fill in (gitignored)
├── .env                   # your real credentials — NOT committed
└── conversation/          # runtime state + message logs (gitignored)
```

## License

Specify your license here (e.g. MIT). All rights reserved by default.
