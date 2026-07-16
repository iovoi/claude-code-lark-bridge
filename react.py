#!/usr/bin/env python3
"""
Manage the emoji reaction lifecycle on a Feishu message (thin wrapper over feishu_api).

Usage:
    .venv/bin/python react.py <message_id> WORKING   # stamp the "working" emoji
    .venv/bin/python react.py <message_id> DONE       # add done emoji, then delete the stored working one
"""

import sys

import feishu_api


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit("Usage: react.py <message_id> WORKING|DONE")
    message_id = sys.argv[1]
    action = sys.argv[2].strip().upper()

    if action == "WORKING":
        code = feishu_api.EMOJI_WORKING
    elif action == "DONE":
        code = feishu_api.EMOJI_DONE
    else:
        sys.exit(f"unknown action {action!r}; use WORKING or DONE")

    rid = feishu_api.add_reaction(message_id, code)
    if not rid:
        print(f"[react] FAILED to add {code}", file=sys.stderr)
        sys.exit(1)
    print(f"[react] added {code} to {message_id} (reaction_id={rid})")

    if action == "DONE":
        entry = feishu_api.read_json(feishu_api.REACTIONS_PATH).get(message_id, {})
        old = entry.get("reaction_id")
        if old:
            ok = feishu_api.delete_reaction(message_id, old)
            print(f"[react] removed working emoji {old} -> {ok}")


if __name__ == "__main__":
    main()
