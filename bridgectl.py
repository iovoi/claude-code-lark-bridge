#!/usr/bin/env python3
"""
Bridge control: bind the bot to the current Claude Code pane and (re)start the bot.

Usage (run from anywhere, via the venv or system python — stdlib only):
    .venv/bin/python bridgectl.py init     # detect Claude pane, update .env, restart bot
    .venv/bin/python bridgectl.py status   # show current bindings + bot health

init is idempotent and safe to run at the start of every Claude Code session (the lark-feishu-bot
skill runs it on load). It:
  1. confirms a tmux server is reachable;
  2. finds the pane whose current command is `claude` (Claude Code) — no hardcoded pane id;
  3. writes that pane id to .env as FEISHU_CLAUDE_PANE (so deliver_to_claude.py pastes into it);
  4. (re)starts the bot in a dedicated detached tmux session named `feishu-bot`, so it survives
     Claude session restarts and reads the freshly-bound pane.
"""

import os
import re
import subprocess
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
ENV_PATH = PROJECT_DIR / ".env"
VENV_PYTHON = str(PROJECT_DIR / ".venv" / "bin" / "python")
BOT_SCRIPT = str(PROJECT_DIR / "bot.py")
BOT_SESSION = "feishu-bot"


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def tmux_reachable() -> bool:
    if os.environ.get("TMUX"):
        return True
    return run(["tmux", "info"]).returncode == 0


def find_claude_pane():
    """Return the pane id of the Claude Code process, or None."""
    r = run(["tmux", "list-panes", "-a", "-F", "#{pane_id} #{pane_current_command}"])
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "claude":
            return parts[0]
    return None


def update_env_pane(pane: str) -> None:
    lines = []
    found = False
    if ENV_PATH.is_file():
        for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if raw.startswith("FEISHU_CLAUDE_PANE="):
                lines.append(f"FEISHU_CLAUDE_PANE={pane}")
                found = True
            else:
                lines.append(raw)
    if not found:
        lines.append(f"FEISHU_CLAUDE_PANE={pane}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def kill_bot():
    # kill any running bot.py (matches absolute path used by the session start)
    run(["pkill", "-f", re.escape(BOT_SCRIPT)])
    # also catch the relative "python bot.py" form just in case
    run(["pkill", "-f", r"python bot\.py"])
    # kill the dedicated session if it exists
    run(["tmux", "kill-session", "-t", BOT_SESSION])
    time.sleep(1)


def start_bot() -> bool:
    r = run(
        ["tmux", "new-session", "-d", "-s", BOT_SESSION,
         "-c", str(PROJECT_DIR), f"{VENV_PYTHON} {BOT_SCRIPT}"]
    )
    return r.returncode == 0


def bot_running() -> bool:
    r = run(["pgrep", "-f", re.escape(BOT_SCRIPT)])
    return r.returncode == 0 and bool(r.stdout.strip())


def cmd_init():
    if not tmux_reachable():
        print("ERROR: no tmux server reachable. Start tmux first (e.g. `tmux new -s claude`) and re-run.")
        sys.exit(1)
    pane = find_claude_pane()
    if not pane:
        print("ERROR: could not find a tmux pane running `claude` (Claude Code). "
              "Is Claude Code running inside tmux?")
        sys.exit(2)
    update_env_pane(pane)
    print(f"[init] Claude pane detected and bound: FEISHU_CLAUDE_PANE={pane}")

    kill_bot()
    if start_bot():
        print(f"[init] bot (re)started in tmux session '{BOT_SESSION}'")
    else:
        print("[init] ERROR: failed to start bot session", file=sys.stderr)
        sys.exit(3)
    time.sleep(3)
    cmd_status()


def cmd_status():
    pane = find_claude_pane()
    env_pane = None
    if ENV_PATH.is_file():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if line.startswith("FEISHU_CLAUDE_PANE="):
                env_pane = line.split("=", 1)[1].strip()
    print(f"[status] Claude pane now : {pane}")
    print(f"[status] .env CLAUDE_PANE: {env_pane}  {'(matches)' if env_pane == pane else '(MISMATCH — run init)'}")
    print(f"[status] bot running     : {bot_running()}")
    # show last few bot log lines
    r = run(["tmux", "capture-pane", "-p", "-t", BOT_SESSION, "-S", "-6"])
    if r.returncode == 0:
        last = [l for l in r.stdout.splitlines() if l.strip()][-4:]
        print("[status] bot log tail:")
        for l in last:
            print("   " + l)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "init":
        cmd_init()
    elif cmd == "status":
        cmd_status()
    else:
        sys.exit("Usage: bridgectl.py [init|status]")


if __name__ == "__main__":
    main()
