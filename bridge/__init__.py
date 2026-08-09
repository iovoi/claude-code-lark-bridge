"""bridge — unified streaming print-mode Feishu/Lark <-> Claude Code bridge.

Drives Claude Code in non-interactive streaming mode
(`claude -p --input-format stream-json --output-format stream-json`), kept alive as
one process per scope, with a hand-rolled bidirectional control protocol for Lark
tool-approval cards and graceful /stop. All Lark interaction (reactions, streaming
cards, approval cards) is owned here. See docs/pipe-bridge/prd.md.
"""

__version__ = "0.1.0"
