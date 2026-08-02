"""Feishu bridge launcher — bring up / status / stop / mode for the headless bridge.

The bridge (B) is a detached, headless `claude --dangerously-load-development-channels
server:feishu` session that hosts the Feishu channel. Because claude needs a TTY (no-TTY
forces --print and exits), B runs inside a PTY held by a long-lived "keeper" process that
also auto-confirms claude's dev-channels dialog (fixed-cadence Enter — see T0.1 spike) and
tees output to the log.

Subcommands (CLI: `python -m mcp_channel.launcher <cmd> [...args]`):
  up [--mode MODE]   launch B (default mode `auto`; `--mode bypassPermissions` requires
                     an allowlist). Refuses if a bridge is already running (discovered
                     via /proc, not just the pid file).
  status             is B up? (via /proc discovery) + last log lines
  stop               stop B by discovering real bridge pids, SIGTERM (grace) then SIGKILL,
                     and reap leftover keepers. Idempotent.
  mode MODE          respawn B with --resume <session-id> --permission-mode MODE; waits
                     for the old bridge to fully exit first so the resumed session does
                     not collide with a still-living one.
  keeper ...         (internal) the long-lived PTY supervisor, spawned detached by `up`
"""
from __future__ import annotations
import json
import os
import platform
import select
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STATE_DIR = Path(os.environ.get("FEISHU_BRIDGE_STATE", str(Path.home() / ".feishu-bridge")))
PID_FILE = STATE_DIR / "bridge.pid"
SESSION_FILE = STATE_DIR / "bridge.session"
# Cross-platform default log location. /tmp does not exist on native Windows
# (it resolves to C:\tmp\…, which usually can't be created), so fall back to the
# OS temp dir. Override with FEISHU_CHANNEL_LOG. Must match mcp_channel/__main__.py.
DEFAULT_LOG_PATH = os.path.join(tempfile.gettempdir(), "feishu-channel.log")
LOG_PATH = os.environ.get("FEISHU_CHANNEL_LOG", DEFAULT_LOG_PATH)
CLAUDE = shutil.which("claude") or "claude"
# Default permission mode for `up` (no --mode) and `mode` (no arg). `auto` lands the
# bridge in a sane, least-surprise mode without the allowlist requirement that
# `bypassPermissions` imposes. Bypass is still opt-in via `--mode bypassPermissions`.
DEFAULT_MODE = "auto"
VALID_MODES = {"plan", "auto", "acceptEdits", "bypassPermissions", "default"}


# ---------- helpers ----------
def _ensure_state() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def _read_pid() -> int | None:
    try:
        return int(PID_FILE.read_text().strip()) if PID_FILE.is_file() else None
    except Exception:
        return None


def _alive(pid: int | None) -> bool:
    """Is `pid` a running process? Cross-platform.

    POSIX: os.kill(pid, 0) is the standard liveness probe. On Windows that maps
    signal 0 to CTRL_C_EVENT and is NOT a liveness probe, so use the Win32 API
    (OpenProcess + GetExitCodeProcess == STILL_ACTIVE) there instead."""
    if not pid:
        return False
    if os.name == "nt":
        return _alive_windows(pid)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _alive_windows(pid: int) -> bool:
    """Win32 liveness check via GetExitCodeProcess. False on any error (handles
    closed, insufficient rights, or already-exited process)."""
    import ctypes
    from ctypes import wintypes
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    try:
        k = ctypes.windll.kernel32
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        try:
            code = wintypes.DWORD()
            if not k.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            k.CloseHandle(h)
    except Exception:
        return False


def _kill_pid(pid: int | None, force: bool = True) -> None:
    """Terminate a process. POSIX: SIGKILL (force) or SIGTERM. Windows: taskkill
    /T [/F] (kills the whole process tree). Per-pid failures are swallowed."""
    if not pid:
        return
    if os.name == "nt":
        cmd = ["taskkill", "/PID", str(pid), "/T"]
        if force:
            cmd.append("/F")
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        except OSError:
            pass
        return
    try:
        os.kill(pid, 9 if force else 15)
    except OSError:
        pass


