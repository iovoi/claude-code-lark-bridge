# PRD: Codex agent support (pluggable coding-agent backend)

- **Status:** Complete (live codex smoke test pending — see §8 / log)
- **Feature dir:** `docs/codex-agent-support/`
- **Created:** 2026-09-06 · **Last updated:** 2026-09-06 (§8 parity matrix added)
- **Branch:** `feat/codex-agent-support` · **PR:** #19

## 0. Resume protocol
If you are a new agent: read this `prd.md`, then `tasks.md`, then `log.md`, then
resume from the first unchecked task in `tasks.md`. This document is the source
of truth — do not re-derive the design.

> **Backfill note:** this feature was implemented *before* these docs existed
> (the formal-feature skill was applied retroactively at the user's request).
> The docs below describe the software as actually built and verified; `log.md`
> records the real implementation-time events, in order.

## 1. Overview
The bridge previously hardcoded Claude Code as its coding agent. This feature
makes the backend pluggable — `claude` (default, unchanged behavior) or `codex`
(the OpenAI Codex CLI) — selectable at install time (installer prompt), in
`.env` (`FEISHU_AGENT`), and per-invocation (`feishu-bridge up|run --agent …`).

## 2. Goals and non-goals
- **Goals:**
  - Select the coding agent via installer prompt, `.env`, or CLI flag, with
    that precedence for the CLI flag (env var override).
  - Drive codex through its non-interactive `codex exec --json` mode, mapping
    its JSONL events onto the existing `AgentEvent` stream — no changes to the
    runtime, cards, watchdog, or session store.
  - Session continuity across turns (resume by thread id) for codex, mirroring
    claude's `--resume` behavior.
  - Cross-platform process handling identical to the claude path (POSIX
    `start_new_session`+`killpg`, Windows `CREATE_NEW_PROCESS_GROUP |
    CREATE_NO_WINDOW`, `.cmd`/`.bat` shim routing, `_taskkill` tree kill).
- **Non-goals:**
  - Approval cards for codex — `codex exec` has no interactive approval
    round-trip; the sandbox policy is the safety boundary. Cards remain
    claude-only (possible follow-up if codex grows a control protocol).
    See §8 for the full claude-vs-codex parity matrix.
  - Any UI/config surface beyond the four env keys, the CLI flag, and the
    installer prompt.
  - Running multiple agents simultaneously or per-chat agent switching.

## 3. Acceptance criteria
1. `BridgeConfig.load()` reads `FEISHU_AGENT` (`claude`|`codex`), falling back
   to `claude` for missing/unknown values. ✅ test `test_make_adapter_dispatch`
2. `make_adapter(cfg, …)` returns a `CodexAdapter` iff `cfg.agent == "codex"`,
   else `ClaudeAdapter`. ✅ same test
3. The codex argv for a fresh turn is `codex exec --json --skip-git-repo-check
   -c sandbox_mode="<sandbox>" [<extra args>] -` (prompt on stdin); for a
   resumed turn `codex exec resume <session-id> …same flags… -`. The sandbox
   is a `-c` config override, never `-s` — `exec resume` rejects `-s`
   (rc=2, every resumed turn failed). ✅ `test_codex_argv_fresh_turn`,
   `test_codex_argv_resume_and_extra_args`
4. Live codex JSONL events map as: `thread.started`→`SystemEvent` (+capture
   thread id), `item.completed item.type=agent_message`→`TextEvent`,
   `reasoning`→`ThinkingEvent`, tool items (`command_execution`, `file_change`,
   `mcp_tool_call`, `web_search`, `todo_list`)→`ToolUseEvent`+`ToolResultEvent`,
   `turn.completed`→`UsageEvent`+`DoneEvent(normal)`, `error`/`turn.failed`→
   `ErrorEvent`+`DoneEvent(error)`. ✅ `test_map_*`, confirmed against real
   codex-cli 0.147 output (see log)
5. `feishu-bridge up --agent codex` sets `FEISHU_AGENT=codex` in the
   environment of both the CLI process and the supervisor's detached child. ✅
   `test_cli_agent_flag_sets_env`
6. A `run_turn` over a fake subprocess maps the stream, returns
   `{"session_id": <thread id>}`, and the next turn resumes that id. ✅
   `test_run_turn_maps_stream_and_resumes`
7. Installer: interactive preflight prompts `Agent [claude/codex]` (default:
   existing `.env` value, else `claude`); `--agent <x>` CLI arg and
   `FEISHU_AGENT` env bypass the prompt; unknown values exit 1 with
   `unknown agent '<x>' (expected: claude or codex)`; preflight dies if the
   chosen CLI is not on PATH; the choice is persisted to `.env` as
   `FEISHU_AGENT=<x>` (also under `--no-creds`); the DONE banner prints
   `Agent: <x>`. ✅ manual verification in-session (installer never prompts in
   CI; no automated installer tests exist — see log)
8. Full test suite passes: `47 passed`. ✅

## 4. Detailed specification

### 4.1 Inputs
- `.env` / environment keys (resolved via `feishu_api.cred`, see `bridge/config.py`):
  - `FEISHU_AGENT` — `claude` (default) | `codex`. Unknown/empty → `claude`.
  - `FEISHU_CODEX_BIN` — path to the codex CLI. Default: `shutil.which("codex")` or `"codex"`.
  - `FEISHU_CODEX_ARGS` — extra CLI flags, whitespace-split (e.g.
    `-c model=gpt-5.2 --enable feature`). Default: none.
  - `FEISHU_CODEX_SANDBOX` — `read-only` | `workspace-write` (default) |
    `danger-full-access`; passed as the config override
    `-c sandbox_mode="<value>"` (not `-s`: `exec resume` rejects `-s`).
  - `FEISHU_RESUME_SESSIONS` — `1`/`true` to resume the session/thread stored
    in `sessions.json` at bridge startup (cross-restart context continuity).
    **Default OFF**: every bridge start opens a fresh thread per chat for
    BOTH backends (identical behavior); messages within one running bridge
    still share context (claude: one long-lived process; codex: the adapter
    chains turns via `exec resume` of its own in-memory thread id).
- CLI: `feishu-bridge up --agent claude|codex`, `feishu-bridge run --agent claude|codex`
  (argparse `choices=("claude","codex")`). The flag sets `os.environ["FEISHU_AGENT"]`
  before dispatch, so both the foreground runtime and the supervisor's
  `python -m bridge run` child inherit it.
- Installer: `--agent <x>` positional-style flag, or `FEISHU_AGENT` env, or the
  interactive prompt.

### 4.2 Outputs
- Normalized `AgentEvent`s on the same `Emit` callback contract as claude
  (`bridge/agent/__init__.py`). Turn result dict from `run_turn`:
  `{"session_id": str|None, "exit_code": int}` (claude additionally returns
  cost/token fields; codex's usage rides on `UsageEvent`s instead).
- `.env` gains a `FEISHU_AGENT=<claude|codex>` line after install.

### 4.3 Behavior
- One `codex exec` subprocess **per turn** (claude, by contrast, uses one
  long-lived subprocess speaking the bidirectional control protocol):
  1. Build argv (see 4.5 `_build_codex_argv`).
  2. Spawn with `stdin/stdout/stderr = PIPE`, `cwd = cfg.workdir`, platform
     flags as in §4.3 of the claude transport.
  3. Write `prompt + "\n"` to stdin, close stdin (codex starts the turn on EOF).
  4. Read stdout line-by-line; JSON-parse lines starting with `{`; call
     `on_frame()` (watchdog heartbeat) per event; map and `emit()` each
     AgentEvent; stop on `turn.completed`/`turn.failed`/`error`/EOF.
  5. On EOF without a terminal event, emit `DoneEvent(reason="eof")`.
  6. First turn: capture thread id from `thread.started`; subsequent turns of
     the same scope pass it as `resume` (adapter stores it in `self._resume`).
- stderr is always drained into a bounded 8 KB tail (+ the scope's sink,
  `~/.chat_bridge/agent-stderr.log`); if the process exits non-zero or emits
  no events, an `ErrorEvent("codex exited (rc=N): <stderr tail>")` +
  `DoneEvent(reason="error")` are emitted — never a silent empty turn.
- Sessions are stored per backend: `session_store.get/set_session_id(…,
  agent=…)` only resume an id created by the *same* agent (legacy entries
  without an `agent` field are claude's). A claude UUID is never passed to
  codex (which would fail `thread/resume: no rollout found`).
- `interrupt()`/`stop()`: no control protocol — terminate the in-flight
  process tree (Windows `_taskkill(pid, force=True)`; POSIX
  `os.killpg(os.getpgid(pid), SIGKILL)` with single-proc fallback).
- `start()` is a no-op (nothing persistent to launch).

### 4.4 Data model
Codex JSONL events consumed (verified against codex-cli 0.147; `item.*`
payloads follow the Responses-API item schema):

```json
{"type":"thread.started","thread_id":"01a076e5-236d-7560-a47c-9c5aba306e09"}
{"type":"turn.started"}
{"type":"item.started","item":{"id":"i1","type":"command_execution","command":"ls"}}
{"type":"item.completed","item":{"id":"i1","type":"command_execution","command":"ls",
 "aggregated_output":"a\nb","exit_code":0,"status":"completed"}}
{"type":"item.completed","item":{"type":"agent_message","text":"hello"}}
{"type":"item.completed","item":{"type":"reasoning","summary":[{"type":"summary_text","text":"…"}]}}
{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":5}}
{"type":"error","message":"…"}
{"type":"turn.failed","error":{"message":"…"}}
```

Item-type → tool-name map: `command_execution`→`shell`, `file_change`→
`file_change`, `mcp_tool_call`→`mcp_tool_call`, `web_search`→`web_search`,
`todo_list`→`todo_list`. Unknown item types are ignored (forward-compat).
Tool result `is_error` = `status ∉ {None, "completed", "success"}` or
`exit_code` is a nonzero int.

### 4.5 Interfaces
- `bridge/agent/__init__.py`:
  `make_adapter(cfg, *, resume: str|None = None, approval_callback: ApprovalCallback|None = None, stderr_sink=None) -> AgentAdapter`
  — dispatches on `cfg.agent == "codex"` (lazy import) else claude.
- `bridge/session_store.py`:
  - `get_session_id(scope: str, agent: str = "claude") -> str|None`
  - `set_session_id(scope: str, session_id: str, cwd: str, agent: str = "claude") -> None`
- `bridge/agent/codex_adapter.py`:
  - `_build_codex_argv(codex_bin: str, *, sandbox: str, extra_args: list[str], resume: str|None) -> list[str]`
  - `_map_event(evt: dict, session_id_ref: dict) -> tuple[list, bool]` (events, turn_done?)
  - `_map_item(item: dict, *, started: bool) -> list`
  - `class CodexAdapter(cfg, *, resume=None, approval_callback=None, stderr_sink=None)`
    with `session_id` property + `start/run_turn/interrupt/stop` per the
    `AgentAdapter` protocol.
- `bridge/config.py` `BridgeConfig` new fields: `agent: str`, `codex_bin: str`,
  `codex_extra_args: list[str]`, `codex_sandbox: str`.
- `bridge/scope.py`: `_default_adapter_factory` now calls `make_adapter(...)`
  (was a direct `ClaudeAdapter(...)` construction).
- `bridge/__main__.py`: `--agent` on the `up` and `run` subparsers.
- `install.py`: `_AGENT` module global (default `"claude"`),
  `_select_agent() -> str`, `_validate_agent(agent) -> str`,
  `_persist_agent_only()`, and a `FEISHU_AGENT=…` line written by
  `collect_credentials()` (managed key, so never duplicated in extras).

### 4.6 Error handling & edge cases
- Unknown `FEISHU_AGENT` value in `.env` → silently `claude` (config-side
  fallback; installer-side `--agent bogus` → exit 1).
- Chosen CLI missing from PATH → installer `_die` (claude:
  `Claude Code (\`claude\`) not found on PATH…`; codex: `codex CLI not found on
  PATH. Install it (https://github.com/openai/codex) and re-run, or choose
  claude instead.`).
- Codex auth revoked/expired → codex itself emits `error` + `turn.failed`
  events; surfaced to chat as `ErrorEvent` → error phase (observed live).
- Non-JSON stdout lines are skipped; JSON-decode failures skipped.
- Prompt is never an argv element (stdin `-`), so prompt text cannot be
  parsed as flags.

### 4.7 Security & permissions
- Codex runs sandboxed (`-s`, default `workspace-write`) — the sandbox is the
  safety boundary because exec mode has no approval round-trip.
  `FEISHU_CODEX_SANDBOX=danger-full-access` is the user's explicit opt-out.
- Codex auth lives in `$CODEX_HOME` (`~/.codex`) of the user running the
  bridge; the bridge never stores codex credentials.

## 5. Architecture and file layout
- `bridge/agent/codex_adapter.py` (new) — CodexAdapter + argv/event mapping.
- `bridge/agent/__init__.py` (edit) — `make_adapter` factory.
- `bridge/config.py` (edit) — 4 new config fields.
- `bridge/scope.py` (edit) — factory call; docstring.
- `bridge/__main__.py` (edit) — `--agent` flag.
- `install.py` (edit) — agent prompt/validation/persistence.
- `skills/feishu-bridge/SKILL.md` (edit) — document `--agent` + codex notes
  (mirrored to `/mnt/c/Users/wade/Desktop/workspace/skills/skills/feishu-bridge/SKILL.md`).
- `tests/test_codex_agent.py` (new) — 8 tests.
- `docs/codex-agent-support/` — this documentation.

## 6. Dependencies
- codex CLI ≥ 0.147 (`--json`, `exec resume`, `-s` sandbox flag). `codex login`
  required on the machine running the bridge (WSL's `CODEX_HOME` is separate
  from the Windows desktop app's — see log).
- No new Python dependencies. Test suite: `.venv/bin/python -m pytest tests/ -q`
  from the repo root (long-running commands go through the tmux pane per
  workspace CLAUDE.md).

## 7. Testing strategy
- Unit: `.venv/bin/python -m pytest tests/test_codex_agent.py -q`
  (argv, mapping, factory, CLI flag incl. env propagation via monkeypatched
  `bridge.supervisor.handle`, fake-subprocess `run_turn` with resume).
- Full suite: `.venv/bin/python -m pytest tests/ -q` → 47 passed.
- Live smoke (still pending, needs `codex login` in WSL):
  `printf 'Reply with the single word: pong\n' | codex exec --json -s read-only --skip-git-repo-check -`
  then `feishu-bridge up --agent codex` and a chat message.
- Installer manual check: `python3 install.py --agent bogus` exits 1 with the
  exact error; `--agent codex` passes preflight when codex is on PATH.

## 8. Backend parity: claude vs codex

The chat-side UX and runtime orchestration are **shared and identical** for both
backends (one runtime; only the adapter differs). The user-facing feature set,
however, is **not** a pure drop-in — three real differences exist:

**Identical (shared runtime, zero behavioral difference):** message ingestion
(Feishu websocket, allowlist, stale filtering); single-flight turns (busy →
"send /stop" hint); OnIt→Done emoji cycle; deferred 10s "Working…" progress
card with periodic refresh (tool list/status on the card); card-button actions
(stop); reply-based approval parsing (approve/all/deny/stop text replies —
note: only meaningful when an approval flow exists, i.e. claude); session
persistence + resume across restarts (claude: session-id, codex: thread-id;
per-scope, agent-gated so backends never resume each other's sessions); stuck
watchdog (`FEISHU_STUCK_TIMEOUT`); `/stop` interruption; final answer delivered
as a bot text message.

| Capability | claude backend | codex backend |
|---|---|---|
| Tool approval cards (risky tools pause and ask in chat) | ✅ full `can_use_tool` round-trip: Approve / Approve-all(turn) / Deny / Deny+stop cards, `FEISHU_AUTO_APPROVE_TOOLS` allowlist, `FEISHU_APPROVAL_TIMEOUT` auto-deny | ❌ `codex exec` has no interactive approval channel — the **sandbox** (`FEISHU_CODEX_SANDBOX`, default `workspace-write`) is the safety boundary: out-of-policy actions fail inside the sandbox instead of prompting the user |
| Cost display on the done card | ✅ 💰 $x.xxxx (claude reports `total_cost_usd`) | ❌ codex reports token usage only (`turn.completed.usage`), no cost figure |
| Turn interruption mechanism | graceful (control-protocol `interrupt`) | process-tree kill (same UX, less graceful) |
| Permission semantics | `FEISHU_DEFAULT_PERMISSION_MODE` / `--dangerously-skip-permissions` | sandbox tier: `read-only` / `workspace-write` (≈ writable workdir, no network) / `danger-full-access` |

**Possible follow-up** (not committed): approximate approval cards for codex by
defaulting to `read-only` sandbox and offering an "escalate this turn to
workspace-write and retry" card when a command fails the sandbox policy.

## 9. Open questions
- None blocking. One pending verification: live codex turn over the bridge
  (blocked on `codex login` in WSL — see log). If `item.*` field names differ
  in practice (e.g. `output` vs `aggregated_output`), the mapper already
  checks both; adjust `_map_item` and update §4.4.

## Appendix A — Decision log

| # | Decision | Options considered | Chosen | Rationale | Date |
|---|----------|--------------------|--------|-----------|------|
| D1 | Codex process model | (a) persistent subprocess w/ control protocol (b) one-shot `exec` per turn | (b) | codex has no documented bidirectional stdio control protocol; `exec` is its supported non-interactive mode; resume-by-id gives continuity | 2026-09-06 |
| D2 | Approvals for codex | (a) approval cards via `--ask-for-approval` (b) sandbox as boundary, no cards | (b) | exec mode can't round-trip approvals over stdio; claude's card flow (scope.py `_approval_cb`) stays claude-only | 2026-09-06 |
| D3 | Default sandbox | (a) read-only (b) workspace-write (c) danger-full-access | (b) | agent must edit files (bridge's purpose) without unrestricted host access; overridable via `FEISHU_CODEX_SANDBOX` | 2026-09-06 |
| D4 | Selection precedence | (a) write agent into .env from CLI flag (b) env-var override set by the CLI | (b) | `up` spawns a detached child; exporting `FEISHU_AGENT` is inherited without mutating the user's `.env` behind their back | 2026-09-06 |
| D5 | Resume argv placement | (a) flags before `resume` (b) flags after `resume <id>` | (b) | `exec resume` is a clap subcommand defining its own flags; parent flags don't inherit | 2026-09-06 |
| D6 | Unknown `.env` agent value | (a) hard fail (b) fall back to claude | (b) | bridge must start even with a typo'd value; installer-side flag *does* hard-fail (that's interactive) | 2026-09-06 |
| D7 | Session resume at bridge startup | (a) always resume stored sessions (b) fresh threads by default, `FEISHU_RESUME_SESSIONS=1` opts in | (b) | resuming stale cross-restart sessions caused both live bugs (cross-agent resume, `-s` on resume); fresh start is predictable; user decision 2026-09-13, identical for both backends | 2026-09-13 |

## Appendix B — Glossary
- **AgentEvent** — normalized event stream (`bridge/agent/__init__.py`) the
  runtime/cards consume, regardless of backend.
- **thread id** — codex's session identifier (`thread.started.thread_id`),
  the codex analogue of claude's `session_id`; both feed `session_store`.
- **Responses-API item** — codex's per-action payload schema
  (`command_execution`, `agent_message`, `reasoning`, …).
- **exec mode** — codex's non-interactive `codex exec` mode (vs the
  interactive TUI); `--json` emits JSONL events.
