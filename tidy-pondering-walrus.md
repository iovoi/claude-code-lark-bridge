# Bridge watchdog: liveness monitoring + stuck-prompt forwarding

## Context

The bridge runs a headless `claude` session inside a PTY. When Claude hits an
**interactive prompt** mid-task (onboarding/tips screen, plan-mode confirmation,
MCP/trust dialog, a Yes/No menu), the session silently wedges: the `OnIt` emoji
stays forever and the user has no idea nothing is progressing. Even when Claude
*is* working on a long task, the user gets **zero feedback** until the final
reply. This actually happened: a task sat behind a "Type something / Enter to
select" tips screen until we killed the bridge.

Goal: a watchdog that (1) reports progress to the user every ~1 min while Claude
is genuinely working, and (2) when Claude is stuck on an interactive prompt,
forwards the prompt to the user via Feishu and lets the user **reply with a
keystroke** that the bridge types into the PTY to un-stick it.

### Key architectural fact (drives the design)

The **keeper** (`_keeper_posix` / `_keeper_windows` in `mcp_channel/launcher.py`)
is the *only* component that sees Claude's raw TTY stream — it owns the PTY
master, already runs a 1-second `select` loop, already tees output to `LOG_PATH`,
and already parses output for two boot dialogs. The MCP server (`server.py`)
cannot detect "stuck at prompt" — it only sees tool calls and inbound messages,
never Claude's screen. **The watchdog therefore lives in the keeper.** The keeper
can also *write keystrokes* to the PTY master (`os.write(master, …)`), which is
exactly what un-sticking requires.

### Decisions (confirmed with user)
- **Stuck recovery = notify + keystroke forwarding.** Forward the stuck screen,
  and let the user reply from Feishu; the bridge types the reply into the PTY.
- **Surface every task-time prompt.** No new auto-dismiss. The two *boot-critical*
  dialogs (dev-channels confirm, bypass-accept) stay auto-answered because `up()`
  cannot wait for a human — it polls for `connected to wss` and times out at 240s.
  Surfacing those would make the bridge unable to start unattended. Only prompts
  that occur **while a task is active** are surfaced.

## Design

### Cross-process state (new module `mcp_channel/bridgestate.py`)

The keeper and the MCP server are separate processes and need to coordinate.
Avoid shared mutable files (write contention) by giving **each file a single
writer**. All under `STATE_DIR` (= `FEISHU_BRIDGE_STATE` or `~/.feishu-bridge`,
same expression as `launcher.py:35`).

| file | writer | reader | purpose |
|---|---|---|---|
| `bridge.active.json` | **server** | keeper | active task: `chat_id`, `message_id`, `pushed_at`, `content_preview` |
| `bridge.stuck.json` | **keeper** | server | watchdog state: `awaiting_keystroke` (bool), `alerted` (bool), `stuck_screen` (str), `updated_at` |
| `bridge.keystrokes.json` | **server** (append) | keeper (drain) | queue of pending keystrokes: `[{"seq":N,"text":"…"}]` |

Reuse `feishu_api.atomic_write_json` / `feishu_api.read_json` (feishu_api.py:107-134)
for all writes — atomic, fsync, `os.replace`. `bridgestate.py` exposes thin
helpers (`read_active()`, `write_active()`, `read_stuck()`, `write_stuck()`,
`push_keystroke()`, `drain_keystrokes()`). No new dependency.

### The watchdog (new module `mcp_channel/watchdog.py`)

Pure-Python class `Watchdog`, no Feishu import — emits *actions* so it is unit-
testable offline. The keeper wires actions to a sender (see below).

State it tracks:
- `last_activity_at` — updated on every `feed(data)` (any non-empty PTY chunk).
- `last_progress_sent_at` — for the 60s progress cadence.
- rolling ring buffer of the last ~4 KB of **cleaned** (ANSI-stripped) output,
  for progress/stuck message snippets.

