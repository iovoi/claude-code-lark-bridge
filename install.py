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


def preflight() -> None:
    if sys.version_info < (3, 10):
        _die(f"Python >=3.10 required (have {platform.python_version()}). Install python3 and re-run.")
    if not _bin("claude"):
        _die("Claude Code (`claude`) not found on PATH. Install it (https://docs.anthropic.com/claude-code) and re-run.")
    _step(f"python {platform.python_version()} OK; claude found.")


def make_venv() -> None:
    CHAT_BRIDGE.mkdir(parents=True, exist_ok=True)
    if not VENV.is_dir():
        _step("creating venv at ~/.chat_bridge/venv …")
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    pip = _venv_bin("pip")
    _step("ensuring uv is installed in the venv …")
    subprocess.run([pip, "install", "-q", "-U", "uv"], check=True)
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
    """Rewrite .mcp.json to use the venv's uvx (absolute, OS-correct) + the local repo."""
    import json
    uvx = _venv_bin("uvx")
    mcp = {
        "mcpServers": {
            "feishu": {"command": uvx, "args": ["--from", str(REPO), "feishu-channel"]}
        }
    }
    (REPO / ".mcp.json").write_text(json.dumps(mcp, indent=2), encoding="utf-8")
    _step(f".mcp.json -> venv uvx ({uvx}) + repo ({REPO})")


def install_skill() -> None:
    src = REPO / SKILL_SRC
    if not src.is_file():
        _step(f"skill source not found at {src} (skipping)"); return
    SKILL_DST.parent.mkdir(parents=True, exist_ok=True)
    SKILL_DST.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    _step(f"installed run skill -> {SKILL_DST}")


def main() -> None:
    _step(f"installing bridge into {CHAT_BRIDGE} (ref={REPO_REF})")
    preflight()
    make_venv()
    fetch_repo()
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