def _proc_cmdline(pid: int, proc_root: str = "/proc") -> list[str]:
    """Read /proc/<pid>/cmdline as a list of argv tokens. [] on any error."""
    try:
        raw = Path(proc_root, str(pid), "cmdline").read_bytes()
    except OSError:
        return []
    return [t for t in raw.decode(errors="replace").split("\x00") if t]


def _pids_matching(needle_tokens: set[str], proc_root: str = "/proc") -> list[int]:
    """Return pids (sorted) whose command line contains ALL needle_tokens.
    POSIX: scans /proc/<pid>/cmdline. Windows: queries Win32_Process via PowerShell
    (no /proc). Excludes our own pid. Does NOT rely on bridge.pid, so it survives a
    stale/missing pid file. Returns [] if neither source is available."""
    root = Path(proc_root)
    if root.is_dir():
        me = os.getpid()
        out: list[int] = []
        for name in os.listdir(root):
            if not name.isdigit():
                continue
            pid = int(name)
            if pid == me:
                continue
            parts = _proc_cmdline(pid, proc_root)
            if parts and needle_tokens.issubset(set(parts)):
                out.append(pid)
        return sorted(out)
    if os.name == "nt":
        return _pids_matching_windows(needle_tokens)
    return []


def _pids_matching_windows(needle_tokens: set[str]) -> list[int]:
    """Discover pids whose command line contains all needle_tokens on Windows, via
    PowerShell Get-CimInstance (no /proc available). Best-effort: returns [] on any
    failure (PowerShell missing, timeout, parse error)."""
    try:
        ps_cmd = ("Get-CimInstance Win32_Process | "
                  "ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }")
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=20, check=False)
    except Exception:
        return []
    res: list[int] = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if "|" not in line:
            continue
        pid_s, _, cmdline = line.partition("|")
        if not pid_s.isdigit():
            continue
        # Tokenise by whitespace (robust to quoting) and require all needles present.
        if needle_tokens.issubset(set(cmdline.split())):
            res.append(int(pid_s))
    return sorted(res)


# Tokens that uniquely identify a bridge claude process (see _claude_argv):
#   claude --dangerously-load-development-channels server:feishu ...
_BRIDGE_TOKENS = {"--dangerously-load-development-channels", "server:feishu"}
_KEEPER_TOKENS = {"mcp_channel.launcher", "keeper"}


def _bridge_pids(proc_root: str = "/proc") -> list[int]:
    """Discover live bridge-claude pids. POSIX via /proc; Windows via CIM.
    Independent of bridge.pid, so orphaned/hard-killed-keeper cases are still found."""
    return _pids_matching(_BRIDGE_TOKENS, proc_root)


def _keeper_pids(proc_root: str = "/proc") -> list[int]:
    """Discover live launcher-keeper pids. The keeper argv is
    `python -m mcp_channel.launcher keeper <mode> <session_id>` (note: it does NOT
    carry the dev-channels tokens, so _bridge_pids won't catch it)."""
    return _pids_matching(_KEEPER_TOKENS, proc_root)


def _allowlist_set() -> bool:
    try:
        import feishu_api as api
        return bool(api.cred("FEISHU_ALLOWED_OPEN_IDS") or api.cred("FEISHU_ALLOWED_CHAT_IDS"))
    except Exception:
        return False


def _ensure_bypass_accepted() -> None:
    """Pre-accept the bypass-permissions dialog by setting skipDangerousModePermissionPrompt
    in ~/.claude/settings.json, so headless B isn't blocked by the '1.No,exit / 2.Yes,I accept'
    prompt. Idempotent merge; preserves existing settings."""
    sp = Path.home() / ".claude" / "settings.json"
    sp.parent.mkdir(parents=True, exist_ok=True)
    d = {}
    if sp.is_file():
        try:
            d = json.loads(sp.read_text())
        except Exception:
            d = {}
    if not d.get("skipDangerousModePermissionPrompt"):
        d["skipDangerousModePermissionPrompt"] = True
        sp.write_text(json.dumps(d, indent=2, ensure_ascii=False))
        print("[up] set skipDangerousModePermissionPrompt=true in ~/.claude/settings.json")


