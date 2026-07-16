#!/usr/bin/env python3
"""
Record that the LLM bridge has replied to a message, so it isn't handled again.

Usage:
    .venv/bin/python mark_handled.py <message_id> ["<reply text that was sent>"]
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
CONVERSATION_DIR = Path(
    os.environ.get("FEISHU_CONVERSATION_DIR", str(PROJECT_DIR / "conversation"))
)
HANDLED_PATH = CONVERSATION_DIR / ".handled.json"


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("Usage: mark_handled.py <message_id> [\"<reply sent>\"]")

    message_id = sys.argv[1]
    reply = sys.argv[2] if len(sys.argv) > 2 else None

    handled = {}
    if HANDLED_PATH.is_file():
        try:
            handled = json.loads(HANDLED_PATH.read_text(encoding="utf-8"))
        except Exception:
            handled = {}

    handled[message_id] = {
        "reply": reply,
        "replied_at": datetime.now(timezone.utc).isoformat(),
    }

    CONVERSATION_DIR.mkdir(parents=True, exist_ok=True)
    HANDLED_PATH.write_text(
        json.dumps(handled, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[handled] {message_id}")


if __name__ == "__main__":
    main()
