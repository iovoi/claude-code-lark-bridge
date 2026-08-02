#!/usr/bin/env python3
"""Cross-platform installer for the Feishu/Lark <-> Claude Code bridge.

Run via the thin wrappers: `./install.sh` (POSIX) or `install.bat` (Windows),
which just call `python3 install.py`.

Layout created under ~/.chat_bridge/:
  ~/.chat_bridge/venv/                 # venv: provides launcher python + uvx (uv pip-installed)
  ~/.chat_bridge/<repo>/               # the bridge code (git clone, or curl tarball if no git)
  ~/.chat_bridge/<repo>/.mcp.json      # rewritten to point at the venv's uvx
The run skill is installed to ~/.claude/skills/feishu-bridge/.
"""
from __future__ import annotations
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

REPO_URL = "https://github.com/iovoi/claude-code-lark-bridge"
REPO_REF = os.environ.get("FEISHU_BRIDGE_REF", "main")   # branch/tag to install
DIR_NAME = "claude-code-lark-bridge"
CHAT_BRIDGE = Path.home() / ".chat_bridge"
VENV = CHAT_BRIDGE / "venv"
REPO = CHAT_BRIDGE / DIR_NAME
SKILL_SRC = "skills/feishu-bridge/SKILL.md"          # relative to repo
SKILL_DST = Path.home() / ".claude" / "skills" / "feishu-bridge" / "SKILL.md"


def _bin(name: str) -> str | None:
    return shutil.which(name)


def _venv_bin(tool: str) -> str:
    """Path to a tool inside the venv, OS-correct (bin/ on POSIX, Scripts/ on Windows)."""
    sub = "Scripts" if os.name == "nt" else "bin"
    ext = ".exe" if os.name == "nt" else ""
    return str(VENV / sub / f"{tool}{ext}")


def _step(msg: str) -> None:
    print(f"[install] {msg}")


def _die(msg: str) -> "NoReturn":  # noqa: F821
    print(f"[install] ERROR: {msg}", file=sys.stderr); sys.exit(1)


def _py_ok(exe: str) -> bool:
    """True if `exe` is a Python >=3.10 we can run."""
    try:
        v = subprocess.run([exe, "--version"], capture_output=True, text=True, check=False)
    except OSError:
        return False
    m = re.search(r"(\d+)\.(\d+)", (v.stdout or "") + (v.stderr or ""))
    return bool(m) and (int(m.group(1)), int(m.group(2))) >= (3, 10)


def _windows_python_candidates() -> list[str]:
    """Extra Python interpreter paths to probe when the `py` launcher and
    python3.1x aren't on PATH (common on Windows: the launcher lives at
    %LOCALAPPDATA%\\Programs\\Python\\Launcher but isn't added to PATH, and Store
    Pythons sit under WindowsApps). Returns absolute paths that may or may not
    exist; the caller filters via _py_ok()."""
    out: list[str] = []
    local = Path(os.environ.get("LOCALAPPDATA", "")) if os.name == "nt" else Path("")
    # Python launcher installed without a PATH entry.
    launcher = local / "Programs" / "Python" / "Launcher" / "py.exe"
    if launcher.is_file():
        out.append(str(launcher))
    # python.org installs (per-version dirs).
    for ver in ("Python313", "Python312", "Python311", "Python310"):
        exe = local / "Programs" / "Python" / ver / "python.exe"
        if exe.is_file():
            out.append(str(exe))
    # Microsoft Store Pythons (re-execute stubs; safe to invoke).
    winapps = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps"
    for name in ("python3.13.exe", "python3.12.exe", "python3.11.exe", "python3.10.exe",
                 "python.exe"):
        exe = winapps / name
        if exe.is_file():
            out.append(str(exe))
    return out


def _find_python_ge310() -> str | None:
    """Locate a Python >=3.10 already on the system: py launcher -0p list, then
    python3.1x / python3 / python on PATH, then well-known Windows install/Store
    dirs (covers the case where a 3.12 is present but neither `py` nor `python3.12`
    is on PATH). Returns absolute path or None."""
    cands: list[str] = []
    py = _bin("py")  # Windows Python launcher
    if py:
        try:
            lst = subprocess.run([py, "-0p"], capture_output=True, text=True, check=False).stdout
            for line in lst.splitlines():
                m = re.search(r"([A-Za-z]:\\[^\s]+|/\S+)", line)
                if m:
                    cands.append(m.group(1))
        except OSError:
            pass
    for name in ("python3.12", "python3.11", "python3.10", "python3", "python"):
        p = _bin(name)
        if p:
            cands.append(p)
    cands.extend(_windows_python_candidates())
    for c in cands:
        if _py_ok(c):
            return c
    return None


