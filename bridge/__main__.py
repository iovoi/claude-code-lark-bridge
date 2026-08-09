"""CLI entry point: ``feishu-bridge up | status | stop | run``.

``up``/``status``/``stop`` manage a detached background bridge process (no PTY,
no tmux) via :mod:`bridge.supervisor`. ``run`` starts the bridge in the
foreground (used by the supervisor itself, or for manual/debug runs).

This module is wired incrementally across the build: subcommands are stubbed
until their owning modules (supervisor, runtime) land in Phase 5.
"""
from __future__ import annotations

import argparse
import sys

from . import __version__


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="feishu-bridge",
        description="Unified streaming Feishu/Lark <-> Claude Code bridge.",
    )
    parser.add_argument("--version", action="version", version=f"feishu-bridge {__version__}")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("up", help="Start the bridge as a detached background process.")
    sub.add_parser("status", help="Report whether the bridge is running (pid, session).")
    sub.add_parser("stop", help="Stop the running bridge.")
    run_p = sub.add_parser("run", help="Run the bridge in the foreground (debug / supervised).")
    run_p.add_argument(
        "--no-ws", action="store_true", help="Skip starting the Feishu websocket (offline/tests)."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    cmd = args.cmd or "status"

    if cmd == "run":
        # Wired in T5.4 (runtime).
        from .runtime import run_forever  # type: ignore

        return run_forever(no_ws=getattr(args, "no_ws", False))

    if cmd in ("up", "status", "stop"):
        # Wired in T5.5 (supervisor).
        from .supervisor import handle as supervisor_handle  # type: ignore

        return supervisor_handle(cmd)

    return 0


def run() -> None:
    """Console-script entry point (pyproject: feishu-bridge = bridge.__main__:run)."""
    raise SystemExit(main())


if __name__ == "__main__":
    run()
