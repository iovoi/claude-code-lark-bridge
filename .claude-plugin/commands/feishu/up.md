---
description: Start the Feishu bridge as a detached background process (streaming mode; no PTY/tmux).
---
Start the Feishu bridge and report its output verbatim:

```bash
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" && .venv/bin/feishu-bridge up
```