def _bootstrap_python() -> str | None:
    """Best-effort install of a Python >=3.10 on a clean machine. Windows: winget.
    Anywhere uv is present: `uv python install`. Returns the interpreter path or None."""
    if os.name == "nt" and _bin("winget"):
        _step("Python <3.10: bootstrapping Python 3.12 via winget …")
        subprocess.run(["winget", "install", "--id", "Python.Python.3.12", "-e", "--silent",
                        "--accept-package-agreements", "--accept-source-agreements"], check=False)
        exe = _find_python_ge310()
        if exe:
            return exe
        known = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python312" / "python.exe"
        if known.is_file() and _py_ok(str(known)):
            return str(known)
    if _bin("uv"):
        _step("Python <3.10: bootstrapping Python 3.12 via uv …")
        subprocess.run(["uv", "python", "install", "3.12"], check=False)
        try:
            out = subprocess.run(["uv", "python", "find", "3.12"],
                                 capture_output=True, text=True, check=False).stdout.strip()
            if out and _py_ok(out):
                return out
        except OSError:
            pass
    return None


def _warn_native_windows() -> None:
    """Detect non-WSL native Windows and emit a prominent warning. Native Windows
    uses the pywinpty PTY path + a Windows asyncio stdio transport that are less
    battle-tested than the POSIX/WSL2 path; WSL2 (run install.sh inside Ubuntu) is
    the fully-verified Windows route. We warn, not gate, because the native path is
    being actively fixed."""
    if os.name != "nt":
        return
    print(
        "\n[install] *** NATIVE WINDOWS DETECTED ***\n"
        "[install] The native-Windows runtime (pywinpty PTY + Windows asyncio stdio)\n"
        "[install] is functional but less verified than the POSIX path. For the most\n"
        "[install] reliable experience on a Windows machine, install under WSL2:\n"
        "[install]     run install.sh inside an Ubuntu shell.\n"
        "[install] Proceeding with the native install anyway.\n",
        file=sys.stderr, flush=True,
    )


def preflight() -> None:
    if sys.version_info < (3, 10):
        _step(f"this Python is {platform.python_version()} (<3.10); looking for a newer one …")
        exe = _find_python_ge310() or _bootstrap_python()
        if not exe:
            _die("Python >=3.10 required. None found and auto-install failed. "
                 "Install Python 3.10+ (winget install --id Python.Python.3.12, or "
                 "https://python.org) and re-run.")
        _step(f"re-launching installer under {exe}")
        # Re-exec this script under the suitable interpreter (preserves argv).
        os.execv(exe, [exe, str(Path(__file__).resolve())] + sys.argv[1:])
    if not _bin("claude"):
        _die("Claude Code (`claude`) not found on PATH. Install it (https://docs.anthropic.com/claude-code) and re-run.")
    _step(f"python {platform.python_version()} OK; claude found.")


