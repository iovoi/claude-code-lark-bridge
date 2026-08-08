#!/usr/bin/env python3
"""Fake Claude Code for tests.

Speaks just enough of the streaming-input stream-json + control protocol to drive
``ClaudeAdapter`` end-to-end without real API calls:

* first stdin line is a ``control_request {subtype:"initialize"}`` -> reply control_response.
* each ``{type:"user"}`` turn emits system/init + assistant text, optionally a
  ``can_use_tool`` control_request (awaiting the bridge's allow/deny control_response),
  then a ``result``.
* honors ``{subtype:"interrupt"}`` (from /stop) by emitting a result and exiting.

Mode via env: ``FAKE_CLAUDE_MODE`` = ``plain`` (default) | ``approval``.
Session id via env: ``FAKE_CLAUDE_SESSION`` (default ``fake-session-1``).

Stays alive between turns until stdin closes (EOF), mirroring real streaming-input mode.
"""
import json
import os
import sys


def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def read_line():
    line = sys.stdin.readline()
    if not line:
        return None
    line = line.strip()
    if not line:
        return {}
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return {}


def main():
    mode = os.environ.get("FAKE_CLAUDE_MODE", "plain")
    session = os.environ.get("FAKE_CLAUDE_SESSION", "fake-session-1")

    first = read_line()
    if isinstance(first, dict) and first.get("type") == "control_request":
        emit({"type": "control_response",
              "response": {"subtype": "success", "request_id": first.get("request_id"), "response": {}}})

    while True:
        msg = read_line()
        if msg is None:  # EOF
            return
        if not isinstance(msg, dict):
            continue
        mtype = msg.get("type")

        if mtype == "control_request":
            # Bridge-side control (e.g. interrupt). Ack it.
            emit({"type": "control_response",
                  "response": {"subtype": "success", "request_id": msg.get("request_id"), "response": {}}})
            if (msg.get("request") or {}).get("subtype") == "interrupt":
                emit({"type": "result", "session_id": session, "result": "interrupted",
                      "usage": {}, "total_cost_usd": 0.0})
                return
            continue

        if mtype == "user":
            prompt = (msg.get("message") or {}).get("content", "")
            emit({"type": "system", "subtype": "init", "data": {"session_id": session, "cwd": "/tmp"}})
            emit({"type": "assistant",
                  "message": {"content": [{"type": "text", "text": f"Working on: {prompt}"}]}})

            if mode == "approval":
                emit({"type": "control_request", "request_id": "cli_1",
                      "request": {"subtype": "can_use_tool", "tool_name": "Bash",
                                  "input": {"command": "rm -rf build"}}})
                resp = read_line()
                if resp is None:
                    return
                rbody = (resp.get("response") or {}).get("response") or {}
                ok = rbody.get("behavior") == "allow"
                emit({"type": "user", "message": {"content": [
                    {"type": "tool_result", "tool_use_id": "tu1",
                     "content": "ran" if ok else "denied", "is_error": not ok}]}})
                if rbody.get("interrupt"):
                    emit({"type": "result", "session_id": session, "result": "stopped",
                          "usage": {}, "total_cost_usd": 0.0})
                    return

            emit({"type": "result", "session_id": session, "result": f"Done: {prompt}",
                  "usage": {"input_tokens": 5, "output_tokens": 7}, "total_cost_usd": 0.001})
            # loop back: stay alive for the next turn until EOF


if __name__ == "__main__":
    main()
