#!/usr/bin/env python3
"""Cross-platform installer for the Feishu/Lark <-> Claude Code bridge.

Run via the thin wrappers: `./install.sh` (POSIX) or `install.bat` (Windows),
which just call `python3 install.py`.

Layout created under ~/.chat_bridge/:
  ~/.chat_bridge/venv/                 # venv: provides the `feishu-bridge` CLI (uv pip-installed)
  ~/.chat_bridge/<repo>/               # the bridge code (git clone, or curl tarball if no git)
  ~/.chat_bridge/<repo>/.env           # Feishu app credentials (written interactively)
The run skill is installed to ~/.claude/skills/feishu-bridge/.

The bridge drives Claude Code in non-interactive streaming mode — no PTY, no tmux,
identical on Windows / Mac / Linux.
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


_TOTAL_STEPS = 5  # preflight, fetch_repo, make_venv, credentials, skill
_step_n = 0


def _step(msg: str) -> None:
    """Sub-detail line within a phase (no number)."""
    print(f"[install]   {msg}", flush=True)


def _phase(label: str) -> None:
    """Announce the start of a numbered phase."""
    global _step_n
    _step_n += 1
    print(f"\n[install] [{_step_n}/{_TOTAL_STEPS}] {label} …", flush=True)


def _done(msg: str) -> None:
    """Announce a completed sub-task within a phase."""
    print(f"[install]   ✓ {msg}", flush=True)


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


def _print_upgrade_help() -> None:
    """Show the ways to get a Python >=3.10 when none was found on the machine."""
    print(
        "\n[install] Python >=3.10 is required; none was found on this machine.\n"
        "Approaches to upgrade:\n"
        "\n"
        "  1. winget (Windows):     winget install --id Python.Python.3.12 -e\n"
        "  2. python.org (any OS):  https://www.python.org/downloads/ — in the\n"
        "     installer, tick \"Add python.exe to PATH\"\n"
        "  3. Microsoft Store:      search \"Python 3.12\", click Install\n"
        "  4. uv (any OS):          uv python install 3.12\n"
        "\n"
        "Note: a new Python installs side-by-side (the old one is not removed).\n"
        "If `python --version` still reports the old version afterwards, check\n"
        "`where python`, disable the old Store alias (Settings > Apps > Advanced\n"
        "app settings > App execution aliases), or invoke the new interpreter\n"
        "explicitly:  py -3.12 install.py\n",
        flush=True)


def _offer_python_upgrade() -> str | None:
    """No Python >=3.10 found — show the upgrade approaches and (when the
    terminal is interactive) ask before auto-installing. Piped/non-interactive
    runs can't prompt, so they attempt the best-effort bootstrap directly."""
    _print_upgrade_help()
    if sys.stdin.isatty():
        try:
            ans = input("[install] Try to install Python 3.12 automatically now "
                        "(winget/uv)? [Y/n] ")
        except EOFError:
            ans = ""
        if ans.strip().lower() in ("n", "no"):
            _die("declined automatic Python install. Upgrade manually "
                 "(approaches above), then re-run the installer.")
    return _bootstrap_python()


def _relaunch_under(exe: str) -> "NoReturn":  # noqa: F821
    """Run this installer under `exe` (preserves argv) and exit with its code.

    Uses subprocess rather than os.execv: on Windows, execv does not quote
    arguments (the CRT joins them raw), so passing the script source via -c
    yields a mangled command line and the child dies silently — and the
    target is often a Store-Python app-execution-alias stub with its own
    exec quirks. subprocess.call quotes correctly and keeps console output
    attached.

    Handles the piped (`irm … | python`) case, where __file__ is '<stdin>' —
    resolving that path raises WinError 123 on Windows (illegal '<'/'>') and
    there's no file to re-run anyway; fetch the installer source from the
    repo into a temp file instead."""
    _step(f"re-launching installer under {exe}")
    argv = sys.argv[1:]
    tmp_path: str | None = None
    try:
        script = Path(__file__)
        if script.is_file():
            target = str(script.resolve())
        else:
            url = f"{REPO_URL}/raw/{REPO_REF}/install.py"
            _step(f"piped install (no script file); fetching {url}")
            try:
                src = urllib.request.urlopen(url, timeout=30).read()
            except OSError as e:
                _die(f"could not fetch {url} to re-run under {exe}: {e}. "
                     "Download install.py to a file and re-run: "
                     "irm <url> -OutFile install.py; python install.py")
            with tempfile.NamedTemporaryFile("wb", suffix=".py", prefix="chat-bridge-install-",
                                             delete=False) as tmp:
                tmp.write(src)
            tmp_path = tmp.name
            target = tmp_path
        rc = subprocess.call([exe, target] + argv)
        sys.exit(rc)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _platform_note() -> None:
    """The bridge is uniformly cross-platform (streaming print/pipe mode — no PTY/tmux),
    so there is no longer a native-Windows caveat. Print a short confirmation."""
    print(f"[install] platform: {platform.system()} (streaming mode — no PTY/tmux needed)",
          flush=True)


