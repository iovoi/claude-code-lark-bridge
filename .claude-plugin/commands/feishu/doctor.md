---
description: Run the Feishu bridge doctor: validate creds, allowlist, and the websocket connect.
---
Run the Feishu bridge launcher and report its output verbatim:

```bash
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" && .venv/bin/python -m mcp_channel.launcher doctor
```
