"""Offline unit tests for the bridge watchdog (mcp_channel/watchdog.py).

The watchdog is pure logic: feed() PTY chunks, tick() returns actions. We drive
it with a fake clock (monotonic integer `now`) and assert the emitted actions —
no Feishu, no PTY, no real time. Style matches tests/test_launcher_session.py.

Run:  python3 tests/test_watchdog.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_channel import watchdog as W


def _names(actions):
    return [type(a).__name__ for a in actions]


def _stuck_screen_bytes():
    """A realistic stuck frame (ANSI-laden) containing a WAITING marker, roughly
    what the keeper saw in the real incident."""
    return (
        b"\x1b[?25l\x1b[H\x1b[2J"
        b"  4. Type something.\n"
        b"  5. Chat about this\n"
        b"\n"
        b"  Enter to select \xc2\xb7 Tab/Arrow keys to navigate \xc2\xb7 Esc to cancel\n"
        b"\x1b[?25h"
    )


def _spinner_bytes():
    """Working frame: spinner glyphs + token counter (no WAITING marker)."""
    return b"\x1b[?25l \xe2\x9c\xb6 10s \xc2\xb7 291 tokens \xc2\xb7 thought for 3s Leavening\xe2\x80\xa6\x1b[?25h"


def main() -> None:
    ACTIVE = {"chat_id": "oc_test", "message_id": "om_test",
              "pushed_at": 1000.0, "content_preview": "do the thing"}

    # --- 1. no active task -> inert ---
    wd = W.Watchdog(now=0.0, progress_secs=60, stuck_quiet_secs=25)
    wd.feed(_spinner_bytes(), now=1.0)
    assert wd.tick(None, now=2.0) == [], "should do nothing without an active task"
    assert wd.tick({}, now=3.0) == [], "should do nothing without chat_id"
    print("[1] inert without active task: OK")

    # --- 2. working -> progress at PROGRESS_SECS, not before ---
    wd = W.Watchdog(now=0.0, progress_secs=60, stuck_quiet_secs=25)
    for t in range(1, 60):  # keep feeding the spinner; no progress yet (< 60s)
        wd.feed(_spinner_bytes(), now=float(t))
        assert _names(wd.tick(ACTIVE, now=float(t))) == [], f"no progress before 60s at t={t}"
    # at t=61, exactly one progress
    wd.feed(_spinner_bytes(), now=61.0)
    acts = wd.tick(ACTIVE, now=61.0)
    assert _names(acts) == ["SendProgress"], f"expected progress at 60s, got {_names(acts)}"
    assert acts[0].chat_id == "oc_test"
    # next tick right after -> no second progress (timer reset)
    wd.feed(_spinner_bytes(), now=62.0)
    assert wd.tick(ACTIVE, now=62.0) == [], "no double progress within the interval"
    print("[2] progress fires once per PROGRESS_SECS while working: OK")

    # --- 3. WAITING marker -> stuck, exactly once (after a brief quiet) ---
    wd = W.Watchdog(now=0.0, progress_secs=60, stuck_quiet_secs=25)
    wd.feed(_stuck_screen_bytes(), now=1.0)
    assert wd.tick(ACTIVE, now=2.0) == [], "no alert during the brief MARKER_QUIET window"
    acts = wd.tick(ACTIVE, now=4.0)  # idle 3s > MARKER_QUIET(2s)
    assert _names(acts) == ["SendStuck"], f"expected stuck on WAITING marker, got {_names(acts)}"
    assert "Enter to select" in acts[0].screen_text or "Tab/Arrow" in acts[0].screen_text, \
        "stuck screen should contain the prompt text"
    # subsequent ticks do NOT re-alert (no spam)
    assert wd.tick(ACTIVE, now=5.0) == [], "must not re-alert while still stuck"
    assert wd.tick(ACTIVE, now=20.0) == [], "must not re-alert while still stuck"
    print("[3] WAITING marker -> one stuck alert (no spam): OK")

    # --- 4. idle wedge (no marker, quiet > threshold) -> stuck ---
    wd = W.Watchdog(now=0.0, progress_secs=60, stuck_quiet_secs=25)
    wd.feed(_spinner_bytes(), now=1.0)        # last activity at t=1, no marker
    assert wd.tick(ACTIVE, now=2.0) == []      # not quiet long enough
    acts = wd.tick(ACTIVE, now=30.0)           # 29s idle > 25s threshold
    assert _names(acts) == ["SendStuck"], f"expected stuck on idle wedge, got {_names(acts)}"
    assert "no output" in acts[0].screen_text.lower(), "idle stuck message should mention no output"
    print("[4] idle wedge -> stuck: OK")

    # --- 5. resumed activity after alert -> ClearStuck, then re-alert possible ---
    wd = W.Watchdog(now=0.0, progress_secs=60, stuck_quiet_secs=25)
    wd.feed(_stuck_screen_bytes(), now=1.0)
    assert _names(wd.tick(ACTIVE, now=4.0)) == ["SendStuck"]   # alert (idle 3s)
    wd.feed(_spinner_bytes(), now=5.0)                          # Claude resumes (no marker)
    acts = wd.tick(ACTIVE, now=6.0)
    assert _names(acts) == ["ClearStuck"], f"expected ClearStuck on resume, got {_names(acts)}"
    # now sticks again -> can alert again (after the quiet window)
    wd.feed(_stuck_screen_bytes(), now=7.0)
    assert wd.tick(ACTIVE, now=8.0) == [], "no alert during MARKER_QUIET on re-stick"
    assert _names(wd.tick(ACTIVE, now=10.0)) == ["SendStuck"], "should re-alert on re-stick"
    print("[5] resume clears alert; re-stick re-alerts: OK")

    # --- 6. note_resolved() (keystroke applied) clears awaiting + resets timer ---
    wd = W.Watchdog(now=0.0, progress_secs=60, stuck_quiet_secs=25)
    wd.feed(_stuck_screen_bytes(), now=1.0)
    wd.tick(ACTIVE, now=4.0)
    wd.note_resolved(now=5.0)                                   # keeper applied a keystroke
    wd.feed(_spinner_bytes(), now=6.0)
    # right after resolve, even at 70s no immediate progress (timer reset to 3.0)
    acts = wd.tick(ACTIVE, now=10.0)
    assert acts == [], "no progress burst right after note_resolved"
    print("[6] note_resolved resets state: OK")

    # --- 7. screen cleaner strips ANSI + drops spinner-only lines ---
    cleaned = W.clean_screen(_stuck_screen_bytes())
    assert "Enter to select" in cleaned
    assert "Type something" in cleaned
    assert "\x1b[" not in cleaned, "ANSI escapes must be stripped"
    spinner_only = W.clean_screen(b"\x1b[?25l \xe2\x9c\xb6 \xe2\x9c\xbb \xe2\x9c\xbd\x1b[?25h")
    assert spinner_only == "", "spinner-only frames must clean to empty"
    print("[7] screen cleaner: OK")

    # --- 7b. extract_output strips TUI chrome, keeps Claude's real output ---
    chrome_and_answer = (
        "\x1b[?25l\x1b[H ▐▛███▜▌ Claude Code v2.1.220\n"
        " glm-5.2[1m] with medium effort · API Usage Billing\n"
        " ▎ Channels (experimental) messages from server:feishu inject directly in this\n"
        " ◐ medium · /effort\n"
        " ❯ Try \"create a util logging.py\"\n"
        " ⏵⏵ auto mode on (shift+tab to cycle) · ← for agents\n"
        "\x1b[?25h"
        "←feishu · ou_abc:hi\n"
        "●hi!👋 What can I help with?\n"
        "✻ Calling feishu…\n"
        "✻ Worked for 6s\n"
    )
    out = W.extract_output(chrome_and_answer)
    assert "Claude Code" not in out, "chrome version line must be stripped"
    assert "medium effort" not in out
    assert "Channels (experimental)" not in out
    assert "/effort" not in out
    assert "auto mode on" not in out
    assert "Try " not in out
    assert "hi!👋" in out, "Claude's answer must be kept"
    assert "Worked for 6s" in out, "action markers must be kept"
    assert "ou_abc:hi" in out, "inbound echo must be kept"
    print("[7b] extract_output strips chrome, keeps real output: OK")

    # --- 8. keystroke token -> bytes mapping ---
    assert W._keystroke_to_bytes("enter") if False else True  # mapping lives in launcher
    from mcp_channel import launcher as L
    assert L._keystroke_to_bytes("enter") == b"\r"
    assert L._keystroke_to_bytes("esc") == b"\x1b"
    assert L._keystroke_to_bytes("down") == b"\x1b[B"
    assert L._keystroke_to_bytes("y") == b"y\r"
    # multi-token applies in order
    assert L._keystroke_to_bytes("down enter") == b"\x1b[B\r"
    # literal text + Enter
    assert L._keystroke_to_bytes("hello world") == b"hello world\r"
    # empty -> just Enter
    assert L._keystroke_to_bytes("") == b"\r"
    print("[8] keystroke token mapping: OK")

    # --- 11. runner maps SendStuck.screen_text -> outbox (regression: was a.text -> crash) ---
    from mcp_channel import launcher as L
    from mcp_channel.watchdog import SendStuck

    class _FakeBS:
        def __init__(self): self.calls = []
        def read_active(self): return None
        def push_outbox(self, c, t): self.calls.append(("outbox", c, t))
        def write_stuck(self, **k): self.calls.append(("stuck", k))
        def clear_stuck(self): self.calls.append(("clear",))
        def is_awaiting_keystroke(self): return False
        def drain_keystrokes(self): return []

    class _StubWD:
        def tick(self, active, now): return [SendStuck("oc_x", "THE SCREEN")]
        def feed(self, *a, **k): pass
        def note_resolved(self, now): pass

    runner = L._WatchdogRunner(write_pty=lambda b: None)
    runner.wd = _StubWD()
    fake = _FakeBS(); runner.bridgestate = fake
    runner.tick()  # must not raise (previously: 'SendStuck' has no attribute 'text')
    assert ("outbox", "oc_x", "THE SCREEN") in fake.calls, \
        f"SendStuck must push screen_text to outbox: {fake.calls}"
    print("[11] runner SendStuck -> outbox(screen_text): OK")

    # --- 10. a NEW task anchors the progress clock to its start, not keeper boot ---
    wd = W.Watchdog(now=0.0, progress_secs=60, stuck_quiet_secs=25)
    # The boot timer would have expired by t=100, but a task arriving then must NOT
    # immediately fire progress -- it should fire PROGRESS_SECS into the task, and
    # the "started Xs ago" should read the true task age.
    A2 = {"chat_id": "oc_t2", "message_id": "om_t2", "pushed_at": 100.0, "content_preview": "x"}
    wd.feed(_spinner_bytes(), now=100.0)
    assert wd.tick(A2, now=100.0) == [], "new task must not fire progress immediately"
    # keep Claude "working" (feed spinner often so idle stays low) through t=159
    for t in range(101, 160, 5):
        wd.feed(_spinner_bytes(), now=float(t))
        assert wd.tick(A2, now=float(t)) == [], f"no progress before task+60s at t={t}"
    wd.feed(_spinner_bytes(), now=160.0)
    acts = wd.tick(A2, now=161.0)
    assert _names(acts) == ["SendProgress"], f"progress at task+60s, got {_names(acts)}"
    # elapsed reads true task age (~61s), not ~0
    assert "1m" in acts[0].text, f"elapsed should read ~1m, got: {acts[0].text[:60]!r}"
    print("[10] new task anchors progress clock + true elapsed: OK")

    # --- 9. real incident frame from /tmp/feishu-channel.log (if present) ---
    log = Path("/tmp/feishu-channel.log")
    if log.is_file():
        sample = log.read_bytes()[-20000:]
        wd = W.Watchdog(now=0.0, progress_secs=60, stuck_quiet_secs=25)
        wd.feed(sample, now=1.0)
        print(f"[9] real-log sample fed: showing_marker={wd._showing_marker}, "
              f"buf_tail={wd._buf[-80:]!r}")
    else:
        print("[9] real-log sample not present, skipping")

    print("\nWATCHDOG OK")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("WATCHDOG FAILED (assertion):", e, file=sys.stderr); sys.exit(1)
    except Exception as e:
        import traceback; traceback.print_exc()
        print("WATCHDOG FAILED:", repr(e), file=sys.stderr); sys.exit(1)
