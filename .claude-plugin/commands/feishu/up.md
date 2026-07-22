---
description: Launch the Feishu bridge (detached headless claude session with the channel). Optional: --mode plan
---
Run the Feishu bridge launcher and report its output verbatim:

```bash
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" && .venv/bin/python -m mcp_channel.launcher auto|bypassPermissions (default bypass, requires an allowlist).|up
```
