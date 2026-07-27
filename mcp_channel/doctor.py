"""Feishu bridge doctor — validate config + (optionally) the Feishu websocket connect.

Prints PASS/FAIL/WARN per check with a hint for the first failure. Never prints the
secret. Returns process-style: 0 = ok, 1 = at least one FAIL.

Usage:
    python -m mcp_channel.doctor            # full (incl. ws probe)
    python -m mcp_channel.doctor --no-ws    # creds + allowlist only (fast; used at bring-up)
"""
from __future__ import annotations
import logging
import threading


def _line(mark: str, name: str, detail: str) -> None:
    print(f"[{mark}] {name}: {detail}")


def check_creds() -> tuple[bool, str]:
    """APP_ID (cli_…) + APP_SECRET non-empty. Never prints the secret."""
    aid = "" ; asec = ""
    try:
        import feishu_api as api
        aid = api.cred("FEISHU_APP_ID"); asec = api.cred("FEISHU_APP_SECRET")
    except Exception as e:
        return False, f"could not import feishu_api: {e}"
    ok = bool(aid and aid.startswith("cli_") and asec)
    hint = "" if ok else "set FEISHU_APP_ID (cli_…) and FEISHU_APP_SECRET via userConfig or .env"
    return ok, hint


def check_allowlist() -> tuple[bool | None, str]:
    """None = WARN (not a hard fail): no allowlist set -> open to everyone."""
    try:
        import feishu_api as api
        oid = api.cred("FEISHU_ALLOWED_OPEN_IDS"); cid = api.cred("FEISHU_ALLOWED_CHAT_IDS")
    except Exception as e:
        return None, f"could not check allowlist: {e}"
    if not (oid or cid):
        return None, "no allowlist set — bot is open to everyone who can reach the Feishu app"
    return True, "allowlist set"


def check_ws(timeout: float = 20.0) -> tuple[bool, str]:
    """Start the lark websocket, wait for 'connected to wss' within timeout, then stop.
    Lazy-imports lark (slow on some FS). The probe ws runs in a daemon thread and dies
    with this process — cleanup is automatic."""
    try:
        import feishu_api as api
        api.route_lark_logs_to_stderr()
        import lark_oapi as lark  # noqa: F401  (import triggers log handler setup)
        from mcp_channel import feishu_ingest
    except Exception as e:
        return False, f"could not import lark for ws probe: {e}"
    connected = threading.Event()

    class _Tripwire(logging.Handler):
        def emit(self, record):
            try:
                if "connected to wss" in self.format(record):
                    connected.set()
            except Exception:
                pass

    lk = logging.getLogger("Lark")
    trip = _Tripwire(); lk.addHandler(trip)
    try:
        feishu_ingest.start_ws(lambda evt: None)  # daemon thread; logs the connect
        ok = connected.wait(timeout)
    finally:
        try:
            lk.removeHandler(trip)
        except Exception:
            pass
    hint = "" if ok else (
        "websocket did not connect — ensure the app has scopes im:message + "
        "im:message:send_as_bot, Events → Long-Connection mode enabled, and "
        "im.message.receive_v1 subscribed."
    )
    return ok, hint


def run_doctor(include_ws: bool = True) -> int:
    """Run checks; print results; return 0 if all pass, 1 on any FAIL."""
    fails = 0

    ok, hint = check_creds()
    _line("PASS" if ok else "FAIL", "creds", hint or "APP_ID/SECRET present")
    fails += 0 if ok else 1

    ok, hint = check_allowlist()
    if ok is None:
        _line("WARN", "allowlist", hint)
    else:
        _line("PASS" if ok else "FAIL", "allowlist", hint)
        fails += 0 if ok else 1

    if include_ws:
        ok, hint = check_ws()
        _line("PASS" if ok else "FAIL", "websocket", hint or "connected to wss://msg-frontier.feishu.cn")
        fails += 0 if ok else 1

    print("\nRESULT:", "ok" if fails == 0 else f"{fails} check(s) failed")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    import sys
    include_ws = "--no-ws" not in sys.argv
    sys.exit(run_doctor(include_ws=include_ws))
