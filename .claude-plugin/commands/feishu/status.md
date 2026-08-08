---
description: Show whether the Feishu bridge is running (pid).
---
Run the bridge status command and report its output verbatim:

```bash
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" && .venv/bin/feishu-bridge status
```