def _new_session_id() -> str:
    """A fresh uuid4 for a new bridge session. Always fresh - never reuses the id
    persisted in bridge.session: `up` starts a brand-new pinned session via
    --session-id (NOT --resume), so an old id gives no conversation continuity and
    can collide with a live session, making claude refuse with
    "Session ID ... is already in use". bridge.session is still written (by `up`
    and the keeper) so `mode` can --resume the running bridge."""
    return str(uuid.uuid4())


def _claude_argv(mode: str, session_id: str, resume: bool) -> list[str]:
    """Dev-mode argv. Bypass uses --dangerously-skip-permissions (the CLI opt-in that
    SKIPS the interactive bypass-accept dialog); other modes use --permission-mode.
    Pins --session-id on first launch, --resume on mode-change (D7)."""
    argv = [CLAUDE, "--dangerously-load-development-channels", "server:feishu"]
    if mode == "bypassPermissions":
        argv.append("--dangerously-skip-permissions")
    else:
        argv += ["--permission-mode", mode]
    if resume:
        argv += ["--resume", session_id]
    else:
        argv += ["--session-id", session_id]
    return argv


# ---------- keeper (internal, long-lived PTY supervisor) ----------
# Keystroke forwarding: map a user's Feishu reply token -> bytes typed into the
# PTY. Named keys cover the common interactive-prompt answers; anything else is
# typed verbatim followed by Enter.
_KEY_BYTES = {
    "enter": b"\r",
    "return": b"\r",
    "esc": b"\x1b",
    "escape": b"\x1b",
    "tab": b"\t",
    "up": b"\x1b[A",
    "down": b"\x1b[B",
    "left": b"\x1b[D",
    "right": b"\x1b[C",
    "y": b"y\r",
    "yes": b"y\r",
    "n": b"n\r",
    "no": b"n\r",
    "space": b" ",
}


def _keystroke_to_bytes(text: str) -> bytes:
    """A reply is EITHER a sequence of named keys (e.g. 'down enter', 'y') — in
    which case each maps to its bytes and they're concatenated — OR literal prose,
    typed verbatim followed by a single Enter. We decide by: if every whitespace
    token is a known key, treat as keys; otherwise treat the whole string as prose
    (so 'hello world' becomes 'hello world\\r', not 'hello\\rworld\\r')."""
    text = (text or "").strip()
    if not text:
        return b"\r"
    tokens = text.split()
    if all(t in _KEY_BYTES for t in tokens):
        return b"".join(_KEY_BYTES[t] for t in tokens)
    return text.encode("utf-8", "replace") + b"\r"


def _sender_loop(send_q: "queue.Queue") -> None:
    """Deprecated stub: the keeper runs under the system python, which lacks
    lark_oapi, so it cannot send Feishu messages itself. Outgoing watchdog
    messages are queued to the outbox (bridgestate.push_outbox) and the MCP
    server (which has lark) drains + sends them. Kept only to avoid churn."""
    return


