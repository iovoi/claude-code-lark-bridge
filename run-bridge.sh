#!/bin/sh
# Run the Feishu bridge (installed by install.py). Starts a detached background process
# (no PTY, no tmux) via the `feishu-bridge` CLI. POSIX / WSL / macOS / Linux.
REPO="$HOME/.chat_bridge/claude-code-lark-bridge"
BIN="$HOME/.chat_bridge/venv/bin/feishu-bridge"
cd "$REPO" 2>/dev/null || { echo "Bridge not installed. Run install.sh first." >&2; exit 1; }
exec "$BIN" up "$@"
