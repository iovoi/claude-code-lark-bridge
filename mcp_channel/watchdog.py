"""Bridge watchdog: tell working from stuck, and say so over Feishu.

Lives in the keeper process (the only component that sees Claude's raw TTY
stream). PURE LOGIC + message composition — it emits *actions* and never touches
Feishu or the PTY directly, so it is fully unit-testable offline. The keeper
(`mcp_channel/launcher.py`) wires actions to a sender thread (Feishu messages)
and the keystroke drain (PTY writes), and feeds it PTY chunks.

Detection model
---------------
Claude actively working emits a near-continuous stream of TTY bytes (spinner
redraws, token counters, tool markers). When it wedges on an interactive prompt
the stream goes quiet AND/OR a recognizable prompt is the last rendered frame.
So:

  STUCK if   a WAITING marker is present in recent output   (explicit prompt)
         OR  no PTY output for > STUCK_QUIET_SECS           (idle wedge)

  WORKING otherwise -> send a progress digest every PROGRESS_SECS.

When STUCK is first detected we emit SendStuck (the keeper forwards the screen
and enters "awaiting keystroke" mode, where the user's Feishu reply is typed into
the PTY). We emit it ONCE until activity resumes (the `alerted` flag), so we
don't spam. Resumed activity emits ClearStuck and resets the progress timer.

Config (read via feishu_api.cred, so .env works; defaults shown):
  FEISHU_WATCHDOG=1                      master on/off
  FEISHU_WATCHDOG_PROGRESS_SECS=60       progress interval while working
  FEISHU_WATCHDOG_STUCK_QUIET_SECS=25    idle threshold -> suspect stuck
"""
from __future__ import annotations
import re
from dataclasses import dataclass

import feishu_api

# --- config (read once at import; cheap, and the keeper is long-lived) ---
def _on() -> bool:
    return feishu_api.cred("FEISHU_WATCHDOG") != "0"


def _progress_secs() -> float:
    try:
        v = float(feishu_api.cred("FEISHU_WATCHDOG_PROGRESS_SECS"))
        return v if v > 0 else 60.0
    except Exception:
        return 60.0


def _stuck_quiet_secs() -> float:
    try:
        v = float(feishu_api.cred("FEISHU_WATCHDOG_STUCK_QUIET_SECS"))
        return v if v > 0 else 25.0
    except Exception:
        return 25.0


# Phrases that appear in Claude Code's interactive prompts. Matched against the
# cleaned (ANSI-stripped, whitespace-collapsed) recent output. Extend freely.
WAITING_MARKERS = (
    "Enter to select",
    "Tab/Arrow",
    "Esc to cancel",
    "Enter to confirm",
    "shift+tab to cycle",
    "to navigate",
    "Yes,Iaccept",
    "No,exit",
    " Yes",
    " No,",
    "Do you trust",
    "approve",
)

# ANSI stripping (str-based). Matches the keeper's regex (launcher.py:340) plus
# a few OSC / cursor-report sequences seen in Claude's TUI frames.
_ANSI = re.compile(
    r"\x1b\[[0-9;?]*[a-zA-Z]"        # CSI
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC
    r"|\x1b[<>][0-9a-z]*"            # cursor reports
    r"|\x1b[=>78]"                   # charset / save-cursor variants
)

# Lines that are only spinner glyphs / box-drawing noise -> drop from snippets.
_NOISE_LINE = re.compile(r"^[⠁-⣿✶✻✽✢✳✻✽·●◐⠂⠐⠒⠓⠙⠹⠸⠼⠴⠦⠧⠇⠏▘▝▗▖▙▟▛▜▌▐─━│▎▏ +]*$")

# Substrings / glyphs that mark a line as Claude Code TUI CHROME (the logo,
# version, model, cwd, channel banner, mode line, hint text, suggestion prompt).
# Used to extract only Claude's ACTUAL output (its answer text + action markers
# like "Calling feishu…" / "Worked for 6s") for digests.
_CHROME_MARKERS = (
    "Claude Code", "medium effort", "API Usage Billing", "Channels (experimental)",
    "dangerously-load-development-channels", "/effort", "shift+tab", "auto mode on",
    "esc to interrupt", "for agents", "Try \"", "for local development",
    "Resume this session", "claude --resume",
)
_LOGO_GLYPHS = "▘▝▛▜▙▟▌▐▎"  # box-drawing pieces of the Claude Code header/logo


