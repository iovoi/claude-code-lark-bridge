"""Offline unit tests for rich-text ('post') message parsing in feishu_ingest.

Feishu/Lark sends rich-text messages as message_type='post' with a structured
body {<locale>: {title, content: [[{tag,...}, ...]]}}. Previously only
message_type='text' was parsed, so post messages (including a lone emoji sent as
rich text) arrived empty and Claude asked the user to resend as plain text.

extract_text() is pure and dependency-free, so we test it directly.

Run:  python3 tests/test_ingest_post.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_channel import feishu_ingest as F


def main() -> None:
    # --- 1. plain text passes through ---
    assert F.extract_text("text", {"text": "hello world"}) == "hello world"
    assert F.extract_text("text", {}) == ""
    print("[1] text passthrough: OK")

    # --- 2. simple single-line post ---
    body = {"zh_cn": {"content": [[{"tag": "text", "text": "hi there"}]]}}
    assert F.extract_text("post", body) == "hi there", F.extract_text("post", body)
    print("[2] single-line post: OK")

    # --- 3. title + multi-row post, mixed node types ---
    body = {
        "zh_cn": {
            "title": "Meeting notes",
            "content": [
                [{"tag": "text", "text": "Action: "}, {"tag": "a", "text": "fix bug", "href": "http://x"}],
                [{"tag": "at", "user_id": "ou_1", "name": "@alice"}],
            ],
        }
    }
    out = F.extract_text("post", body)
    assert out == "Meeting notes\nAction: fix bug\n@alice", repr(out)
    print("[3] title + multi-row + a/at nodes: OK")

    # --- 4. emoji as a text node comes through (the reported bug) ---
    body = {"zh_cn": {"content": [[{"tag": "text", "text": "👍"}]]}}
    assert F.extract_text("post", body) == "👍"
    print("[4] emoji text node: OK")

    # --- 5. image-only post -> empty (caller falls back to placeholder) ---
    body = {"zh_cn": {"content": [[{"tag": "img", "image_key": "k"}]]}}
    assert F.extract_text("post", body) == ""
    print("[5] image-only post -> empty: OK")

    # --- 6. mixed: text + image in one row keeps the text, drops the image ---
    body = {"zh_cn": {"content": [[{"tag": "text", "text": "look "},
                                   {"tag": "img", "image_key": "k"}]]}}
    assert F.extract_text("post", body) == "look"
    print("[6] text+image row keeps text: OK")

    # --- 7. locale fallback: en_us when no zh_cn ---
    body = {"en_us": {"content": [[{"tag": "text", "text": "hello"}]]}}
    assert F.extract_text("post", body) == "hello"
    print("[7] en_us locale fallback: OK")

    # --- 8. missing-locale-wrapper tolerance (some clients) ---
    body = {"title": "T", "content": [[{"tag": "text", "text": "x"}]]}
    assert F.extract_text("post", body) == "T\nx", repr(F.extract_text("post", body))
    print("[8] missing locale wrapper tolerated: OK")

    # --- 9. unknown message_type -> empty (placeholder path) ---
    assert F.extract_text("image", {"image_key": "k"}) == ""
    assert F.extract_text("interactive", {}) == ""
    assert F.extract_text("", {}) == ""
    print("[9] unknown types -> empty: OK")

    # --- 10. malformed content never raises ---
    assert F.extract_text("post", {}) == ""
    assert F.extract_text("post", {"zh_cn": "not a dict"}) == ""
    assert F.extract_text("post", "garbage") == ""  # type: ignore[arg-type]
    assert F.extract_text("post", {"zh_cn": {"content": "nope"}}) == ""
    print("[10] malformed input is safe: OK")

    print("\nINGEST POST OK")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("INGEST POST FAILED (assertion):", e, file=sys.stderr); sys.exit(1)
    except Exception as e:
        import traceback; traceback.print_exc()
        print("INGEST POST FAILED:", repr(e), file=sys.stderr); sys.exit(1)
