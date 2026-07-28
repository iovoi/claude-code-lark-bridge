"""Offline regression test: `up()` must mint a FRESH session id and never reuse
the one persisted in bridge.session.

Bug: up() used to reuse bridge.session's id. That file is preserved across stop()
so `mode` can --resume it, but up() pins a NEW session via --session-id (not
--resume), so reusing an old id gives no continuity and can collide with a live
session -> claude refuses "Session ID ... is already in use" and the bridge
never comes up. This test seeds that colliding id and asserts up() ignores it.

Run:  python3 tests/test_launcher_session.py
"""
import sys
import uuid
from pathlib import Path


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from mcp_channel import launcher as L
    import mcp_channel.doctor as doctor

    fixture = Path(__file__).resolve().parent / "_session_fixture"
    if fixture.exists():
        for child in fixture.glob("*"):
            child.unlink()
    fixture.mkdir(parents=True, exist_ok=True)

    # Point state at the fixture so we never touch the real ~/.feishu-bridge.
    L.STATE_DIR = fixture
    L.SESSION_FILE = fixture / "bridge.session"
    L.PID_FILE = fixture / "bridge.pid"

    # The poison: a persisted id that collides with a live (in-use) session.
    colliding = "738cc5c4-013c-401d-bc23-03d72db333ec"
    L.SESSION_FILE.write_text(colliding)

    # --- monkeypatch the heavy / external pieces of up() ---
    doctor.run_doctor = lambda **k: 0                      # creds pass
    L.time.sleep = lambda *_a, **_k: None                  # no real waiting

    calls = {"n": 0}
    def fake_pids(proc_root="/proc"):                      # first call (the "already
        calls["n"] += 1                                    # running" guard) -> none,
        return [] if calls["n"] == 1 else [123]            # then the bridge is "up"

    L._bridge_pids = fake_pids
    L._log_tail = lambda n=800: "connected to wss://msg-frontier.feishu.cn/ws/v2 ok"

    spawned = {}
    class FakePopen:
        def __init__(self, argv, **kw):
            spawned["argv"] = argv
        def poll(self):
            return 0
    L.subprocess.Popen = FakePopen

    # --- run up() ---
    rc = L.up("auto")
    assert rc == 0, f"up() should succeed, got rc={rc}"

    # the keeper is spawned as: python -m mcp_channel.launcher keeper <mode> <session_id>
    sid = spawned["argv"][-1]
    print("[session] keeper spawned with session id:", sid)

    # MUST NOT reuse the colliding persisted id
    assert sid != colliding, f"up() reused the stale/in-use id {colliding!r}"
    # MUST be a valid uuid4
    u = uuid.UUID(sid)
    assert u.version == 4, f"expected uuid4, got version {u.version}"
    # up() must have written that same fresh id to bridge.session (authoritative)
    assert L.SESSION_FILE.read_text().strip() == sid, "SESSION_FILE != spawned id"
    # the helper mints a distinct id each call
    assert L._new_session_id() != L._new_session_id(), "uuid not fresh"

    print("[session] colliding persisted id was ignored:", colliding)
    print("\nSESSION OK")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("SESSION FAILED (assertion):", e, file=sys.stderr); sys.exit(1)
    except Exception as e:
        import traceback; traceback.print_exc()
        print("SESSION FAILED:", repr(e), file=sys.stderr); sys.exit(1)
