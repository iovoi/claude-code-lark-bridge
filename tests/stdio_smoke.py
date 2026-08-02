"""Offline stdio smoke test for the feishu MCP channel (PRD AC1 + AC2).

Spawns `python -m mcp_channel` as a subprocess (as Claude Code would), drives the
MCP handshake with dummy Feishu creds (so no real websocket connection), and
asserts:
  * initialize result advertises the experimental `claude/channel` capability;
  * tools/list exposes `reply` and `react`;
  * tools/call `reply` runs and returns a content block.

Run:  .venv/bin/python tests/stdio_smoke.py                       (POSIX)
      .venv\\Scripts\\python.exe tests/stdio_smoke.py              (Windows)
      python tests/stdio_smoke.py --python /path/to/venv/python   (explicit)

The interpreter is resolved portably (see _venv_python); the test does NOT depend
on a POSIX-only `.venv/bin/python` layout, so it runs on native Windows too.
"""
import os
import sys
from pathlib import Path

import anyio
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.session import ClientSession


def _venv_python() -> str:
    """Resolve the venv interpreter portably.

    Priority:
      1. ``--python <path>`` on the command line, or ``$FEISHU_BRIDGE_PYTHON``;
      2. ``sys.executable`` (when the test itself runs from the venv, the
         interpreter we're already under is exactly the one to spawn);
      3. the installer's venv at ``~/.chat_bridge/venv``
         (Scripts/python.exe on Windows, bin/python on POSIX);
      4. a repo-local ``.venv`` (legacy dev layout).

    OS-correct bin dir (Scripts vs bin) and .exe suffix are derived from os.name,
    never hardcoded. Returns a string path (not checked for existence beyond the
    explicit candidates — if the chosen path is wrong the spawn error is clear)."""
    # 1. explicit override
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--python" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--python="):
            return a.split("=", 1)[1]
    env_py = os.environ.get("FEISHU_BRIDGE_PYTHON")
    if env_py:
        return env_py

    # 2. the interpreter running this test (most reliable: spawn what we're in)
    if sys.executable and Path(sys.executable).is_file():
        return sys.executable

    # 3. installer venv at ~/.chat_bridge/venv
    installer = Path.home() / ".chat_bridge" / "venv"
    exe = installer / ("Scripts" if os.name == "nt" else "bin") / (
        "python.exe" if os.name == "nt" else "python")
    if exe.is_file():
        return str(exe)

    # 4. legacy repo-local .venv
    here = Path(__file__).resolve().parent.parent
    legacy = here / ".venv" / ("Scripts" if os.name == "nt" else "bin") / (
        "python.exe" if os.name == "nt" else "python")
    return str(legacy)


async def main() -> None:
    # Inherit the FULL parent environment (Windows-safe: preserves SystemRoot,
    # PATHEXT, APPDATA, TEMP, ... which the spawned process needs), then override
    # only the FEISHU_* keys that keep the websocket offline with dummy creds.
    env = dict(os.environ)
    env.update({
        "FEISHU_APP_ID": "dummy_id",
        "FEISHU_APP_SECRET": "dummy_secret",
        "FEISHU_DISABLE_WS": "1",
    })
    params = StdioServerParameters(
        command=_venv_python(),
        args=["-m", "mcp_channel"],
        env=env,
    )

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
