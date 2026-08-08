---
description: Stop the running Feishu bridge.
---
Stop the Feishu bridge and report its output verbatim:

```bash
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" && .venv/bin/feishu-bridge stop
```
