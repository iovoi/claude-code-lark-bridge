"""Offline stdio smoke test for the feishu MCP channel (PRD AC1 + AC2).

Spawns `python -m mcp_channel` as a subprocess (as Claude Code would), drives the
MCP handshake with dummy Feishu creds (so no real websocket connection), and
asserts:
  * initialize result advertises the experimental `claude/channel` capability;
  * tools/list exposes `reply` and `react`;
  * tools/call `reply` runs and returns a content block.

Run:  .venv/bin/python tests/stdio_smoke.py      (POSIX)
      .venv\\Scripts\\python.exe tests/stdio_smoke.py   (Windows)
"""
import os
import sys
from pathlib import Path

import anyio
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.session import ClientSession


def _venv_python() -> str:
    """The venv interpreter, OS-correct. Falls back to sys.executable (the python
    running this test) if the venv layout isn't where we expect."""
    here = Path(__file__).resolve().parent.parent
    if os.name == "nt":
        exe = here / ".venv" / "Scripts" / "python.exe"
    else:
        exe = here / ".venv" / "bin" / "python"
    return str(exe) if exe.is_file() else sys.executable


async def main() -> None:
    params = StdioServerParameters(
        command=_venv_python(),
        args=["-m", "mcp_channel"],
        env={
            "PATH": "",  # inherit handled below; dummy env keeps ws offline
            "FEISHU_APP_ID": "dummy_id",
            "FEISHU_APP_SECRET": "dummy_secret",
            "FEISHU_DISABLE_WS": "1",
        },
    )
    # stdio_client merges env over the parent env; keep PATH/terminfo from parent.
    params.env.update({k: v for k, v in os.environ.items()
                       if k in ("PATH", "TERM", "HOME", "LANG", "LC_ALL", "SYSTEMROOT")})

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            result = await client.initialize()
            exp = result.capabilities.experimental
            assert exp == {"claude/channel": {}}, f"capability mismatch: {exp}"
            print("[smoke] capabilities.experimental =", exp)

            tools = await client.list_tools()
            names = {t.name for t in tools.tools}
            assert {"reply", "react"} <= names, f"missing tools: {names}"
            print("[smoke] tools =", sorted(names))

            r = await client.call_tool("reply", {"chat_id": "smoke_test_chat", "text": "smoke"})
            txt = r.content[0].text if r.content else ""
            assert txt in ("sent", "send failed"), f"unexpected reply result: {txt}"
            print("[smoke] reply tool -> ", txt)
    print("\nSMOKE OK")


if __name__ == "__main__":
    try:
        anyio.run(main)
    except AssertionError as e:
        print("SMOKE FAILED (assertion):", e, file=sys.stderr); sys.exit(1)
    except Exception as e:
        import traceback; traceback.print_exc()
        print("SMOKE FAILED:", repr(e), file=sys.stderr); sys.exit(1)