class _WatchdogRunner:
    """Owns one Watchdog; the keeper calls on_chunk()/tick(). Keeps the watchdog
    logic out of the POSIX/Windows keeper duplication.

    Outgoing Feishu messages (progress/stuck alerts) are NOT sent from here —
    the keeper's python has no lark_oapi. They are queued to the outbox and the
    MCP server sends them. Only PTY writes (keystroke forwarding) happen here."""

    def __init__(self, write_pty) -> None:
        from mcp_channel.watchdog import Watchdog
        from mcp_channel import bridgestate
        self.wd = Watchdog(time.time())
        self.bridgestate = bridgestate
        self.write_pty = write_pty

    def on_chunk(self, data: bytes) -> None:
        if data:
            self.wd.feed(data, time.time())

    def tick(self) -> None:
        now = time.time()
        active = None
        try:
            active = self.bridgestate.read_active()
        except Exception as e:
            print(f"[watchdog] read_active failed: {e}", file=sys.stderr)
        try:
            actions = self.wd.tick(active, now)
        except Exception as e:
            print(f"[watchdog] tick failed: {e}", file=sys.stderr)
            actions = []
        for a in actions:
            kind = type(a).__name__
            try:
                if kind == "SendProgress":
                    self.bridgestate.push_outbox(a.chat_id, a.text)
                    print(f"[watchdog] queued progress for {a.chat_id}", file=sys.stderr)
                elif kind == "SendStuck":
                    self.bridgestate.push_outbox(a.chat_id, a.screen_text)
                    self.bridgestate.write_stuck(awaiting_keystroke=True, alerted=True,
                                                 stuck_screen=a.screen_text, updated_at=now)
                    print(f"[watchdog] queued stuck alert for {a.chat_id}", file=sys.stderr)
                elif kind == "ClearStuck":
                    self.bridgestate.clear_stuck()
            except Exception as e:
                print(f"[watchdog] action {kind} failed: {e}", file=sys.stderr)
        # Drain pending keystrokes (only meaningful while awaiting, but cheap to
        # check). Each is typed into the PTY; applying one resolves the stuck state.
        try:
            if self.bridgestate.is_awaiting_keystroke():
                for k in self.bridgestate.drain_keystrokes():
                    payload = _keystroke_to_bytes(k.get("text", ""))
                    try:
                        self.write_pty(payload)
                    except Exception as e:
                        print(f"[watchdog] pty write failed: {e}", file=sys.stderr)
                    self.bridgestate.clear_stuck()
                    self.wd.note_resolved(time.time())
        except Exception as e:
            print(f"[watchdog] keystroke drain failed: {e}", file=sys.stderr)

    def stop(self) -> None:
        pass


def _import_winpty():
    """Import the winpty PTY class. The PyPI package is `pywinpty`, but its
    importable module is `winpty` in current releases (≥2); very old releases
    exposed `from pywinpty import PTY`. Try both, return the PTY class, raise the
    last error if neither imports (so the caller can fall back to no-PTY)."""
    last = None
    for modname in ("winpty", "pywinpty"):
        try:
            mod = __import__(modname)
            return getattr(mod, "PTY")
        except Exception as e:  # ImportError, AttributeError, …
            last = e
    raise last


def _make_pty(PTY):
    """Construct a PTY across pywinpty API revisions. ≥2.x uses `PTY()` (or the
    classmethod PTY.open(cols, rows)); older builds only had PTY.open. Try the
    documented shapes; raise if none work (caller falls back)."""
    for make in (lambda: PTY(80, 24), lambda: PTY.open(80, 24)):
        try:
            return make()
        except Exception:
            continue
    raise RuntimeError("could not construct a winpty PTY (no PTY()/PTY.open)") from None


def _winpty_spawn(argv: list[str], env: dict) -> tuple:
    """Open a Windows PTY via pywinpty and spawn argv in it. Returns
    (pty_obj, pid_or_None). Raises on ANY incompatibility (wrong import name,
    changed API, missing build) so _keeper_windows can fall back to a no-PTY
    detach — the PRD's documented degradation path."""
    PTY = _import_winpty()
    pty_obj = _make_pty(PTY)
    # spawn may be a classmethod (returns the instance) or an instance method.
    spawn = getattr(pty_obj, "spawn", None) or getattr(PTY, "spawn", None)
    if spawn is None:
        raise RuntimeError("winpty PTY exposes no spawn()")
    spawn(subprocess.list2cmdline(argv), cwd=str(REPO), env=env)
    pid = getattr(pty_obj, "pid", None)
    return pty_obj, pid


