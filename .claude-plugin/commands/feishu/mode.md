---
description: Restart the Feishu bridge in a different permission mode, resuming the same session (e.g. /feishu:mode plan). Arg: plan|auto|acceptEdits|bypassPermissions.
argument-hint: plan|auto|acceptEdits|bypassPermissions
---
The user wants to switch the Feishu bridge to permission mode: **$ARGUMENTS**

Run the launcher and report its output verbatim:

```bash
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" && .venv/bin/python -m mcp_channel.launcher mode "$ARGUMENTS"
```