def preflight() -> None:
    if sys.version_info < (3, 10):
        _step(f"this Python is {platform.python_version()} (<3.10); looking for a newer one …")
        exe = _find_python_ge310()
        if exe:
            _relaunch_under(exe)
        # None found: show the upgrade approaches, then prompt/attempt an
        # automatic install before giving up.
        exe = _offer_python_upgrade()
        if exe:
            _relaunch_under(exe)
        _die("Python >=3.10 required. None found and the automatic upgrade did not "
             "complete — upgrade manually (approaches above), then re-run.")
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
        _done(f"venv created at {VENV}")
    else:
        _step(f"venv exists at {VENV}")
    pip = _venv_bin("pip")
    # Ensure uv in the venv (verbose: no -q, so a slow install isn't mistaken for a hang).
    if not _venv_has_pkg("uv"):
        _step("installing uv into the venv …")
        subprocess.run([pip, "install", "-U", "uv"], check=True)
        _done("uv installed")
    else:
        _step("uv already in venv")
    uv = _venv_bin("uv")
    # The bridge CLI (run-bridge.* → feishu-bridge) imports feishu_api (→ lark-oapi) and
    # the `bridge` package FROM THIS VENV. No mcp / pywinpty anymore.
    target = str(REPO)
    # Checkpoint: if the package already imports in the venv, a previous run finished
    # this step — skip the (slow) reinstall. Lets an interrupted install resume cheaply.
    # (feishu_api is now inside the bridge package as bridge.feishu_api, not top-level.)
    if _venv_has_pkg("bridge"):
        _step("bridge package + deps already present; skipping install.")
    else:
        _step("installing bridge package + deps via uv (faster than pip) …")
        # uv pip resolves + installs in seconds vs pip's minutes; -e editable so
        # `git pull` of the repo is reflected without reinstall. Fall back to pip.
        # --python: uv only auto-discovers VIRTUAL_ENV or a ./.venv near the cwd —
        # our venv lives at an arbitrary path (~/.chat_bridge/venv), so point uv
        # at its interpreter explicitly.
        venv_py = _venv_bin("python")
        rc = subprocess.run([uv, "pip", "install", "--python", venv_py, "-e", target],
                            check=False).returncode
        if rc != 0:
            # editable may fail on some setups; fall back to a plain path install.
            subprocess.run([uv, "pip", "install", "--python", venv_py, target], check=True)
        _done("bridge package + deps installed")
    _done(f"venv ready; feishu-bridge at {_venv_bin('feishu-bridge')}")


def fetch_repo() -> None:
    if REPO.is_dir() and (REPO / ".git").is_dir():
        _step(f"repo exists at {REPO}; pulling ({REPO_REF}) …")
        subprocess.run(["git", "-C", str(REPO), "fetch", "-q"], check=False)
        subprocess.run(["git", "-C", str(REPO), "checkout", "-q", REPO_REF], check=False)
        subprocess.run(["git", "-C", str(REPO), "pull", "-q"], check=False)
        _done(f"repo updated to {REPO_REF}")
        return
    if _bin("git"):
        _step(f"git clone {REPO_URL}@{REPO_REF} -> {REPO}")
        subprocess.run(["git", "clone", "-q", "--branch", REPO_REF, REPO_URL, str(REPO)], check=True)
        _done("repo cloned")
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
        _done("repo downloaded + extracted")
    _step(f"repo ready at {REPO}")


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
    _done("run skill installed")


# --- credential collection -------------------------------------------------

# Keys we manage in .env, in prompt order. None = blank-line spacer in the file.
_ENV_KEYS: list[tuple[str, str, bool]] = [
    # (env key, human prompt label, is_secret)
    ("FEISHU_APP_ID", "App ID (cli_…)", False),
    ("FEISHU_APP_SECRET", "App Secret", True),
    ("FEISHU_ALLOWED_OPEN_IDS", "Allowed open_id list (comma-sep, optional)", False),
    ("FEISHU_ALLOWED_CHAT_IDS", "Allowed chat_id list (comma-sep, optional)", False),
    ("FEISHU_EMOJI_WORKING", "Working emoji code (default: OnIt)", False),
    ("FEISHU_EMOJI_DONE", "Done emoji code (default: Done)", False),
]