def _keeper_windows_pty(argv: list[str], session_id: str) -> int:
    """PTY-keeper via pywinpty. spawn/read/write/close replace POSIX pty+select.
    Raises if pywinpty is missing or its API is incompatible — the caller then
    falls back to _keeper_windows_no_pty."""
    import re
    _ensure_state()
    env = dict(os.environ)
    env["FEISHU_BRIDGE"] = "1"; env["FEISHU_CHANNEL_LOG"] = LOG_PATH
    # Force uvx to rebuild the feishu-channel MCP server from source on every
    # launch. The repo lives on a WSL /mnt/c (drvfs) filesystem whose unreliable
    # mtimes/inodes defeat uv's source-change detection, so a cached wheel can
    # mask server.py edits (the bridge silently runs stale code). UV_NO_CACHE
    # trades ~10-60s of boot time (re-resolving deps) for guaranteed-fresh code.
    env["UV_NO_CACHE"] = "1"
    pty_obj, pid = _winpty_spawn(argv, env)
    try:
        logf = open(LOG_PATH, "a", buffering=1)
    except Exception:
        logf = None
    ansi = re.compile(rb"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[<>][0-9a-z]*")

    def _write(payload: bytes) -> None:
        # winpty PTY.write takes a str; the watchdog hands us bytes.
        try:
            pty_obj.write(payload.decode("utf-8", "replace"))
        except Exception:
            pass

    if pid:
        PID_FILE.write_text(str(pid))
    SESSION_FILE.write_text(session_id)
    buf = b""; dev_done = bypass_done = False
    runner = _WatchdogRunner(_write)
    while True:
        # Exit when the spawned claude dies (newer winpty exposes isalive()).
        isalive = getattr(pty_obj, "isalive", None)
        if callable(isalive):
            try:
                if not isalive():
                    break
            except Exception:
                pass
        try:
            data = pty_obj.read()
        except Exception:
            break
        if data:
            if isinstance(data, str):
                data = data.encode("utf-8", "replace")
            if logf:
                try:
                    logf.write(data.decode(errors="replace")); logf.flush()
                except Exception:
                    pass
            buf += data
            clean = ansi.sub(b"", buf)
            if not dev_done and b"development" in clean:
                _write(b"\r"); dev_done = True
            elif not bypass_done and (b"Yes,Iaccept" in clean or b"No,exit" in clean):
                _write(b"2\r\x1b[B\r"); bypass_done = True
            runner.on_chunk(data)
        else:
            time.sleep(0.2)
        runner.tick()
    runner.stop()
    try:
        pty_obj.close()
    except Exception:
        pass
    try:
        PID_FILE.unlink()
    except OSError:
        pass
    return 0


def _keeper_windows_no_pty(argv: list[str], session_id: str) -> int:
    """Degraded Windows keeper used when pywinpty is unavailable or incompatible.
    Launches claude detached (stdout/stderr → log), but with NO PTY we cannot
    auto-confirm the dev-channels / bypass dialogs — so claude may wedge on a
    confirmation prompt. bypassPermissions mode (skipDangerousModePermissionPrompt)
    avoids the bypass dialog; the dev-channels dialog still needs a TTY. This is a
    last resort documented in the PRD; WSL2 remains the verified Windows path."""
    _ensure_state()
    env = dict(os.environ)
    env["FEISHU_BRIDGE"] = "1"; env["FEISHU_CHANNEL_LOG"] = LOG_PATH
    env["UV_NO_CACHE"] = "1"
    try:
        logf = open(LOG_PATH, "a", buffering=1)
    except Exception:
        logf = subprocess.DEVNULL
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    p = subprocess.Popen(argv, cwd=str(REPO), env=env,
                         stdin=subprocess.DEVNULL, stdout=logf, stderr=logf,
                         creationflags=flags)
    PID_FILE.write_text(str(p.pid))
    SESSION_FILE.write_text(session_id)
    # No PTY to write keystrokes into; keep the watchdog ticking so the outbox
    # (progress/stuck alerts) still drains. Exit when claude exits.
    runner = _WatchdogRunner(lambda payload: None)
    while True:
        runner.tick()
        if p.poll() is not None:
            break
        time.sleep(1)
    runner.stop()
    try:
        PID_FILE.unlink()
    except OSError:
        pass
    return p.returncode if p.poll() is not None else 0


