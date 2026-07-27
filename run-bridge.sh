#!/bin/sh
# Run the Feishu bridge (installed by install.py). Launches a detached headless session.
REPO="$HOME/.chat_bridge/claude-code-lark-bridge"
PY="$HOME/.chat_bridge/venv/bin/python"
cd "$REPO" 2>/dev/null || { echo "Bridge not installed. Run install.sh first." >&2; exit 1; }
exec "$PY" -m mcp_channel.launcher up "$@"