def _meaningful_lines(raw: bytes | str) -> list[str]:
    """Strip ANSI + control bytes, drop spinner-only lines, collapse whitespace.
    Returns the list of surviving lines (chrome NOT yet removed)."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    raw = _ANSI.sub("", raw)
    raw = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", raw)
    out = []
    for ln in raw.splitlines():
        s = ln.strip()
        if not s or _NOISE_LINE.match(s):
            continue
        out.append(re.sub(r"\s+", " ", s))
    return out


def clean_screen(raw: bytes | str, max_chars: int = 600) -> str:
    """Cleaned text (ANSI stripped, spinner lines dropped). Used for marker
    detection, which needs to SEE chrome phrases like 'shift+tab to cycle'."""
    out = "\n".join(_meaningful_lines(raw))
    return out[-max_chars:] if len(out) > max_chars else out


def extract_output(raw: bytes | str, max_chars: int = 700) -> str:
    """Claude's ACTUAL output for digests: cleaned text with TUI chrome removed
    (logo, version, model, cwd, channel banner, mode line, hint/suggestion
    text). Keeps the inbound-message echo, Claude's answer text, and action
    markers (Calling/Worked/Thought/Replied…)."""
    kept = []
    for s in _meaningful_lines(raw):
        if any(m in s for m in _CHROME_MARKERS):
            continue
        if any(g in s for g in _LOGO_GLYPHS):
            continue
        if s == "❯" or (s.startswith("❯") and len(s) <= 3):
            continue
        kept.append(s)
    out = "\n".join(kept)
    return out[-max_chars:] if len(out) > max_chars else out


# --- actions (the keeper interprets these) ---
@dataclass
class SendProgress:
    chat_id: str
    text: str


@dataclass
class SendStuck:
    chat_id: str
    screen_text: str


@dataclass
class ClearStuck:
    """Claude resumed after a stuck alert -> the keeper clears awaiting_keystroke."""
    pass


class Watchdog:
    """Feed PTY chunks; call tick() each loop iteration (~1s).

    `tick(active, now)` returns a list of actions. `active` is the parsed
    bridge.active.json dict (or None = no task in flight -> the watchdog is
    inert). `now` is a monotonic/clock timestamp supplied by the caller (the
    keeper), so this class never calls time itself and stays deterministic in
    tests.
    """

    # A marker seen in a chunk makes us suspect a prompt, but only after a short
    # quiet period (MARKER_QUIET) — active streaming keeps producing bytes and
    # resets idle, so a stray " Yes" in prose won't false-positive mid-work.
    MARKER_QUIET = 2.0

    def __init__(self, now: float,
                 progress_secs: float | None = None,
                 stuck_quiet_secs: float | None = None) -> None:
        self.enabled = _on()
        self.progress_secs = progress_secs if progress_secs is not None else _progress_secs()
        self.stuck_quiet_secs = stuck_quiet_secs if stuck_quiet_secs is not None else _stuck_quiet_secs()
        self._buf = ""                 # rolling cleaned output for snippets
        self._buf_max = 8192
        self._last_activity_at = now
        self._last_progress_sent_at = now
        self._alerted = False          # already sent a stuck alert (don't spam)
        self._awaiting = False         # in-memory mirror of awaiting_keystroke
        self._showing_marker = False   # last non-empty chunk contained a WAITING marker
        self._last_task_mid = None     # active task's message_id (detect task changes)

    def note_resolved(self, now: float) -> None:
        """The keeper applied a keystroke (or Claude self-healed externally) ->
        drop alerted/awaiting and reset the progress timer so we don't immediately
        spam a progress note. Called by the keeper after it types a keystroke."""
        self._alerted = False
        self._awaiting = False
        self._last_progress_sent_at = now

    # --- feeding ---
    def feed(self, data: bytes, now: float) -> None:
        if not data:
            return
        self._last_activity_at = now
        # Marker detection needs to SEE chrome phrases (e.g. 'shift+tab to cycle'):
        cleaned = clean_screen(data, max_chars=self._buf_max)
        # The digest buffer holds only Claude's ACTUAL output (chrome stripped):
        filtered = extract_output(data, max_chars=self._buf_max)
        if filtered:
            self._buf = (self._buf + "\n" + filtered)
            if len(self._buf) > self._buf_max:
                self._buf = self._buf[-self._buf_max:]
        # A prompt is "currently shown" iff the MOST RECENT non-empty chunk had a
        # marker. Idle ticks (no chunk) leave it unchanged, so a prompt that went
        # quiet stays detected; a resume chunk without a marker clears it.
        self._showing_marker = self._chunk_has_marker(cleaned)

    @staticmethod
    def _chunk_has_marker(text: str) -> bool:
        if not text:
            return False
        compact = re.sub(r"\s+", "", text)
        return any(re.sub(r"\s+", "", m) in compact for m in WAITING_MARKERS)

    # --- detection ---
    def tick(self, active: dict | None, now: float) -> list:
        if not self.enabled or not active or not active.get("chat_id"):
            return []
        # New task? Anchor the progress clock to its start so the first progress
        # digest fires PROGRESS_SECS into the task (not PROGRESS_SECS after keeper
        # boot), and so the "started Xs ago" line reads the true task age. Also
        # drop any stale stuck/alerted state from a prior task.
        mid = active.get("message_id")
        if mid != self._last_task_mid:
            self._last_task_mid = mid
            self._last_progress_sent_at = now
            self._alerted = False
            self._awaiting = False
        idle_secs = now - self._last_activity_at
        marker_stuck = self._showing_marker and idle_secs > self.MARKER_QUIET
        idle_stuck = idle_secs > self.stuck_quiet_secs
        is_stuck = marker_stuck or idle_stuck
        waiting = marker_stuck
        actions: list = []

        if is_stuck:
            if not self._alerted:
                screen = self._screen_for_message(active, waiting, idle_secs, now)
                actions.append(SendStuck(active["chat_id"], screen))
                self._alerted = True
                self._awaiting = True
            # already alerted: stay quiet (the keeper is holding for a keystroke)
        else:
            if self._alerted or self._awaiting:
                # Claude resumed -> drop awaiting mode, reset progress timer.
                actions.append(ClearStuck())
                self._alerted = False
                self._awaiting = False
                self._last_progress_sent_at = now
            elif now - self._last_progress_sent_at >= self.progress_secs:
                actions.append(SendProgress(active["chat_id"], self._progress_text(active, now)))
                self._last_progress_sent_at = now
        return actions

    # --- message composition (3 parts: status / next step / Claude's output) ---
    def _compose(self, status: str, suggestion: str, active: dict, now: float) -> str:
        elapsed = self._elapsed(active.get("pushed_at", 0), now)
        output = self._buf[-700:] if self._buf else "(no recent output captured)"
        return (
            f"{status}\n"
            f"Task: {active.get('content_preview', '')!r} · started {elapsed} ago\n\n"
            f"Next step: {suggestion}\n\n"
            f"Claude's recent output:\n{output}"
        )

    def _screen_for_message(self, active: dict, waiting: bool, idle_secs: float,
                            now: float) -> str:
        if waiting:
            status = "⛔ Waiting for input — Claude is showing an interactive prompt."
            suggestion = ("Reply with the option or your answer (e.g. '1', 'y', or "
                          "the text); it will be typed into the prompt + Enter. "
                          "Special keys: enter  esc  tab  up  down.")
        else:
            status = (f"⛔ Idle — no output from Claude for {int(idle_secs)}s. "
                      "It may have finished without sending a reply.")
            suggestion = ("If you didn't get a reply in Feishu, resend your message "
                          "to start fresh — or reply 'enter' to nudge the prompt.")
        return self._compose(status, suggestion, active, now)

    def _progress_text(self, active: dict, now: float) -> str:
        status = "🟢 Working — Claude is still on your task."
        suggestion = "No action needed — I'll send another update in 60s."
        return self._compose(status, suggestion, active, now)

    @staticmethod
    def _elapsed(pushed_at: float, now: float) -> str:
        """Humanized now-pushed_at. Both are real epoch seconds supplied by the
        keeper (the watchdog never reads the clock itself, so tests stay fakeable)."""
        try:
            d = max(0.0, float(now) - float(pushed_at))
        except Exception:
            return "?"
        m, s = divmod(int(d), 60)
        if m >= 60:
            h, m = divmod(m, 60)
            return f"{h}h{m}m"
        return f"{m}m{s}s" if m else f"{s}s"
