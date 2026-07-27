"""Offline unit test for the launcher's /proc-based bridge process discovery.

`_bridge_pids()` / `_keeper_pids()` are the load-bearing piece of the wedge fix: they
let stop/status/up/mode find real bridge processes WITHOUT trusting the (often stale)
pid file. This test points the discovery at a synthetic /proc tree so it needs no real
bridge and runs anywhere.

Run:  .venv/bin/python tests/test_launcher_discover.py   (or: python3 tests/test_launcher_discover.py)
"""
import sys
from pathlib import Path


def _make_proc(root: Path, pid: int, argv: list[str]) -> None:
    """Create a synthetic /proc/<pid>/cmdline under `root`."""
    d = root / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "cmdline").write_bytes("\x00".join(argv).encode())


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from mcp_channel import launcher as L

    tmp = Path(__file__).resolve().parent / "_proc_fixture"
    if tmp.exists():
        for child in tmp.glob("[0-9]*"):
            (child / "cmdline").unlink()
            child.rmdir()
    tmp.mkdir(parents=True, exist_ok=True)

    # 1) a real bridge claude (matches both dev-channels + server:feishu)
    _make_proc(tmp, 1001, ["/home/x/.local/bin/claude",
                           "--dangerously-load-development-channels", "server:feishu",
                           "--permission-mode", "auto", "--resume", "abc"])
    # 2) a plain claude (no feishu channel) — must NOT match _bridge_pids
    _make_proc(tmp, 1002, ["/home/x/.local/bin/claude", "--resume", "other"])
    # 3) a launcher keeper (matches mcp_channel.launcher + keeper)
    _make_proc(tmp, 1003, ["python3", "-m", "mcp_channel.launcher", "keeper", "auto", "abc"])
    # 4) unrelated process
    _make_proc(tmp, 1004, ["bash", "-c", "sleep 1"])
    # 5) a bridge with dev-channels but a DIFFERENT channel — must NOT match
    _make_proc(tmp, 1005, ["claude", "--dangerously-load-development-channels",
                           "server:other", "--permission-mode", "auto"])

    bridge = L._bridge_pids(proc_root=str(tmp))
    keepers = L._keeper_pids(proc_root=str(tmp))

    assert bridge == [1001], f"bridge discovery wrong: {bridge}"
    assert keepers == [1003], f"keeper discovery wrong: {keepers}"
    assert 1002 not in bridge, "plain claude should not be flagged as a bridge"
    assert 1005 not in bridge, "non-feishu dev-channel claude should not be flagged"

    # default mode landed on auto (the MR's success criterion)
    assert L.DEFAULT_MODE == "auto", f"DEFAULT_MODE should be auto, got {L.DEFAULT_MODE!r}"

    # _pids_matching requires ALL needle tokens (subset), not just one
    both = L._pids_matching({"--dangerously-load-development-channels", "server:feishu"},
                            proc_root=str(tmp))
    assert both == [1001], f"token-subset match wrong: {both}"

    print("[discover] bridge_pids =", bridge)
    print("[discover] keeper_pids =", keepers)
    print("[discover] DEFAULT_MODE =", L.DEFAULT_MODE)
    print("\nDISCOVER OK")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("DISCOVER FAILED (assertion):", e, file=sys.stderr); sys.exit(1)
    except Exception as e:
        import traceback; traceback.print_exc()
        print("DISCOVER FAILED:", repr(e), file=sys.stderr); sys.exit(1)
