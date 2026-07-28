#!/bin/sh
# Run the Feishu bridge (installed by install.py). Launches a detached headless session.
# POSIX / WSL / macOS / Linux ONLY — it uses the venv's bin/python. On native Windows
# (where venvs live under Scripts/), use run-bridge.bat instead. The installer surfaces
# the right one per platform.
REPO="$HOME/.chat_bridge/claude-code-lark-bridge"
PY="$HOME/.chat_bridge/venv/bin/python"
cd "$REPO" 2>/dev/null || { echo "Bridge not installed. Run install.sh first." >&2; exit 1; }
exec "$PY" -m mcp_channel.launcher up "$@"
