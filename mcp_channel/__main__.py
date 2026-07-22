"""Entry point for the Feishu MCP channel.

`run()` is the console entry point (`feishu-channel`) and the `python -m mcp_channel`
target. It tees sys.stderr to a file (default /tmp/feishu-channel.log, override via
FEISHU_CHANNEL_LOG) so the channel's [boot]/[ws]/[access]/[push] logs are inspectable
even when launched as a child process whose stderr is captured by the host (which may
only surface the first line)."""
import os
import sys


class _Tee:
    """Write to the real stderr AND a log file, transparently."""
    def __init__(self, primary, extra):
        self._primary = primary
        self._extra = extra

    def write(self, s):
        try:
            self._primary.write(s); self._primary.flush()
        except Exception:
            pass
        try:
            self._extra.write(s); self._extra.flush()
        except Exception:
            pass

    def flush(self):
        for s in (self._primary, self._extra):
            try:
                s.flush()
            except Exception:
                pass

    def isatty(self):
        return self._primary.isatty()

    def __getattr__(self, name):
        return getattr(self._primary, name)


def run() -> None:
    """Console entry point + `python -m mcp_channel` target."""
    real = sys.stderr
    log_path = os.environ.get("FEISHU_CHANNEL_LOG", "/tmp/feishu-channel.log")
    try:
        sys.stderr = _Tee(real, open(log_path, "a", buffering=1))
    except Exception as e:  # pragma: no cover
        real.write(f"[tee] could not open {log_path}: {e}\n")
    import anyio
    from .server import main
    anyio.run(main)


if __name__ == "__main__":
    run()