def _parse_env(path: Path) -> dict[str, str]:
    """Parse a .env file into a {key: value} dict (comments/blank lines dropped).
    Mirrors feishu_api.load_env's value handling (quotes stripped)."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _prompt(label: str, default: str, secret: bool) -> str:
    """Read one value from stdin. Enter keeps the default; empty default => blank."""
    shown = f"  {label}"
    if default:
        shown += f" [{default}]" if not secret else f" [{'*' * 8}]"
    shown += ": "
    try:
        raw = input(shown)
    except EOFError:
        raw = ""
    val = raw.strip()
    if val:
        return val
    return default  # empty input keeps the existing/default value


def collect_credentials() -> None:
    """Interactively prompt for Feishu app credentials and write REPO/.env.

    Skipped (leaving .env to be filled later) when:
      * ``--no-creds`` is on the command line, or
      * stdin isn't a TTY (e.g. ``curl | python`` install, CI).

    Existing values in .env are offered as defaults so a re-run only changes what
    you retype. .env is gitignored, so secrets never enter the repo."""
    if "--no-creds" in sys.argv[1:]:
        _step("skipping credential prompt (--no-creds); fill .env manually.")
        return
    if not sys.stdin.isatty():
        _step("non-interactive shell; skipping credential prompt. Fill .env later:")
        _step(f"  edit {REPO / '.env'}  (FEISHU_APP_ID / FEISHU_APP_SECRET)")
        return

    env_file = REPO / ".env"
    current = _parse_env(env_file)
    _step(f"Feishu app credentials  (Developer Console > your app > Credentials)")
    _step("Press Enter to keep the value in [brackets]; leave blank to skip/empty.")
    print()
    collected: dict[str, str] = {}
    for key, label, secret in _ENV_KEYS:
        collected[key] = _prompt(label, current.get(key, ""), secret)
    print()

    # Build the .env: a short header + the managed keys + any pre-existing extras.
    lines = [
        "# Feishu/Lark bridge credentials — written by install.py.",
        "# This file is gitignored; keep secrets here, never in the repo.",
        "",
    ]
    defaults = {"FEISHU_EMOJI_WORKING": "OnIt", "FEISHU_EMOJI_DONE": "Done"}
    managed = {k for k, _, _ in _ENV_KEYS}
    for key, label, secret in _ENV_KEYS:
        val = collected.get(key, "") or defaults.get(key, "")
        if val:
            lines.append(f"{key}={val}")
    # Preserve any pre-existing keys we don't manage (e.g. FEISHU_DISABLE_WS).
    for key, val in current.items():
        if key not in managed and val:
            lines.append(f"{key}={val}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _done(f"credentials written to {env_file}")
    if not collected.get("FEISHU_APP_ID") or not collected.get("FEISHU_APP_SECRET"):
        _step("note: APP_ID/SECRET left blank — set them in .env before running the bridge.")
    if not collected.get("FEISHU_ALLOWED_OPEN_IDS") and not collected.get("FEISHU_ALLOWED_CHAT_IDS"):
        _step("note: no allowlist set — the bot will answer anyone who can reach it.")


def main() -> None:
    print(f"\n[install] Feishu/Lark <-> Claude Code bridge  (ref={REPO_REF})")
    print(f"[install] target: {CHAT_BRIDGE}\n")
    _platform_note()

    _phase("Preflight — Python + Claude Code")
    preflight()

    _phase("Fetch bridge repo")
    fetch_repo()        # fetch before make_venv: the venv installs the repo package

    _phase("Create venv + install dependencies")
    make_venv()

    _phase("Feishu app credentials")
    collect_credentials()

    _phase("Install run skill")
    install_skill()

    rb = "run-bridge.sh" if os.name != "nt" else "run-bridge.bat"
    print("\n[install] ===========================================")
    print("[install] DONE.\n"
          f"  Repo:          {REPO}\n"
          f"  Venv:          {VENV}\n"
          f"  CLI:           {_venv_bin('feishu-bridge')}\n"
          f"  Skill:         {SKILL_DST}\n"
          f"  Creds:         {REPO}/.env\n"
          f"  Start with:    {REPO}/{rb}   (or: feishu-bridge up)")


if __name__ == "__main__":
    main()
