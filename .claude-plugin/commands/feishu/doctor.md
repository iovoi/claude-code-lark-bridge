---
description: Check Feishu bridge health — running status + recent log lines.
---
Check bridge health: report status, then show the tail of the bridge log.

```bash
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
.venv/bin/feishu-bridge status
echo "--- recent log ---"
tail -n 40 "$HOME/.chat_bridge/bridge.log" 2>/dev/null || echo "(no log yet)"
```
