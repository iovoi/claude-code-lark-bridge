"""Feishu bridge launcher — bring up / status / stop / mode for the headless bridge.

The bridge (B) is a detached, headless `claude --dangerously-load-development-channels
server:feishu` session that hosts the Feishu channel. Because claude needs a TTY (no-TTY
forces --print and exits), B runs inside a PTY held by a long-lived "keeper" process that
also auto-confirms claude's dev-channels dialog (fixed-cadence Enter — see T0.1 spike) and
tees output to the log.

Subcommands (CLI: `python -m mcp_channel.launcher <cmd> [...args]`):
  up [--mode MODE]   launch B (doctor --no-ws first; bypass requires an allowlist)
  status             is B up? + last log lines
  stop               stop B + reap orphans
  mode MODE          respawn B with --resume <session-id> --permission-mode MODE
  keeper ...         (internal) the long-lived PTY supervisor, spawned detached by `up`
"""
from __future__ import annotations
import json
import os
import select
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STATE_DIR = Path(os.environ.get("FEISHU_BRIDGE_STATE", str(Path.home() / ".feishu-bridge")))
PID_FILE = STATE_DIR / "bridge.pid"
SESSION_FILE = STATE_DIR / "bridge.session"
LOG_PATH = os.environ.get("FEISHU_CHANNEL_LOG", "/tmp/feishu-channel.log")
CLAUDE = shutil.which("claude") or "claude"
DEFAULT_MODE = "bypassPermissions"
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
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


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
def keeper(argv: list[str], session_id: str) -> int:
    """Create a PTY, spawn claude (B) in it, auto-confirm the dev-channels dialog,
    tee output to LOG_PATH, and write B's PID. POSIX only (uses `pty`). Returns when
    claude exits."""
    import pty
    _ensure_state()
    master, slave = pty.openpty()
    env = dict(os.environ)
    env["FEISHU_BRIDGE"] = "1"
    env["FEISHU_CHANNEL_LOG"] = LOG_PATH
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
    try:
        PID_FILE.unlink()
    except OSError:
        pass
    return p.returncode if p.poll() is not None else 0


# ---------- public subcommands ----------
def up(mode: str = DEFAULT_MODE) -> int:
    _ensure_state()
    if _alive(_read_pid()):
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

    session_id = (SESSION_FILE.read_text().strip()
                  if SESSION_FILE.is_file() else str(uuid.uuid4()))
    argv = _claude_argv(mode, session_id, resume=False)
    print(f"[up] launching bridge (mode={mode}, session={session_id}); PTY keeper detaching…")
    # spawn keeper detached (survives this process)
    subprocess.Popen([sys.executable, "-m", "mcp_channel.launcher", "keeper", mode, session_id],
                     cwd=str(REPO), env=dict(os.environ),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    # wait for the keeper to write the PID + the ws to connect
    deadline = time.time() + 240
    while time.time() < deadline:
        if _alive(_read_pid()) and "connected to wss" in _log_tail(2000):
            print(f"[up] bridge UP (pid {_read_pid()}); Feishu websocket connected.")
            return 0
        time.sleep(3)
    if _alive(_read_pid()):
        print(f"[up] bridge running (pid {_read_pid()}) but ws not connected yet — see {LOG_PATH}.")
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
    pid = _read_pid()
    if _alive(pid):
        print(f"[status] UP (pid {pid})")
    else:
        print("[status] DOWN")
    tail = [l for l in _log_tail(1200).splitlines() if l.strip()][-6:]
    for l in tail:
        print("   " + l)
    return 0 if _alive(pid) else 1


def stop() -> int:
    pid = _read_pid()
    if not _alive(pid):
        print("[stop] not running")
    else:
        print(f"[stop] stopping pid {pid} …")
        try:
            os.kill(pid, 15)  # SIGTERM
        except OSError:
            pass
        for _ in range(10):
            if not _alive(pid):
                break
            time.sleep(0.5)
        if _alive(pid):
            try:
                os.kill(pid, 9)  # SIGKILL
            except OSError:
                pass
    try:
        PID_FILE.unlink()
    except OSError:
        pass
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
    time.sleep(1)
    argv = _claude_argv(new_mode, session_id, resume=True)
    subprocess.Popen([sys.executable, "-m", "mcp_channel.launcher", "keeper", new_mode, session_id],
                     cwd=str(REPO), env=dict(os.environ),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    deadline = time.time() + 240
    while time.time() < deadline:
        if _alive(_read_pid()) and "connected to wss" in _log_tail(2000):
            print(f"[mode] bridge UP in {new_mode} (pid {_read_pid()})."); return 0
        time.sleep(3)
    print(f"[mode] bridge relaunched (pid {_read_pid()}); see {LOG_PATH}.")
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
