#!/usr/bin/env python3
"""
List unreplied (pending) messages for the LLM bridge to handle.

Pending = every message in conversation/*.jsonl whose message_id is NOT recorded
in conversation/.handled.json.

Usage:
    .venv/bin/python pending_messages.py           # print pending, oldest first
    .venv/bin/python pending_messages.py --seed    # mark ALL current messages as
                                                   # handled (catch up to "now")
Each pending line is a JSON object with:
    message_id, chat_id, chat_type, sender_open_id, text, ts, source_file
"""

import json
import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
CONVERSATION_DIR = Path(
    os.environ.get("FEISHU_CONVERSATION_DIR", str(PROJECT_DIR / "conversation"))
)
HANDLED_PATH = CONVERSATION_DIR / ".handled.json"


def load_handled() -> dict:
    if HANDLED_PATH.is_file():
        try:
            return json.loads(HANDLED_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_handled(handled: dict) -> None:
    CONVERSATION_DIR.mkdir(parents=True, exist_ok=True)
    HANDLED_PATH.write_text(
        json.dumps(handled, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def iter_messages():
    """Yield (source_file, record) for every message across all conversation files."""
    if not CONVERSATION_DIR.is_dir():
        return
    for path in sorted(CONVERSATION_DIR.glob("*.jsonl")):
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield path.name, json.loads(line)
                except Exception:
                    continue


def main() -> None:
    seed = "--seed" in sys.argv
    handled = load_handled()

    if seed:
        # Catch-up: record every existing message as already handled.
        count = 0
        for _, rec in iter_messages():
            mid = rec.get("message_id")
            if mid and mid not in handled:
                handled[mid] = {"text": rec.get("text"), "reply": None, "seeded": True}
                count += 1
        save_handled(handled)
        print(f"[seed] marked {count} existing messages as handled.")
        return

    # Normal mode: print pending (unhandled) messages, oldest first.
    pending = []
    for name, rec in iter_messages():
        mid = rec.get("message_id")
        if mid and mid not in handled:
            pending.append(
                {
                    "message_id": mid,
                    "chat_id": rec.get("chat_id"),
                    "chat_type": rec.get("chat_type"),
                    "sender_open_id": rec.get("sender_open_id"),
                    "text": rec.get("text"),
                    "ts": rec.get("ts"),
                    "source_file": name,
                }
            )

    for rec in pending:
        print(json.dumps(rec, ensure_ascii=False))
    if not pending:
        print("[pending] none", file=sys.stderr)


if __name__ == "__main__":
    main()