Config (read via `feishu_api.cred()`, so `.env` works; defaults shown):
- `FEISHU_WATCHDOG=1` — master on/off.
- `FEISHU_WATCHDOG_PROGRESS_SECS=60` — progress interval while working.
- `FEISHU_WATCHDOG_STUCK_QUIET_SECS=25` — idle threshold → suspect stuck.

API:
- `feed(data: bytes, now: float)` — keeper calls this on every PTY chunk.
- `tick(active, now: float) -> list[Action]` — keeper calls each loop iteration
  (≈1s). `active` is the parsed `bridge.active.json` (or `None`). Returns 0..n
  actions: `SendProgress(chat_id, text)` / `SendStuck(chat_id, screen_text)` /
  `ClearStuck()` (internal flag management) / `SetAwaitingKeystroke(screen)`.

Tick logic (only acts when `active` is present — i.e. a task is in flight):
1. Scan the recent-output buffer for **WAITING markers** (curated phrases from
   Claude's interactive prompts): `Enter to select`, `Tab/Arrow`, `Esc to cancel`,
   `Enter to confirm`, `shift+tab to cycle`, `to navigate`, `Yes,Iaccept`,
   `No,exit`, ` Yes`, ` No,`. (Kept as a module-level list, easy to extend.)
2. If a WAITING marker is present **and not already `alerted`**:
   → emit `SendStuck` + `SetAwaitingKeystroke(cleaned_screen)`, set `alerted`.
3. Else if no marker but `now - last_activity_at > STUCK_QUIET_SECS` **and not
   `alerted`** (idle wedge with no recognizable prompt):
   → emit `SendStuck`("no output for Xs — may be stuck") + `SetAwaitingKeystroke`,
   set `alerted`.
4. Else (presumed working) **and not awaiting keystroke**: if
   `now - last_progress_sent_at >= PROGRESS_SECS` → emit `SendProgress` (elapsed
   since `pushed_at` + a ≤500-char cleaned snippet), update `last_progress_sent_at`.
5. On resumed activity (`feed` received bytes) **after `alerted`** → clear
   `alerted` (so it can re-alert if it sticks again). The keeper clears
   `awaiting_keystroke` when it applies a keystroke.

Screen cleaning for messages: strip ANSI (reuse the keeper's existing regex at
`launcher.py:340`), collapse runs of whitespace, drop lines that are only spinner
glyphs, take the last ~500 chars.

### Keystroke forwarding (reply → PTY)

When `SendStuck` fires, the keeper sets `awaiting_keystroke=true` in
`bridge.stuck.json` and sends the user a Feishu message like:

> ⛔ Claude is waiting for input (looks stuck). Last screen:
> ```
> <cleaned screen — the menu/options/question>
> ```
> Reply with your answer — it will be typed into the prompt + Enter.
> Special keys: `enter` `esc` `tab` `up` `down` `y` `n`.

The user replies in Feishu. Inbound flow change in `server.py` (`on_message` /
`drain`, server.py:218-238): **before** queueing the event as a Claude turn,
check `bridge.stuck.json`. If `awaiting_keystroke` is true **and** the sender is
allowed (`access.allowed`, already enforced at server.py:195) → do **not** push as
a turn; instead append `{"text": <user reply>}` to `bridge.keystrokes.json`
(via `bridgestate.push_keystroke`) and return. Otherwise, normal push.

The keeper drains `bridge.keystrokes.json` each tick. For each entry:
- Map special tokens → bytes: `enter`→`\r`, `esc`→`\x1b`, `tab`→`\t`,
  `up`→`\x1b[A`, `down`→`\x1b[B`, `y`→`y\r`, `n`→`n\r`.
- Default → `<text>\r` (type verbatim + Enter).
- `os.write(master, payload)` (POSIX) / `pty_obj.write(payload)` (Windows).
- After applying: clear `awaiting_keystroke`, clear the queue entry. Resumed PTY
  activity clears `alerted`.

### Never block the keeper loop on Feishu

`feishu_api.send_text` imports `lark_oapi` lazily (~100 s on this WSL drvrs the
first call). That must not stall the 1s `select` loop (would buffer/drop Claude
output). So the keeper runs a **dedicated sender daemon thread** with a
`queue.Queue`. The watchdog's `tick` enqueues outgoing messages; the sender
thread drains and calls `feishu_api.send_text`. The expensive first import happens
once, off the main loop. Sends are rare (≤1/min + on-stuck), so one thread is
plenty.

### Active-task lifecycle (server.py changes)

- `_push` (server.py:190): after `_stamp_working`, call
  `bridgestate.write_active({chat_id, message_id, pushed_at: now, content_preview})`.
- `_finish_working` (server.py:69): on successful reply, call
  `bridgestate.clear_active()` (task done → watchdog stops monitoring that task).
- This is the watchdog's only source of truth for "is a task in flight" — no log
  scraping.

## Files

- **NEW `mcp_channel/bridgestate.py`** — `STATE_DIR` + the three state files +
  read/write/drain helpers (wraps `feishu_api.atomic_write_json` / `read_json`).
- **NEW `mcp_channel/watchdog.py`** — `Watchdog` class (`feed`/`tick`), action
  dataclasses, WAITING-marker list, screen-cleaner, message composers.
- **MODIFY `mcp_channel/launcher.py`** — in `_keeper_posix` and `_keeper_windows`:
  instantiate `Watchdog`; `feed(data)` on each chunk; `tick(read_active(), now)`
  each loop; sender daemon thread + queue; drain `bridge.keystrokes.json` and
  write mapped bytes to the PTY master; on keystroke applied, clear
  `awaiting_keystroke`. Add an `ansi` clean helper (already present at :340 /
  :273 — reuse).
- **MODIFY `mcp_channel/server.py`** — `write_active` in `_push`, `clear_active`
  in `_finish_working`, keystroke-intercept in the inbound path
  (`on_message`/`drain`) when `awaiting_keystroke`.
- **NEW `tests/test_watchdog.py`** — offline unit tests (style of
  `tests/test_launcher_session.py`): feed simulated PTY chunks and assert the
  emitted actions. Cases: working→progress at 60s; WAITING marker→stuck (once,
  not repeated); idle>threshold→stuck; resumed activity clears `alerted` and
  re-alerts on re-stick; no active task → no actions; keystroke token→byte
  mapping.

## Config additions (`.env`, optional — all have safe defaults)
```
FEISHU_WATCHDOG=1
FEISHU_WATCHDOG_PROGRESS_SECS=60
FEISHU_WATCHDOG_STUCK_QUIET_SECS=25
```

## Verification

1. **Unit tests:** `python3 tests/test_watchdog.py` — must print `WATCHDOG OK`.
   Also re-run existing: `python3 tests/test_launcher_session.py` and
   `python3 tests/test_launcher_discover.py` to confirm no regression.
2. **Offline dry-run of detection:** a tiny script that constructs a `Watchdog`,
   feeds it a recorded stuck-screen chunk (from `/tmp/feishu-channel.log`) and
   asserts a `SendStuck` action with the cleaned screen — proves the cleaner +
   marker match work against the real incident output.
3. **End-to-end live (via tmux pane, per workspace rules):**
   - `python3 -m mcp_channel.launcher up` → confirm `bridge UP`.
   - DM the bot a long task (e.g. "read every file and summarize") → confirm a
     **progress** message arrives ~60s later and every 60s while working.
   - Force a stuck prompt: DM a task that triggers an interactive prompt, or
     temporarily lower `STUCK_QUIET_SECS` and pause output → confirm a **stuck**
     alert arrives with the screen content.
   - Reply to the stuck alert with `enter` (or an option) → confirm the bridge
     types it, Claude proceeds, and a follow-up progress/reply appears.
   - Confirm `bridge.active.json` is written on push and cleared on reply;
     `bridge.stuck.json` toggles `awaiting_keystroke`; `bridge.keystrokes.json`
     drains to empty.
   - `python3 -m mcp_channel.launcher stop` → confirm clean teardown (no orphan
     keeper/sender thread), and that re-`up` works.
4. **No regression to the happy path:** a simple "Hi" still gets a single reply
   with OnIt→Done and **no** spurious progress/stuck messages.
