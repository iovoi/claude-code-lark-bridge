# Fix native-Windows install + runtime of the Feishu bridge

## Context

A user installed the bridge on native Windows 11 (not WSL) and hit 9 issues
(report in chat, 2026-08-02). The architecture is sound — the breakage is
concentrated in the native-Windows **runtime** path (`pywinpty` usage in the
keeper), **installer** robustness, and a couple of tooling gaps. The critical
blocker is that `pywinpty` is imported and driven against an API that does not
exist in the pinned dependency, so the bridge crashes on `up` on Windows.

Research done from source:
- Installed dist is `pywinpty`, but its **top-level module is `winpty`**
  (`from winpty import PTY`). `from pywinpty import PTY` → `ModuleNotFoundError`.
- 3.0.5 `PTY` is an **instance** class: `PTY(cols, rows)`, *not* `PTY.open(80,24)`.
  `spawn(appname, cmdline=None, cwd=None, env=None)` is an **instance method**,
  `env` must be a **null-joined string** (`'K=V\0K2=V2\0'`, not a dict), `read()`
  returns **str**, `write()` takes **str**, there is **no `close()`**.
- The high-level `winpty.PtyProcess.spawn(argv_list, cwd, env_dict, dimensions)`
  handles all of that conversion and exposes `.pid/.read/.write(str)/.isalive()/
  .exitstatus/.close()/.terminate()`. **We'll use `PtyProcess`** instead of
  hand-rolling the low-level `PTY` — far less code, far fewer ways to be wrong.

This box is Linux/WSL, so I can fix and regression-test everything *except* the
live Windows PTY/stdio path, which the user must verify on their laptop.

## Changes

### 1. `mcp_channel/launcher.py` — rewrite `_keeper_windows` (issues #1, #2, #4)
- Import `PTY` via `winpty` (fall back to `pywinpty` for older installs).
- Drive it through **`winpty.PtyProcess.spawn(argv, cwd=REPO, env=env_dict,
  dimensions=(24,80))`** — eliminates the bad `PTY.open`/dict-env/`close` calls.
- `PtyProcess` returns/accepts **str**: `proc.read()` → str (decode path drops),
  `proc.write(str)`. Keep one `_to_str(payload)` shim so the existing
  byte-based watchdog writes (`b"\r"`, mapped keystrokes) keep working.
- Poll liveness via `proc.isalive()` + read in a 1s loop (mirrors `_keeper_posix`),
  instead of the broken blocking `pty_obj.read()`.
- **Fallback (issue #4 / PRD):** `keeper()` wraps `_keeper_windows` in try/except.
  On any import/spawn failure, log clearly and call a new
  `_keeper_windows_no_pty(argv, session_id)` that runs claude with
  `subprocess.Popen(stdin/stdout/stderr=DEVNULL, creationflags=CREATE_NEW_PROCESS_GROUP
  | DETACHED_PROCESS)` and tees nothing (no TTY stream) — claude gets no TTY, so the
  boot-dialog auto-confirm is impossible, but the process at least launches and the
  websocket can come up. This is a degraded-but-alive mode, explicitly logged.
  The POSIX keeper is unchanged.

### 2. `mcp_channel/__main__.py` + `mcp_channel/server.py` — Windows stdio handshake (issue #3, *best-effort, needs user verification*)
- Most likely cause: Windows' default `ProactorEventLoop` mishandles the stdio
  transports mcp's `stdio_server()` relies on. Standard fix: set
  `asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())` on
  Windows **before** `anyio.run`, in `__main__.run()` (it already owns the entry
  point). Selector loop is fine here — the channel server spawns no subprocesses.
- Also flush stdout/stderr on boot so the host actually sees the boot line.
- Cannot reproduce here; user must confirm the offline smoke test (now Windows-capable, see #7) completes the `initialize` handshake on their laptop.

### 3. `install.py` — installer reliability (issues #5, #6, #9)
- **`make_venv` (#5):** drop `-q`; install the package with the **uv already in
  the venv** (`uv pip install --python <venv_python> -e <target>`) instead of
  pip — seconds vs minutes and naturally verbose. Keep a pip fallback. Make the
  step idempotent (re-running just re-resolves, no half-state) by not gating on
  "venv dir exists" alone for the package install — always (re)install the editable
  package into the existing venv.
- **`_find_python_ge310` (#6):** additionally probe well-known launcher + Store
  Python paths that may be absent from PATH:
  `%LOCALAPPDATA%\Programs\Python\Launcher\py.exe` (run `py -0p` against it),
  and `%LOCALAPPDATA%\Microsoft\WindowsApps\python3.1x.exe` Store stubs.
- **`.mcp.json` template (#9):** change the checked-in template from a dead
  `"command": "uvx"` to a path that works standalone / is a clear placeholder, and
  document that `configure_mcp` rewrites it. (Low priority; the rewrite is the
  source of truth. Keep the template but note it's overwritten.)

### 4. `install.py` — native-Windows runtime warning (issue #9 / "Critical #4")
- Add a `preflight()` notice on non-WSL Windows (detect: `os.name=='nt'` and no
  `WSL_DISTRO_NAME`/`WSLENV`-style marker): print a prominent warning that the
  native runtime is still maturing and WSL2 is recommended, **before** continuing.
  Not a hard gate (user explicitly chose native), but no longer silent success.

### 5. `mcp_channel/doctor.py` — output + fail-fast (issue #8)
- Force line-buffered/flushed output (`print(..., flush=True)`) so piped output
  isn't swallowed for 60s.
- In `run_doctor`, run `check_creds()` first and **return early** on creds FAIL
  before any websocket/network work (currently `check_ws` lazy-imports lark and
  blocks). This makes a no-creds `doctor` finish instantly.

### 6. `tests/stdio_smoke.py` — OS-aware (issue #7)
- Replace the hardcoded `.venv/bin/python` with an OS-aware interpreter path
  (`Scripts\python.exe` on Windows) and resolve it relative to the repo / `sys.executable`,
  so the smoke test (and thus the #3 regression check) runs on Windows too.

## Verification

On this box (Linux/WSL) — must stay green:
- `python3 tests/test_watchdog.py` (existing) → `WATCHDOG OK`.
- `python3 tests/test_launcher_session.py`, `test_launcher_discover.py` (if present) → no regression.
- `python3 tests/stdio_smoke.py` → `SMOKE OK` (now also picks the right interpreter).
- `python3 -c "import ast; ast.parse(open('install.py').read())"` and a compile-all of the edited modules.

On the user's Windows laptop (the real proof — I'll hand them the exact commands):
- Re-run `install.bat` → completes with visible progress, no second Python install, native-Windows warning shown.
- `python tests\stdio_smoke.py` → `SMOKE OK` (proves #3 handshake fix).
- `run-bridge.bat` (or `launcher up`) → bridge comes up and Feishu ws connects; if `PtyProcess` is unavailable, see the degraded-mode log line and still get a running (TTY-less) process.

## Out of scope / notes
- I will **not** claim #3 is fixed — only that the standard Windows event-loop fix is applied and the smoke test is now runnable there; the user must confirm on Windows.
- No new dependencies; `pywinpty>=2` already declared. The import-layer fallback means an older/newer release won't hard-crash.
