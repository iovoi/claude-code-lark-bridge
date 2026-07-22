---
description: Show whether the Feishu bridge is up, plus recent channel log lines.
---
Run the Feishu bridge launcher and report its output verbatim:

```bash
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" && .venv/bin/python -m mcp_channel.launcher status
```
