# Kimi Code Mesh Orchestration Design

> **Status:** Approved for implementation  
> **Scope:** Add Kimi Code as a first-class repowire backend, enforce per-context-window 100k token limits, and enable cross-runtime review cycles between Claude Code, Kimi Code, and Gemini CLI.  
> **Approach:** Hybrid Runtime-Aware Orchestration

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [M1: Kimi Code Backend](#2-m1-kimi-code-backend)
3. [M2: Per-Context-Window Token Limits & Guardrails](#3-m2-per-context-window-token-limits--guardrails)
4. [M3: Cross-Runtime Review Cycles](#4-m3-cross-runtime-review-cycles)
5. [Testing Strategy](#5-testing-strategy)
6. [Risks & Mitigations](#6-risks--mitigations)

---

## 1. Architecture Overview

### 1.1 Design Principles

- **Daemon is the single hub for identity, routing, and mesh-level policy.** Every cross-runtime message flows through the daemon so it can log, gate, and account.
- **Each runtime manages its own internal subagent hierarchy.** Kimi's native `Agent` tool, Claude's subagents (if any), and Gemini's threads are opaque to the daemon in terms of their internal logic, but their **token usage is reported**.
- **Every entity with its own context window gets its own 100k token budget.** This includes mesh peers (top-level) and native subagents (reported by their parent runtime).
- **Deterministic guardrails are runtime-agnostic MCP tools.** `lint_code`, `run_tests`, `type_check`, and `static_analyze` are daemon-side tools that any peer can call.

### 1.2 System Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    Repowire Daemon (:8377)                   │
│  ┌──────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │ PeerRegistry │  │ AskTracker  │  │ TokenBudgetStore    │ │
│  │  (identity)  │  │ (lifecycle) │  │ (100k per context)  │ │
│  └──────────────┘  └─────────────┘  └─────────────────────┘ │
│  ┌──────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │ MessageRouter│  │GuardrailMCP │  │ SpawnService        │ │
│  │  (ws/acp)    │  │ (lint/test) │  │ (claude/kimi/gemini)│ │
│  └──────────────┘  └─────────────┘  └─────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
         ▲                    ▲                    ▲
         │ WebSocket          │ WebSocket          │ WebSocket
         │ (tmux inject)      │ (tmux inject)      │ (tmux inject)
    ┌────┴────┐          ┌────┴────┐          ┌────┴────┐
    │ Claude  │◄────────►│  Kimi   │◄────────►│ Gemini  │
    │  Code   │   ask/   │  Code   │   ask/   │   CLI   │
    │         │  notify  │         │  notify  │         │
    │ ┌─────┐ │          │ ┌─────┐ │          │ ┌─────┐ │
    │ │Sub- │ │          │ │Sub- │ │          │ │Sub- │ │
    │ │agent│ │          │ │agent│ │          │ │agent│ │
    │ │(1..N)│ │          │ │(1..N)│ │          │ │(1..N)│ │
    │ └─────┘ │          │ └─────┘ │          │ └─────┘ │
    │(reports │          │(reports │          │(reports │
    │ usage)  │          │ usage)  │          │ usage)  │
    └─────────┘          └─────────┘          └─────────┘
```

### 1.3 Runtime Capabilities Matrix

| Capability | Claude Code | Kimi Code | Gemini CLI |
|---|---|---|---|
| Mesh peer registration | ✅ Hooks | ✅ Hooks (new) | ✅ Hooks |
| Session resume | ✅ `--resume` | ✅ `-S <id>` / `-C` | ✅ `--resume` |
| Native subagents | ⚠️ Limited | ✅ `Agent` tool | ⚠️ Threads |
| Subagent token reporting | ❌ (not yet) | ✅ (new) | ❌ (not yet) |
| Auto-approve mode | ✅ `--dangerously-skip-permissions` | ✅ `--yolo --auto` | ✅ `--yolo` |
| Plan mode | ❌ | ✅ `--plan` | ❌ |

### 1.4 Milestones

| Milestone | Scope | Deliverable |
|---|---|---|
| **M1** | Kimi Code backend registration, spawn, resume, ask/notify | Kimi peers talk in the mesh |
| **M2** | Per-context-window token budget, deterministic flags, guardrail MCP tools | No context window exceeds 100k |
| **M3** | Cross-runtime review protocol, guardrail-gated merge | Claude reviews Kimi, Kimi reviews Claude, etc. |

---

## 2. M1: Kimi Code Backend

### 2.1 Backend Detection (`agent_backends.py`)

Add `KimiCodeBackend` following the existing singleton pattern:

```python
class KimiCodeBackend(AgentBackend):
    agent_type: ClassVar[AgentType] = AgentType.KIMI_CODE
    cli_names: ClassVar[tuple[str, ...]] = ("kimi",)
    config_markers: ClassVar[tuple[Path, ...]] = (Path.home() / ".kimi-code",)
    default_command: ClassVar[str | None] = "kimi -C --yolo --auto"
    supports_resume: ClassVar[bool] = True
    resume_flag: ClassVar[str | None] = "-S"
    resume_subcommand: ClassVar[str | None] = None
    post_spawn_strategy: ClassVar[str] = "kimi_warmup"
    mcp_config_scope: ClassVar[McpConfigScope | None] = McpConfigScope.USER
```

Update `detect_mcp_backend()` detection order:

1. `REPOWIRE_BACKEND=kimi-code` env var (explicit override)
2. `KIMI_CODE_SESSION_ID` env var (if Kimi sets one in future releases)
3. `~/.kimi-code/config.toml` exists AND pane metadata shows `kimi` process in subtree
4. Fallback to other backends

Add `AgentType.KIMI_CODE = "kimi-code"` to the `AgentType` enum.

### 2.2 Resume Safety (`resume_safety.py`)

Kimi stores sessions in `~/.kimi-code/session_index.jsonl` (JSONL format). Extend `resolve_resume_safety()` with a Kimi-specific resolver:

```python
def _resolve_kimi_resume(
    project_path: Path,
    session_bindings: SessionBindingStore,
) -> ResumeSafetyDecision:
    index_path = Path.home() / ".kimi-code" / "session_index.jsonl"
    if not index_path.exists():
        return ResumeSafetyDecision(warning="No Kimi session index found.")

    sessions = []
    with open(index_path) as f:
        for line in f:
            entry = json.loads(line)
            if Path(entry["workDir"]).resolve() == project_path.resolve():
                sessions.append(entry)

    if not sessions:
        return ResumeSafetyDecision(warning="No prior Kimi session for this project.")

    # Most recent session (last in file, or by session dir mtime)
    latest = max(sessions, key=lambda s: Path(s["sessionDir"]).stat().st_mtime)
    session_dir = Path(latest["sessionDir"])

    if not session_dir.exists():
        return ResumeSafetyDecision(warning=f"Kimi session dir missing: {session_dir}")

    return ResumeSafetyDecision(
        plan=ResumePlan(
            session_id=latest["sessionId"],
            resume_command=f"kimi -S {latest['sessionId']} --yolo --auto",
        )
    )
```

### 2.3 Hook Adapter (`hooks/adapters.py`)

Kimi does not emit a `claude_cli`-style hook JSON payload (there is no `hooks.json` equivalent in Kimi's core). Instead, we treat Kimi as a **hook-capable backend via its plugin system**.

**Short-term (M1):** Kimi uses the same tmux-based ws-hook as Claude, but without hook JSON adapters. The ws-hook is spawned by the daemon after peer registration, and it connects directly to `/ws`. Kimi's `sessionStart` plugin (`superpowers`) already runs; we extend it to register with the daemon.

**Hook payload normalization:**

```python
@dataclass
class KimiHookPayload:
    event: str                    # "session_start", "user_prompt_submit", "stop"
    session_id: str               # Kimi session UUID
    work_dir: str
    agent_id: str = "main"        # "main" or "agent-N"
    parent_agent_id: str | None = None
    turn_state: str = "idle"      # "idle", "working", "awaiting_input"

    def normalize(self) -> HookPayload:
        return HookPayload(
            event=self.event,
            session_id=self.session_id,
            work_dir=self.work_dir,
            agent_id=self.agent_id,
            turn_state=self.turn_state,
        )
```

**Key difference from Claude:** Kimi does not dump a full transcript to disk on every turn. The `stop_handler.py` MUST gracefully skip chat-turn extraction for Kimi peers if no transcript is available. This is a one-line change: add a `backend_supports_transcript` check before parsing.

### 2.4 Spawn & Warmup (`spawn.py`, `spawn_service.py`)

**Spawn command resolution:**

| Scenario | Command |
|---|---|
| Fresh spawn | `kimi -C --yolo --auto <project_path>` |
| Resume spawn | `kimi -S <session_id> --yolo --auto <project_path>` |
| Plan mode | `kimi -C --yolo --auto --plan <project_path>` |

The `-C` flag tells Kimi to continue the previous session for the working directory (auto-resolved from `session_index.jsonl`). The `-S <id>` flag resumes a specific session by UUID.

**Post-spawn warmup:**

Kimi's registration can be slightly delayed on first launch (similar to Codex). Add `_kimi_warmup()` to `agent_backends.py`:

```python
async def _kimi_warmup(
    pane_id: str,
    display_name: str,
    daemon_base_url: str,
) -> None:
    # Kimi needs a moment to initialize its TUI and session
    await asyncio.sleep(2.0)
    # Send a no-op notification to trigger SessionStart hook registration
    # Uses existing daemon HTTP client (same pattern as hooks/utils.py)
    await _daemon_post(
        daemon_base_url,
        path="/notify",
        json={"target": display_name, "message": "_repowire_warmup_"},
    )
```

### 2.5 WebSocket Hook Transport (`hooks/websocket_hook.py`)

Kimi uses the **same tmux-based WebSocket hook** as Claude Code. No new transport is needed.

**Injection mode caveat:** Kimi's TUI consumes pasted text differently from Claude's REPL. We add a backend-specific injection mode flag:

```python
class AgentBackend(ABC):
    ...
    injection_mode: ClassVar[str] = "bracketed_paste"  # default

class KimiCodeBackend(AgentBackend):
    ...
    injection_mode: ClassVar[str] = "bracketed_paste_slow"
```

In `websocket_hook.py`, `_tmux_send_keys()` checks `injection_mode`:
- `"bracketed_paste"` — existing behavior (bracketed paste + Enter)
- `"bracketed_paste_slow"` — same but with a 50ms delay between chunks to let Kimi's TUI process input

### 2.6 Config (`config/models.py`)

Add Kimi defaults to `DaemonConfig.spawn`:

```python
class SpawnConfig(BaseModel):
    commands: dict[str, str] = Field(default_factory=lambda: {
        "claude-code": "claude --dangerously-skip-permissions",
        "kimi-code": "kimi -C --yolo --auto",
        "gemini": "gemini --yolo",
        # ... other backends
    })
    profiles: dict[str, dict[str, SpawnProfile]] = Field(default_factory=lambda: {
        "kimi-code": {
            "default": SpawnProfile(),
            "plan_mode": SpawnProfile(extra_args=["--plan"]),
            "deterministic": SpawnProfile(
                extra_args=["--model", "kimi-code/kimi-for-coding"],
                deterministic=True,
            ),
        },
        # ... other backends
    })
```

### 2.7 Testing

- `tests/test_kimi_code_backend.py` — Backend detection with mocked `~/.kimi-code` and env vars
- `tests/test_kimi_code_resume.py` — Resume safety with mocked `session_index.jsonl`
- `tests/test_spawn_kimi.py` — Spawn command resolution (fresh vs resume)
- `tests/test_hooks_kimi.py` — Hook adapter normalization
- Manual integration: Spawn Kimi peer, send `ask`, verify `ack` round-trip

---

## 3. M2: Per-Context-Window Token Limits & Guardrails

### 3.1 Token Budget Store (`daemon/state/token_budget.py`)

New SQLite table:

```sql
CREATE TABLE token_budgets (
    budget_id TEXT PRIMARY KEY,       -- peer_id for mesh peers, "peer_id/agent-N" for subagents
    parent_peer_id TEXT,              -- NULL for mesh peers, peer_id for subagents
    agent_type TEXT NOT NULL,         -- "claude-code", "kimi-code", "kimi-subagent", "gemini"
    cumulative_input_tokens INTEGER DEFAULT 0,
    cumulative_output_tokens INTEGER DEFAULT 0,
    budget_ceiling INTEGER DEFAULT 100000,
    warning_sent INTEGER DEFAULT 0,   -- 0 = no warning yet, 1 = 80% warning sent
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Key invariant:** `cumulative_input_tokens + cumulative_output_tokens <= budget_ceiling` for every row. The daemon enforces this; runtimes are responsible for respecting daemon signals.

### 3.2 Usage Reporting

**For mesh peers:**

The existing hook pipeline records usage:

1. **`prompt_handler.py`** — On `UserPromptSubmit`, query daemon for budget status. Daemon returns:
   - `{"decision": "proceed", "remaining": 82345}` — turn allowed
   - `{"decision": "block", "reason": "budget_exhausted", "used": 100000}` — turn blocked

2. **`stop_handler.py`** — After turn completes, POST usage to daemon:
   ```json
   POST /peers/{peer_id}/usage
   {"input_tokens": 15000, "output_tokens": 8000}
   ```

   **New route:** `POST /peers/{peer_id}/usage` in `daemon/routes/peers.py` delegates to `TokenBudgetStore.record_usage(peer_id, input_tokens, output_tokens)`.

   **Per-backend usage sources:**
   - **Claude Code:** Parse usage from `session/history.py` or transcript metadata (if available in hook payload).
   - **Kimi Code:** Kimi does not currently expose per-turn token counts in its logs. Use character-count estimate × model-specific multiplier as a conservative proxy. Default multiplier: **3.5 chars/token** for Kimi's models (based on typical CJK/English mixed tokenization). Over-estimate by 10% for safety. This is a temporary measure until Kimi adds usage metadata.
   - **Gemini:** Similar to Claude; parse from transcript or estimate using **4.0 chars/token** for Gemini models.

**For Kimi native subagents:**

Kimi peer reports subagent lifecycle and usage via a new ws-hook message type:

```json
{
  "type": "subagent_report",
  "peer_id": "kimi-main",
  "subagent_id": "agent-0",
  "agent_type": "kimi-subagent",
  "input_tokens": 15000,
  "output_tokens": 8000,
  "event": "usage_update"
}
```

Events:
- `"created"` — Subagent spawned; daemon creates budget row
- `"usage_update"` — Periodic or per-turn usage report
- `"destroyed"` — Subagent exited; daemon closes budget row

The daemon handles these in `WebSocketTransport.on_message()`:

```python
elif message_type == "subagent_report":
    await token_budget_store.upsert_subagent(
        parent_peer_id=data["peer_id"],
        subagent_id=data["subagent_id"],
        agent_type=data["agent_type"],
        input_tokens=data.get("input_tokens", 0),
        output_tokens=data.get("output_tokens", 0),
        event=data["event"],
    )
```

### 3.3 Enforcement

**Hard limit check (every turn):**

```python
async def check_budget(peer_id: str, estimated_input: int) -> BudgetDecision:
    row = await token_budget_store.get(peer_id)
    if row is None:
        return BudgetDecision(proceed=True, remaining=100000)
    used = row.cumulative_input_tokens + row.cumulative_output_tokens
    if used + estimated_input > row.budget_ceiling:
        return BudgetDecision(
            proceed=False,
            reason="budget_exhausted",
            used=used,
            ceiling=row.budget_ceiling,
        )
    return BudgetDecision(proceed=True, remaining=row.budget_ceiling - used)
```

**Integration points:**
- `hooks/prompt_handler.py` — Check budget before marking `BUSY`
- `daemon/job_runner.py` — Check budget in `acquire_executor_for_work()`
- `daemon/routes/spawn.py` — Optional: Check circle-wide total budget before spawning

**Warning at 80%:**

```python
async def maybe_warn_budget(peer_id: str):
    row = await token_budget_store.get(peer_id)
    if row is None:
        return
    used = row.cumulative_input_tokens + row.cumulative_output_tokens
    if used >= 0.8 * row.budget_ceiling and not row.warning_sent:
        await message_router.send_notification(
            from_peer="daemon",
            to_peer_id=peer_id,
            text=json.dumps({
                "type": "budget_warning",
                "used": used,
                "budget": row.budget_ceiling,
                "recommendation": "compact_context_or_spawn_fresh",
            }),
        )
        await token_budget_store.mark_warning_sent(peer_id)
```

### 3.4 Deterministic Spawn Flags

Extend `SpawnProfile`:

```python
@dataclass
class SpawnProfile:
    extra_args: list[str] = field(default_factory=list)
    deterministic: bool = False
```

When `deterministic=True`, inject backend-specific flags:

| Backend | Deterministic Flags |
|---|---|
| Claude Code | `--dangerously-skip-permissions` (already default); no temperature control available |
| Kimi Code | `--model kimi-code/kimi-for-coding` + disable non-deterministic skills via config |
| Gemini | `--model gemini-2.5-pro` (deterministic by default for coding) |

**Note:** True LLM determinism is limited. The guardrails (lint, test, compile) are the real determinism layer.

### 3.5 Guardrail MCP Tools (`mcp/server.py`)

New tools available to ALL peers regardless of runtime:

#### `lint_code(files: list[str], linter: str = "ruff") -> dict`

Runs the specified linter on the given files. Returns structured JSON.

```json
{
  "passed": false,
  "violations": [
    {"file": "src/foo.py", "line": 42, "code": "E501", "message": "Line too long"}
  ],
  "stdout": "...",
  "stderr": "...",
  "exit_code": 1
}
```

Supported linters: `ruff`, `flake8`, `pylint`, `eslint`, `prettier`.

#### `run_tests(test_path: str, runner: str = "pytest") -> dict`

Runs tests and returns structured results.

```json
{
  "passed": false,
  "failed": [
    {"test": "test_foo::test_bar", "message": "AssertionError: expected 42"}
  ],
  "stdout": "...",
  "stderr": "...",
  "exit_code": 1
}
```

Supported runners: `pytest`, `unittest`, `jest`, `vitest`, `cargo test`.

#### `type_check(files: list[str], checker: str = "mypy") -> dict`

Runs type checker.

```json
{
  "passed": true,
  "errors": [],
  "stdout": "Success: no issues found",
  "exit_code": 0
}
```

Supported checkers: `mypy`, `pyright`, `tsc`, `rustc` (for type errors).

#### `static_analyze(files: list[str], analyzer: str = "bandit") -> dict`

Runs security/static analyzer.

```json
{
  "passed": true,
  "issues": [],
  "stdout": "...",
  "exit_code": 0
}
```

Supported analyzers: `bandit`, `semgrep`, `safety`.

**Implementation pattern:**

Each tool:
1. Validates the requested tool is installed (`shutil.which()`)
2. Runs `subprocess.run()` with a timeout (default 60s)
3. Parses stdout/stderr into structured JSON
4. Returns result; never uses LLM for interpretation

```python
async def lint_code(files: list[str], linter: str = "ruff") -> dict:
    binary = shutil.which(linter)
    if not binary:
        return {"passed": False, "error": f"{linter} not found in PATH"}

    result = subprocess.run(
        [binary, "check", "--output-format", "json"] + files,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=project_path,
    )

    violations = json.loads(result.stdout) if result.stdout else []
    return {
        "passed": result.returncode == 0,
        "violations": violations,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.returncode,
    }
```

### 3.6 Circle-Wide Total Budget (Optional)

Prevent runaway orchestrators from spawning unlimited peers:

```yaml
daemon:
  token_budget:
    per_peer_ceiling: 100000
    per_circle_ceiling: 500000   # sum of all peers in a circle
```

If a spawn would exceed the circle budget, `POST /spawn` returns `HTTP 429` with:

```json
{"detail": "Circle token budget exhausted. Kill existing peers or increase budget."}
```

### 3.7 Testing

- `tests/test_token_budget.py` — Budget CRUD, enforcement, warning at 80%
- `tests/test_token_budget_integration.py` — Full pipeline: prompt → budget check → stop → usage update
- `tests/test_guardrail_tools.py` — Lint, test, type-check with mocked subprocess
- `tests/test_subagent_budget.py` — Kimi subagent report lifecycle

---

## 4. M3: Cross-Runtime Review Cycles

### 4.1 Review Gate State Machine

New SQLite table:

```sql
CREATE TABLE review_gates (
    gate_id TEXT PRIMARY KEY,         -- UUID
    work_id TEXT,                     -- FK to tracked_work or custom job
    author_peer_id TEXT NOT NULL,     -- Who produced the artifact
    reviewer_peer_id TEXT NOT NULL,   -- Who must review
    artifact_type TEXT,               -- "diff", "file", "pr"
    artifact_ref TEXT,                -- File path, PR URL, or diff hash
    guardrail_results TEXT,           -- JSON of lint/test/type-check results
    state TEXT DEFAULT "pending",     -- "pending", "in_review", "approved", "rejected", "merged"
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 4.2 Review Protocol

**Step 1: Author submits for review**

Author peer calls `request_review` MCP tool:

```json
{
  "tool": "request_review",
  "arguments": {
    "reviewer_peer": "claude-reviewer",
    "artifact_type": "diff",
    "artifact_ref": "/tmp/feature-xyz.diff",
    "guardrail_results": {
      "lint": {"passed": true},
      "tests": {"passed": true},
      "type_check": {"passed": true}
    }
  }
}
```

Daemon:
1. Creates `review_gates` row with `state="pending"`
2. Sends `ask` to reviewer peer:
   ```
   [ask #<gate_id>] Review request from kimi-worker:
   Artifact: diff at /tmp/feature-xyz.diff
   Guardrails: lint=✓ tests=✓ type_check=✓
   Please review and ack(gate_id, "approved" | "rejected", feedback)
   ```

**Step 2: Reviewer evaluates**

Reviewer peer reads the diff, runs its own guardrails (optional), and calls:

```json
POST /ack
{
  "correlation_id": "<gate_id>",
  "message": "Looks good, but add docstring to foo()",
  "attachments": {"decision": "approved", "confidence": "high"}
}
```

Or:

```json
{
  "correlation_id": "<gate_id>",
  "message": "Tests fail on edge case N=0",
  "attachments": {"decision": "rejected", "required_fixes": ["fix_zero_division"]}
}
```

**Step 3: Daemon updates gate**

```python
if decision == "approved":
    await review_gate_store.update_state(gate_id, "approved")
    await notify_peer(author_peer_id, f"Review approved by {reviewer_peer_id}")
elif decision == "rejected":
    await review_gate_store.update_state(gate_id, "rejected")
    await notify_peer(author_peer_id, f"Review rejected by {reviewer_peer_id}: {feedback}")
```

**Step 4: Author acts on feedback**

If rejected, author fixes and calls `request_review` again (new gate). If approved, author calls `merge_artifact` or proceeds.

### 4.3 Guardrail-Gated Merge

No artifact is considered "done" until:

1. `lint_code` returns `passed: true`
2. `run_tests` returns `passed: true`
3. `type_check` returns `passed: true`
4. Review gate is `state="approved"`

The daemon exposes `merge_readiness(gate_id) -> dict`:

```json
{
  "ready": true,
  "checks": {
    "lint": {"passed": true},
    "tests": {"passed": true},
    "type_check": {"passed": true},
    "review": {"state": "approved", "reviewer": "claude-reviewer"}
  }
}
```

### 4.4 Runtime-Aware Review Prompts

The orchestrator persona (`~/.repowire/orchestrator/SOUL.md`) includes runtime-specific review guidance:

```markdown
## Review Guidelines by Runtime

### Kimi Code Output
- Check for over-reliance on Write tool (Kimi tends to rewrite entire files)
- Verify edge cases in arithmetic and boundary conditions
- Ensure type hints are present (Kimi sometimes omits them)

### Claude Code Output
- Check for verbose explanations in comments
- Verify no hallucinated imports or dependencies
- Ensure error handling is specific (not bare except)

### Gemini CLI Output
- Check for consistent naming conventions
- Verify async/await patterns are correct
- Ensure tests cover the changed behavior, not just existing tests
```

This is persona content, not daemon code. The daemon just routes the `ask`; the reviewer peer's system prompt (SOUL.md) contains the runtime-specific guidance.

### 4.5 Testing

- `tests/test_review_gates.py` — Gate lifecycle: pending → in_review → approved → rejected
- `tests/test_review_protocol.py` — Full flow: request_review → ask → ack → state update
- `tests/test_merge_readiness.py` — Guardrail gate logic

---

## 5. Testing Strategy

### 5.1 Unit Tests

Follow existing patterns:
- `httpx.AsyncClient` + `ASGITransport` for route tests
- `httpx-ws` + `ASGIWebSocketTransport` for WebSocket tests
- `make_daemon_app()` harness in `conftest.py`
- Fake services (`FakeSpawn`, `FakeDelivery`) for job runner tests

### 5.2 Integration Tests

**M1 Integration:**
1. Install repowire with changes: `uv tool install . --force-reinstall`
2. Start daemon: `repowire service start`
3. Spawn Kimi peer: `repowire spawn --backend kimi-code --path /tmp/test-project`
4. From a Claude peer, `ask` the Kimi peer
5. Verify Kimi receives the ask and can `ack`

**M2 Integration:**
1. Spawn Kimi peer
2. Send multiple large prompts to exhaust budget
3. Verify 101st prompt is blocked with `budget_exhausted`
4. Spawn Kimi subagent via Kimi's `Agent` tool
5. Verify daemon receives `subagent_report` and tracks subagent budget

**M3 Integration:**
1. Kimi peer produces a code change
2. Kimi peer calls `lint_code`, `run_tests`, `type_check`
3. Kimi peer calls `request_review` to a Claude reviewer
4. Claude reviewer `ack`s with approval
5. Kimi peer calls `merge_readiness` → returns `ready: true`

### 5.3 Manual Testing Checklist

- [ ] Kimi peer spawns in tmux and self-registers
- [ ] Kimi peer resumes previous session with `-S <id>`
- [ ] Claude peer can `ask` Kimi peer; Kimi can `ack`
- [ ] Kimi peer can `ask` Claude peer; Claude can `ack`
- [ ] Token budget blocks prompts at 100k
- [ ] Budget warning fires at 80k
- [ ] `lint_code` returns structured JSON for Python files
- [ ] `run_tests` returns structured JSON for pytest
- [ ] Review gate transitions correctly on `ack`
- [ ] `merge_readiness` returns `ready: false` until all checks pass

---

## 6. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Kimi TUI doesn't handle tmux paste injection cleanly | Medium | High | Test injection modes; add `"bracketed_paste_slow"` fallback; if TUI is incompatible, fall back to HTTP notify-only transport |
| Kimi doesn't expose per-turn token counts | High | Medium | Use character-count estimate as conservative proxy; document limitation; request upstream feature from Moonshot AI |
| Token budget enforcement adds latency to every prompt | Medium | Medium | Budget check is a single SQLite `SELECT` with an index on `budget_id`; should be <1ms |
| Guardrail tools fail on non-Python projects | Medium | Medium | Each tool validates tool availability before running; returns clear error if tool missing; extend tool list per project type over time |
| Review protocol adds friction to simple tasks | Low | Medium | Review is opt-in via `request_review` tool; fast-path tasks can skip it; orchestrator persona decides when review is needed |
| Circle-wide budget prevents legitimate spawning | Low | Medium | Configurable `per_circle_ceiling`; can be disabled per circle; admin override via HTTP API |
| Kimi session_index.jsonl format changes | Low | High | Wrap parser in try/except; log parse errors; fallback to fresh spawn if resume fails |

---

## Appendix A: Kimi Session File Format Reference

**`~/.kimi-code/session_index.jsonl`** (one JSON object per line):

```json
{"sessionId":"session_d2a3e8b4-54a5-4d07-b8b8-e49e03ec082b","sessionDir":"/Users/.../.kimi-code/sessions/wd_settl_c5a41363feee/session_d2a3e8b4-...","workDir":"/Users/.../settl"}
```

**`~/.kimi-code/sessions/<wd_dir>/<session_dir>/state.json`**:

```json
{
  "createdAt": "2026-06-01T19:36:10.209Z",
  "updatedAt": "2026-06-01T19:41:37.601Z",
  "title": "...",
  "isCustomTitle": false,
  "agents": {
    "main": {"homedir": "...", "type": "main", "parentAgentId": null},
    "agent-0": {"homedir": "...", "type": "sub", "parentAgentId": "main"}
  }
}
```

**`~/.kimi-code/config.toml`** (relevant fields):

```toml
[loop_control]
max_steps_per_turn = 1000
reserved_context_size = 50000
compaction_trigger_ratio = 0.85
```

## Appendix B: New MCP Tools Summary

| Milestone | Tool | Purpose |
|---|---|---|
| M2 | `lint_code(files, linter)` | Deterministic lint check |
| M2 | `run_tests(test_path, runner)` | Deterministic test run |
| M2 | `type_check(files, checker)` | Deterministic type check |
| M2 | `static_analyze(files, analyzer)` | Deterministic security/static analysis |
| M2 | `get_budget_status(peer_id)` | Query token usage for self or subagent |
| M3 | `request_review(reviewer, artifact, guardrails)` | Submit work for review |
| M3 | `merge_readiness(gate_id)` | Check if all gates pass |
| M3 | `list_pending_reviews()` | List reviews assigned to caller |