def _keeper_windows(argv: list[str], session_id: str) -> int:
    """Windows keeper: PTY (pywinpty) first, with an automatic no-PTY detach
    fallback if pywinpty is missing/incompatible (the PRD's degradation path).
    See _keeper_windows_pty / _keeper_windows_no_pty."""
    try:
        return _keeper_windows_pty(argv, session_id)
    except Exception as e:
        print(f"[keeper] pywinpty PTY unavailable/incompatible ({e!r}); "
              f"falling back to NO-PTY detached launch (interactive dialogs will "
              f"NOT be auto-confirmed — prefer WSL2).", file=sys.stderr)
        return _keeper_windows_no_pty(argv, session_id)


def keeper(argv: list[str], session_id: str) -> int:
    """PTY-keeper dispatcher: pywinpty on Windows, POSIX pty elsewhere (AC7)."""
    # The keeper is spawned with stderr=DEVNULL by up(), which would make its
    # diagnostics ([watchdog] lines) invisible. Redirect our own stderr to the
    # shared log so watchdog state/errors are observable alongside claude's TTY
    # output. (Claude's output is teed separately; both land in LOG_PATH.)
    try:
        sys.stderr = open(LOG_PATH, "a", buffering=1)
    except Exception:
        pass
    print(f"[keeper] starting (pid={os.getpid()}, session={session_id})", file=sys.stderr)
    if platform.system() == "Windows":
        return _keeper_windows(argv, session_id)
    return _keeper_posix(argv, session_id)


