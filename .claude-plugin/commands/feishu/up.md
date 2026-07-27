---
description: Launch the Feishu bridge (detached headless claude session with the channel). Default mode bypass (requires an allowlist); pass --mode plan/auto via the launcher to change.
---
Run the Feishu bridge launcher and report its output verbatim:

```bash
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" && .venv/bin/python -m mcp_channel.launcher up
```