def _venv_has_pkg(import_name: str) -> bool:
    """True if `import_name` imports successfully inside the venv python. Used to
    checkpoint make_venv so a re-run after an interrupted install doesn't redo the
    slow dependency resolution from scratch."""
    py = _venv_bin("python")
    if not Path(py).is_file():
        return False
    rc = subprocess.run([py, "-c", f"import {import_name}"], check=False,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    return rc == 0


def make_venv() -> None:
    CHAT_BRIDGE.mkdir(parents=True, exist_ok=True)
    if not VENV.is_dir():
        _step("creating venv at ~/.chat_bridge/venv …")
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    pip = _venv_bin("pip")
    # Ensure uv in the venv (verbose: no -q, so a slow install isn't mistaken for a hang).
    if not _venv_has_pkg("uv"):
        _step("installing uv into the venv …")
        subprocess.run([pip, "install", "-U", "uv"], check=True)
    uv = _venv_bin("uv")
    # The launcher/doctor/keeper (run-bridge.* → python -m mcp_channel.launcher) import
    # feishu_api (→ lark-oapi), mcp, and (Windows) pywinpty FROM THIS VENV. The MCP server
    # context (A) gets its deps via uvx at runtime, but the management CLI context (B)
    # does not — so install the package + platform extra into the venv too.
    extra = "[windows]" if os.name == "nt" else ""
    target = f"{REPO}{extra}"
    # Checkpoint: if the package already imports in the venv, a previous run finished
    # this step — skip the (slow) reinstall. Lets an interrupted install resume cheaply.
    if _venv_has_pkg("feishu_api") and _venv_has_pkg("mcp_channel") and \
            (os.name != "nt" or _venv_has_pkg("winpty")):
        _step("venv already has the bridge package + deps; skipping install.")
    else:
        suffix = "" if not extra else " (extra: windows)"
        _step(f"installing bridge package + deps via uv (faster than pip){suffix} …")
        # uv pip resolves + installs in seconds vs pip's minutes; -e editable so
        # `git pull` of the repo is reflected without reinstall. Fall back to pip.
        rc = subprocess.run([uv, "pip", "install", "-e", target], check=False).returncode
        if rc != 0:
            # editable may fail on some setups; fall back to a plain path install.
            subprocess.run([uv, "pip", "install", target], check=True)
    _step(f"venv ready; uvx at {_venv_bin('uvx')}")


def fetch_repo() -> None:
    if REPO.is_dir() and (REPO / ".git").is_dir():
        _step(f"repo exists at {REPO}; pulling ({REPO_REF}) …")
        subprocess.run(["git", "-C", str(REPO), "fetch", "-q"], check=False)
        subprocess.run(["git", "-C", str(REPO), "checkout", "-q", REPO_REF], check=False)
        subprocess.run(["git", "-C", str(REPO), "pull", "-q"], check=False)
        return
    if _bin("git"):
        _step(f"git clone {REPO_URL}@{REPO_REF} -> {REPO}")
        subprocess.run(["git", "clone", "-q", "--branch", REPO_REF, REPO_URL, str(REPO)], check=True)
    else:
        _step("git not found; downloading tarball via urllib …")
        url = f"{REPO_URL}/archive/refs/heads/{REPO_REF}.tar.gz"
        with tempfile.TemporaryDirectory() as td:
            tgz = Path(td) / "repo.tar.gz"
            urllib.request.urlretrieve(url, tgz)
            with tarfile.open(tgz) as tf:
                tf.extractall(td)
            extracted = Path(td) / f"{DIR_NAME}-{REPO_REF}"
            shutil.move(str(extracted), str(REPO))


def configure_mcp() -> None:
    """Rewrite .mcp.json to use the venv's uvx (absolute, OS-correct) + the local repo.
    On Windows, add --extra windows so the uvx-fetched env includes pywinpty."""
    import json
    uvx = _venv_bin("uvx")
    args = ["--from", str(REPO)]
    if os.name == "nt":
        args += ["--extra", "windows"]
    args += ["feishu-channel"]
    mcp = {"mcpServers": {"feishu": {"command": uvx, "args": args}}}
    (REPO / ".mcp.json").write_text(json.dumps(mcp, indent=2), encoding="utf-8")
    _step(f".mcp.json -> venv uvx ({uvx}) + repo ({REPO})" +
          (" (+windows)" if os.name == "nt" else ""))


def install_skill() -> None:
    src = REPO / SKILL_SRC
    if not src.is_file():
        _step(f"skill source not found at {src} (skipping)"); return
    text = src.read_text(encoding="utf-8")
    # The source skill carries {{REPO}}/{{PY}} placeholders so it is not pinned to a
    # developer's machine; substitute the concrete install path + venv interpreter here.
    text = text.replace("{{REPO}}", str(REPO))
    text = text.replace("{{PY}}", _venv_bin("python"))
    SKILL_DST.parent.mkdir(parents=True, exist_ok=True)
    SKILL_DST.write_text(text, encoding="utf-8")
    _step(f"installed run skill -> {SKILL_DST}")


def main() -> None:
    _step(f"installing bridge into {CHAT_BRIDGE} (ref={REPO_REF})")
    _warn_native_windows()
    preflight()
    fetch_repo()        # fetch before make_venv: the venv installs the repo package
    make_venv()
    configure_mcp()
    install_skill()
    rb = "run-bridge.sh" if os.name != "nt" else "run-bridge.bat"
    print("\n[install] DONE.\n"
          f"  Repo:    {REPO}\n"
          f"  Venv:    {VENV}\n"
          f"  uvx:     {_venv_bin('uvx')}\n"
          f"  Skill:   {SKILL_DST}\n"
          f"  Run via: {REPO}/{rb}   (or tell your agent: 'run the feishu bridge')\n"
          f"  Creds:   put FEISHU_APP_ID/SECRET in {REPO}/.env  (the skill will prompt you)")


if __name__ == "__main__":
    main()