def _keeper_posix(argv: list[str], session_id: str) -> int:
    """Create a PTY, spawn claude (B) in it, auto-confirm the dev-channels dialog,
    tee output to LOG_PATH, and write B's PID. POSIX only (uses `pty`). Returns when
    claude exits."""
    import pty
    _ensure_state()
    master, slave = pty.openpty()
    env = dict(os.environ)
    env["FEISHU_BRIDGE"] = "1"
    env["FEISHU_CHANNEL_LOG"] = LOG_PATH
    env["UV_NO_CACHE"] = "1"  # see _keeper_windows: drvfs-safe rebuild of the server
    p = subprocess.Popen(argv, cwd=str(REPO), env=env,
                         stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
    os.close(slave)
    PID_FILE.write_text(str(p.pid))
    SESSION_FILE.write_text(session_id)
    try:
        logf = open(LOG_PATH, "a", buffering=1)
    except Exception:
        logf = None
    import re
    ansi = re.compile(rb"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[<>][0-9a-z]*")
    buf = b""
    dev_done = bypass_done = False
    # Watchdog: monitors Claude's TTY stream for "stuck on a prompt" vs "working",
    # forwards progress/stuck alerts to the user, and types the user's reply into
    # the PTY to un-stick it. Writes go through os.write(master, ...) below.
    runner = _WatchdogRunner(lambda payload: os.write(master, payload))
    while True:
        rc = p.poll()
        if rc is not None:
            break
        r, _, _ = select.select([master], [], [], 1)
        if master in r:
            try:
                data = os.read(master, 4096)
            except OSError:
                break
            if not data:
                break
            if logf:
                try:
                    logf.write(data.decode(errors="replace")); logf.flush()
                except Exception:
                    pass
            buf += data
            clean = ansi.sub(b"", buf)
            # dev-channels dialog (option 1 = proceed) -> Enter.
            if not dev_done and b"development" in clean:
                try: os.write(master, b"\r")
                except OSError: pass
                dev_done = True
            # bypass-accept dialog (option 2 = Yes, I accept). Key on the option text, NOT on
            # 'Bypass' (which also appears in the informational mode-warning). Send '2' + Enter
            # (number-select) then a Down+Enter fallback.
            elif not bypass_done and (b"Yes,Iaccept" in clean or b"No,exit" in clean):
                try: os.write(master, b"2\r\x1b[B\r")
                except OSError: pass
                bypass_done = True
            runner.on_chunk(data)
        # tick every iteration (~1s) whether or not data arrived, so we detect
        # idle wedges (no output) as well as explicit prompts.
        runner.tick()
    runner.stop()
    try:
        PID_FILE.unlink()
    except OSError:
        pass
    return p.returncode if p.poll() is not None else 0


# ---------- public subcommands ----------
def up(mode: str = DEFAULT_MODE) -> int:
    _ensure_state()
    pids = _bridge_pids()
    if pids:
        print(f"[up] bridge already running (pids {pids})"); return 0
    # POSIX discovery is empty on Windows; fall back to the pid file there.
    if platform.system() == "Windows" and _alive(_read_pid()):
        print(f"[up] bridge already running (pid {_read_pid()})"); return 0
    if mode not in VALID_MODES:
        print(f"[up] unknown mode {mode!r}; one of {sorted(VALID_MODES)}"); return 2
    if mode == "bypassPermissions" and not _allowlist_set():
        print("[up] REFUSED: bypassPermissions requires an allowlist "
              "(FEISHU_ALLOWED_OPEN_IDS / FEISHU_ALLOWED_CHAT_IDS). Set one, or use --mode plan|auto.")
        return 2
    if mode == "bypassPermissions":
        _ensure_bypass_accepted()
    # fast creds check first
    from mcp_channel.doctor import run_doctor
    if run_doctor(include_ws=False) != 0:
        print("[up] doctor failed (creds); not launching."); return 1

    session_id = _new_session_id()
    # Write the fresh id ourselves so bridge.session is authoritative even before
    # the keeper writes it, and so `mode` later --resumes the session actually
    # running. We deliberately do NOT reuse a persisted id (see _new_session_id).
    SESSION_FILE.write_text(session_id)
    argv = _claude_argv(mode, session_id, resume=False)
    print(f"[up] launching bridge (mode={mode}, session={session_id}); PTY keeper detaching…")
    # spawn keeper detached (survives this process)
    subprocess.Popen([sys.executable, "-m", "mcp_channel.launcher", "keeper", mode, session_id],
                     cwd=str(REPO), env=dict(os.environ),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    # wait for the keeper to spawn claude + the ws to connect
    deadline = time.time() + 240
    while time.time() < deadline:
        if _bridge_pids() and "connected to wss" in _log_tail(2000):
            print(f"[up] bridge UP (pids {_bridge_pids()}); Feishu websocket connected.")
            return 0
        if "already in use" in _log_tail(2000):
            print(f"[up] session id already in use - claude refused to start. See {LOG_PATH}.")
            return 1
        time.sleep(3)
    if _bridge_pids():
        print(f"[up] bridge running (pids {_bridge_pids()}) but ws not connected yet — see {LOG_PATH}.")
        return 0
    print(f"[up] bridge did not start — see {LOG_PATH}.")
    return 1


def _log_tail(n: int = 800) -> str:
    try:
        with open(LOG_PATH) as f:
            return f.read()[-n:]
    except Exception:
        return ""


def status() -> int:
    pids = _bridge_pids()
    pid = _read_pid()
    up_by_discovery = bool(pids)
    up_by_pidfile = (platform.system() == "Windows") and _alive(pid)  # Windows has no /proc
    if up_by_discovery:
        print(f"[status] UP (pids {pids})")
    elif up_by_pidfile:
        print(f"[status] UP (pid {pid})")
    else:
        print("[status] DOWN")
    tail = [l for l in _log_tail(1200).splitlines() if l.strip()][-6:]
    for l in tail:
        print("   " + l)
    return 0 if (up_by_discovery or up_by_pidfile) else 1


def _term_then_kill(pids: list[int], grace_terms: int = 14, grace_kill: int = 4) -> None:
    """Stop each pid. POSIX: SIGTERM, wait for graceful exit (lets claude release its
    session lock and flush), then SIGKILL stragglers. Windows: no graceful signal
    semantics, so taskkill /T /F the whole tree immediately. Per-pid errors swallowed."""
    if not pids:
        return
    if os.name == "nt":
        for pid in pids:
            _kill_pid(pid, force=True)
        # brief wait so callers reading _alive() right after see them gone
        for _ in range(grace_kill):
            if not any(_alive(p) for p in pids):
                break
            time.sleep(0.5)
        return
    for pid in pids:
        try:
            os.kill(pid, 15)  # SIGTERM
        except OSError:
            pass
    for _ in range(grace_terms):
        if not any(_alive(p) for p in pids):
            break
        time.sleep(0.5)
    for pid in pids:
        if _alive(pid):
            try:
                os.kill(pid, 9)  # SIGKILL
            except OSError:
                pass
    for _ in range(grace_kill):
        if not any(_alive(p) for p in pids):
            break
        time.sleep(0.5)


def stop() -> int:
    pids = _bridge_pids()
    # Windows has no /proc discovery; fall back to the pid file written by the keeper.
    if not pids and platform.system() == "Windows":
        fpid = _read_pid()
        if _alive(fpid):
            pids = [fpid]
    keepers = _keeper_pids()
    if not pids and not keepers:
        print("[stop] not running")
    else:
        if pids:
            print(f"[stop] stopping bridge pids {pids} …")
            _term_then_kill(pids)
        # Keepers normally exit on their own once their claude child dies; reap any that
        # linger (e.g. keeper whose claude was orphaned under a different pid).
        keepers = [k for k in _keeper_pids() if _alive(k)]
        if keepers:
            print(f"[stop] reaping keeper pids {keepers} …")
            _term_then_kill(keepers, grace_terms=2, grace_kill=2)
    try:
        PID_FILE.unlink()
    except OSError:
        pass
    # NOTE: bridge.session is intentionally preserved — `mode` needs it for --resume.
    print("[stop] done")
    return 0


def mode(new_mode: str) -> int:
    """D7: respawn B with the same session id and a new permission mode."""
    if new_mode not in VALID_MODES:
        print(f"[mode] unknown mode {new_mode!r}; one of {sorted(VALID_MODES)}"); return 2
    if new_mode == "bypassPermissions" and not _allowlist_set():
        print("[mode] REFUSED: bypassPermissions requires an allowlist."); return 2
    if not SESSION_FILE.is_file():
        print("[mode] no bridge session on record — run `up` first."); return 1
    session_id = SESSION_FILE.read_text().strip()
    print(f"[mode] restarting bridge in {new_mode} (resuming session {session_id}) …")
    stop()
    # Wait gate: do NOT --resume until the old bridge is actually gone, otherwise
    # claude refuses with "Session ID ... is already in use" and the bridge wedges.
    # The graceful SIGTERM inside stop() lets claude release its session lock first.
    deadline = time.time() + 15
    while time.time() < deadline:
        if not _bridge_pids() and not _keeper_pids():
            break
        time.sleep(0.5)
    remaining = _bridge_pids()
    if remaining:
        print(f"[mode] could not stop existing bridge (pids {remaining}); "
              f"aborting to avoid session-lock collision. See {LOG_PATH}.")
        return 1
    argv = _claude_argv(new_mode, session_id, resume=True)
    subprocess.Popen([sys.executable, "-m", "mcp_channel.launcher", "keeper", new_mode, session_id],
                     cwd=str(REPO), env=dict(os.environ),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    deadline = time.time() + 240
    while time.time() < deadline:
        if _bridge_pids() and "connected to wss" in _log_tail(2000):
            print(f"[mode] bridge UP in {new_mode} (pids {_bridge_pids()})."); return 0
        time.sleep(3)
    print(f"[mode] bridge relaunched; see {LOG_PATH}.")
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__); return 0
    cmd = argv[0]
    if cmd == "up":
        mode_ = DEFAULT_MODE
        if len(argv) > 2 and argv[1] == "--mode":
            mode_ = argv[2]
        return up(mode_)
    if cmd == "status":
        return status()
    if cmd == "stop":
        return stop()
    if cmd == "mode":
        return mode(argv[1] if len(argv) > 1 else DEFAULT_MODE)
    if cmd == "keeper":
        # internal: keeper <mode> <session_id>
        return keeper(_claude_argv(argv[1], argv[2], resume=False), argv[2])
    print(f"unknown command {cmd!r}\n{__doc__}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
