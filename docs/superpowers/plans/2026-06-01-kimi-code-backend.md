# Kimi Code Backend — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Kimi Code as a first-class repowire backend so Kimi peers can spawn, resume, ask, notify, and kill in the mesh alongside Claude Code, Gemini, and Codex.

**Architecture:** Extend the existing `AgentBackend` singleton registry with a `KimiCodeBackend` that mirrors the pattern of `GeminiBackend` and `ClaudeCodeBackend`. Kimi uses tmux-based WebSocket hooks (same transport as Claude), resume via `kimi -S <session-id>` or `kimi -C`, and session discovery via `~/.kimi-code/session_index.jsonl`.

**Tech Stack:** Python 3.12, FastAPI, pydantic, pytest, tmux/libtmux, SQLite (state already exists)

---

## File Structure

| File | Responsibility |
|---|---|
| `repowire/agent_types.py` | `AgentType` enum — add `KIMI_CODE` |
| `repowire/agent_backends.py` | `KimiCodeBackend` class + registration in `AGENT_BACKENDS` + detection in `detect_mcp_backend()` |
| `repowire/session/history.py` | `_kimi_resumable()` validator + registration in `_RESUME_VALIDATORS` |
| `repowire/hooks/adapters.py` | Add `"kimi"` to `hook_output()` explicit-allow list (Kimi shares Gemini's hook event names via plugin system) |
| `repowire/protocol/peers.py` | Update `backend` field description to include `kimi-code` |
| `tests/test_kimi_code_backend.py` | Backend detection tests |
| `tests/test_kimi_code_resume.py` | Resume safety tests |

---

## Task 1: Add `KIMI_CODE` to `AgentType` enum

**Files:**
- Modify: `repowire/agent_types.py`
- Test: `tests/test_kimi_code_backend.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_kimi_code_backend.py
from repowire.agent_types import AgentType


def test_kimi_code_agent_type_exists():
    assert hasattr(AgentType, "KIMI_CODE")
    assert AgentType.KIMI_CODE.value == "kimi-code"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_kimi_code_backend.py::test_kimi_code_agent_type_exists -v`

Expected: FAIL with `AttributeError: KIMI_CODE`

- [ ] **Step 3: Add `KIMI_CODE` to enum**

```python
# repowire/agent_types.py
class AgentType(str, Enum):
    """Type of AI coding agent a peer is running."""

    CLAUDE_CODE = "claude-code"
    OPENCODE = "opencode"
    CODEX = "codex"
    GEMINI = "gemini"
    ANTIGRAVITY = "antigravity"
    PI = "pi"
    MCP_HTTP = "mcp-http"
    KIMI_CODE = "kimi-code"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_kimi_code_backend.py::test_kimi_code_agent_type_exists -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add repowire/agent_types.py tests/test_kimi_code_backend.py
git commit -m "feat: add KIMI_CODE to AgentType enum"
```

---

## Task 2: Implement `KimiCodeBackend`

**Files:**
- Modify: `repowire/agent_backends.py`
- Test: `tests/test_kimi_code_backend.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_kimi_code_backend.py (append)
import os
from unittest.mock import patch

from repowire.agent_backends import (
    AGENT_BACKENDS,
    KimiCodeBackend,
    detect_mcp_backend,
)
from repowire.agent_types import AgentType


def test_kimi_code_backend_registered():
    backend = AGENT_BACKENDS.get(AgentType.KIMI_CODE)
    assert backend is not None
    assert isinstance(backend, KimiCodeBackend)
    assert backend.agent_type == AgentType.KIMI_CODE
    assert backend.display_name == "Kimi Code"
    assert backend.cli_names == ("kimi",)
    assert backend.supports_resume is True
    assert backend.resume_flag == "-S"
    assert backend.default_command == "kimi -C --yolo --auto"


def test_detect_mcp_backend_explicit_env():
    env = {"REPOWIRE_BACKEND": "kimi-code"}
    assert detect_mcp_backend(env) == AgentType.KIMI_CODE


def test_detect_mcp_backend_kimi_marker():
    """Kimi is detected when ~/.kimi-code exists and kimi binary is in PATH."""
    with patch.dict(os.environ, {"PATH": "/Users/test/.kimi-code/bin:/usr/bin"}, clear=True):
        with patch("shutil.which", return_value="/Users/test/.kimi-code/bin/kimi"):
            with patch.object(
                KimiCodeBackend, "mcp_runtime_matches", return_value=True
            ):
                result = detect_mcp_backend(os.environ)
                assert result == AgentType.KIMI_CODE


def test_build_resume_command_kimi():
    backend = KimiCodeBackend()
    command = backend.build_resume_command("kimi -C --yolo --auto", "session_abc123")
    assert command == "kimi -C --yolo --auto -S session_abc123"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_kimi_code_backend.py -v`

Expected: FAIL with `ImportError: cannot import name 'KimiCodeBackend'` and `NameError: name 'KimiCodeBackend' is not defined`

- [ ] **Step 3: Implement `KimiCodeBackend` and register it**

Insert `KimiCodeBackend` class after `GeminiBackend` in `repowire/agent_backends.py`:

```python
class KimiCodeBackend(AgentBackend):
    agent_type = AgentType.KIMI_CODE
    display_name = "Kimi Code"
    cli_names = ("kimi",)
    config_markers = (Path.home() / ".kimi-code",)
    default_command = "kimi -C --yolo --auto"
    supports_resume = True
    resume_strategy = "kimi_resume"
    resume_flag = "-S"
    post_spawn_strategy = "seed_message"
    mcp_config_scope = McpConfigScope(
        owner="backend",
        effective_scope="backend_global",
        label="Kimi Code global backend config",
        description=(
            "Kimi Code MCP edits target the user-level Kimi Code config shared by "
            "Kimi Code sessions on this host."
        ),
    )

    @classmethod
    def mcp_runtime_matches(cls, env: Mapping[str, str]) -> bool:
        if super().mcp_runtime_matches(env):
            return True
        # Kimi Code does not set a distinctive env var yet, so we rely on
        # the config marker (~/.kimi-code exists) plus the explicit env check.
        # The super() call already checks REPOWIRE_BACKEND.
        return False

    def install(self, options: BackendInstallOptions | None = None) -> list[BackendInstallMessage]:
        from repowire.installers.kimi_code import install_hooks, install_mcp

        messages: list[BackendInstallMessage] = []
        try:
            install_hooks()
            messages.append(BackendInstallMessage("success", "Kimi Code hooks installed"))
        except Exception as e:
            messages.append(BackendInstallMessage("error", f"Failed to install Kimi Code hooks: {e}"))
        try:
            install_mcp()
            messages.append(BackendInstallMessage("success", "Kimi Code MCP server configured"))
        except Exception as e:
            messages.append(BackendInstallMessage("error", f"Failed to configure Kimi Code MCP: {e}"))
        return messages

    def list_mcp_servers(self, peer):
        from repowire import peer_mcp

        return peer_mcp._kimi_list()

    def add_mcp_server(self, peer, spec) -> None:
        from repowire import peer_mcp

        peer_mcp._kimi_add(spec)

    def remove_mcp_server(self, peer, name: str) -> None:
        from repowire import peer_mcp

        peer_mcp._kimi_remove(name)
```

Update `AGENT_BACKENDS` dict to include Kimi:

```python
AGENT_BACKENDS: dict[AgentType, AgentBackend] = {
    AgentType.CLAUDE_CODE: ClaudeCodeBackend(),
    AgentType.OPENCODE: OpenCodeBackend(),
    AgentType.CODEX: CodexBackend(),
    AgentType.GEMINI: GeminiBackend(),
    AgentType.KIMI_CODE: KimiCodeBackend(),
    AgentType.ANTIGRAVITY: AntigravityBackend(),
    AgentType.PI: PiBackend(),
    AgentType.MCP_HTTP: McpHttpBackend(),
}
```

Update `detect_mcp_backend()` loop to include Kimi after Gemini:

```python
    for backend_type in (
        AgentType.CLAUDE_CODE,
        AgentType.GEMINI,
        AgentType.KIMI_CODE,
        AgentType.CODEX,
        AgentType.OPENCODE,
        AgentType.ANTIGRAVITY,
        AgentType.PI,
    ):
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_kimi_code_backend.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add repowire/agent_backends.py tests/test_kimi_code_backend.py
git commit -m "feat: add KimiCodeBackend with resume and MCP support"
```

---

## Task 3: Add Kimi Resume Validator

**Files:**
- Modify: `repowire/session/history.py`
- Test: `tests/test_kimi_code_resume.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_kimi_code_resume.py
import json
import tempfile
from pathlib import Path

from repowire.session.history import (
    _kimi_resumable,
    runtime_session_validation_status,
)


def test_kimi_resumable_found():
    with tempfile.TemporaryDirectory() as tmp:
        kimi_dir = Path(tmp) / ".kimi-code"
        kimi_dir.mkdir()
        sessions_dir = kimi_dir / "sessions" / "wd_test_abc123"
        sessions_dir.mkdir(parents=True)
        session_dir = sessions_dir / "session_abc123"
        session_dir.mkdir()

        index_path = kimi_dir / "session_index.jsonl"
        with open(index_path, "w") as f:
            f.write(json.dumps({
                "sessionId": "session_abc123",
                "sessionDir": str(session_dir),
                "workDir": "/tmp/test-project",
            }) + "\n")

        with tempfile.TemporaryDirectory() as proj:
            result = _kimi_resumable(proj, "session_abc123")
            assert result is True


def test_kimi_resumable_not_found():
    result = _kimi_resumable("/tmp/nonexistent", "session_nope")
    assert result is False


def test_runtime_session_validation_status_kimi():
    with tempfile.TemporaryDirectory() as tmp:
        kimi_dir = Path(tmp) / ".kimi-code"
        kimi_dir.mkdir()
        sessions_dir = kimi_dir / "sessions" / "wd_test_abc123"
        sessions_dir.mkdir(parents=True)
        session_dir = sessions_dir / "session_abc123"
        session_dir.mkdir()

        index_path = kimi_dir / "session_index.jsonl"
        with open(index_path, "w") as f:
            f.write(json.dumps({
                "sessionId": "session_abc123",
                "sessionDir": str(session_dir),
                "workDir": "/tmp/test-project",
            }) + "\n")

        with tempfile.TemporaryDirectory() as proj:
            status = runtime_session_validation_status(proj, "kimi-code", "session_abc123")
            assert status == "resumable"

            status_missing = runtime_session_validation_status(proj, "kimi-code", "session_nope")
            assert status_missing == "stale_missing_file"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_kimi_code_resume.py -v`

Expected: FAIL with `ImportError: cannot import name '_kimi_resumable'`

- [ ] **Step 3: Implement `_kimi_resumable` and register it**

Add `_kimi_resumable` after `_gemini_resumable` in `repowire/session/history.py`:

```python
def _kimi_resumable(peer_path: str | None, runtime_session_id: str) -> bool:
    """Validate that a Kimi session_id exists in the session index.

    Kimi stores sessions in ~/.kimi-code/session_index.jsonl (JSONL format),
    where each line maps sessionId -> sessionDir -> workDir. We check that
    the sessionId exists and its sessionDir is present on disk.
    """
    index_path = Path.home() / ".kimi-code" / "session_index.jsonl"
    if not index_path.is_file():
        return False

    try:
        with open(index_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("sessionId") != runtime_session_id:
                    continue
                session_dir = Path(entry.get("sessionDir", ""))
                if session_dir.is_dir():
                    return True
    except OSError:
        return False

    return False
```

Update `_RESUME_VALIDATORS` to include Kimi:

```python
_RESUME_VALIDATORS = {
    "claude-code": _claude_resumable,
    "codex": _codex_resumable,
    "opencode": _opencode_resumable,
    "pi": _pi_resumable,
    "antigravity": _antigravity_resumable,
    "gemini": _gemini_resumable,
    "kimi-code": _kimi_resumable,
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_kimi_code_resume.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add repowire/session/history.py tests/test_kimi_code_resume.py
git commit -m "feat: add Kimi session resume validator"
```

---

## Task 4: Update Hook Adapter for Kimi

**Files:**
- Modify: `repowire/hooks/adapters.py`
- Test: `tests/test_hooks_kimi.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hooks_kimi.py
from repowire.hooks.adapters import hook_output, normalize


def test_normalize_kimi_payload():
    raw = {
        "hook_event_name": "AfterAgent",
        "session_id": "session_abc123",
        "cwd": "/tmp/test",
        "transcript_path": None,
        "prompt_response": "Hello from Kimi",
    }
    payload = normalize(raw, backend="kimi")
    assert payload.event == "Stop"
    assert payload.session_id == "session_abc123"
    assert payload.cwd == "/tmp/test"
    assert payload.response_text == "Hello from Kimi"
    assert payload.backend == "kimi"


def test_hook_output_kimi():
    """Kimi uses the plugin system like Gemini; it needs explicit allow output."""
    import io
    import sys
    captured = io.StringIO()
    sys.stdout = captured
    hook_output("kimi")
    sys.stdout = sys.__stdout__
    assert captured.getvalue().strip() == '{"decision": "allow"}'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_hooks_kimi.py -v`

Expected: FAIL — `test_hook_output_kimi` fails because `"kimi"` is not in the `hook_output()` allow list

- [ ] **Step 3: Add `"kimi"` to `hook_output()`**

```python
# repowire/hooks/adapters.py
def hook_output(backend: str) -> None:
    """Print required hook output to stdout. Gemini needs explicit approval.

    Antigravity CLI shares Gemini's hook event names (BeforeAgent/AfterAgent)
    and JSON-decision shape based on its plugin schema, so it gets the same
    explicit allow. Whether the Antigravity CLI actually fires plugin-defined
    hooks today is pending upstream verification.

    Kimi Code uses a plugin system (superpowers) that follows the same
    BeforeAgent/AfterAgent pattern, so it also needs explicit allow.
    """
    if backend in ("gemini", "antigravity", "kimi"):
        print(json.dumps({"decision": "allow"}))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_hooks_kimi.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add repowire/hooks/adapters.py tests/test_hooks_kimi.py
git commit -m "feat: add Kimi to hook adapter explicit-allow list"
```

---

## Task 5: Update Protocol Peer Model Description

**Files:**
- Modify: `repowire/protocol/peers.py`

- [ ] **Step 1: Update `backend` field description**

```python
# repowire/protocol/peers.py
    backend: AgentType = Field(
        default=AgentType.CLAUDE_CODE,
        description=(
            "Agent type: claude-code, opencode, codex, gemini, kimi-code, "
            "antigravity, or pi"
        ),
    )
```

- [ ] **Step 2: Verify no tests break**

Run: `pytest tests/ -k peer -x -q`

Expected: All existing peer tests still pass

- [ ] **Step 3: Commit**

```bash
git add repowire/protocol/peers.py
git commit -m "docs: include kimi-code in Peer.backend description"
```

---

## Task 6: Run Full Test Suite

- [ ] **Step 1: Run all tests**

```bash
pytest tests/ -x -q
```

Expected: All tests pass (including new Kimi tests)

- [ ] **Step 2: Run linters**

```bash
ruff check repowire/ tests/
```

Expected: No errors

- [ ] **Step 3: Run type checker**

```bash
uv run ty check repowire/ tests/
```

Expected: No type errors

- [ ] **Step 4: Commit**

```bash
git commit -m "test: verify full suite passes with Kimi backend"
```

---

## Task 7: Manual Integration Test

- [ ] **Step 1: Install repowire with changes**

```bash
uv tool install . --force-reinstall
```

- [ ] **Step 2: Start daemon**

```bash
repowire service start
```

- [ ] **Step 3: Spawn Kimi peer**

```bash
repowire spawn --backend kimi-code --path /tmp/test-project
```

Expected: tmux pane opens with `kimi -C --yolo --auto /tmp/test-project`

- [ ] **Step 4: Verify peer registration**

```bash
curl http://localhost:8377/peers
```

Expected: JSON response includes a peer with `backend: "kimi-code"`

- [ ] **Step 5: From Claude peer, ask the Kimi peer**

In a Claude Code session with repowire MCP enabled:

```
> ask("kimi-test-project", "Hello from Claude. Can you hear me?")
```

Expected: Kimi peer receives the ask and can `ack` with a response

- [ ] **Step 6: Kill Kimi peer**

```bash
repowire kill-peer <peer-name>
```

Expected: Kimi tmux pane closes; peer shows `status: "offline"`

- [ ] **Step 7: Document results**

If any step fails, capture logs and file a follow-up issue before proceeding to M2.

---

## Spec Coverage Check

| Spec Requirement | Task |
|---|---|
| `AgentType.KIMI_CODE = "kimi-code"` | Task 1 |
| `KimiCodeBackend` class with resume, spawn, MCP | Task 2 |
| `detect_mcp_backend()` includes Kimi | Task 2 |
| `DEFAULT_SPAWN_COMMANDS` auto-populates from `AGENT_BACKENDS` | Task 2 (implicit via `default_command`) |
| Resume safety via `session_index.jsonl` | Task 3 |
| Hook adapter for Kimi (`hook_output`) | Task 4 |
| Config defaults (`kimi -C --yolo --auto`) | Task 2 (via `default_command`) |
| Testing: detection, resume, hooks | Tasks 1–4 |
| Manual integration test | Task 7 |

**Gaps:** None for M1 scope.

---

## Placeholder Scan

- [x] No "TBD", "TODO", "implement later", "fill in details"
- [x] Every test contains actual test code
- [x] Every implementation step contains actual code snippets
- [x] No references to undefined types/functions (all referenced names exist in spec or codebase)

---

## Type Consistency Check

| Name | First Definition | Later Uses | Consistent? |
|---|---|---|---|
| `AgentType.KIMI_CODE` | Task 1 enum | Task 2 backend, Task 3 validator | ✅ |
| `KimiCodeBackend` | Task 2 class | Task 2 tests | ✅ |
| `_kimi_resumable` | Task 3 function | Task 3 tests, `_RESUME_VALIDATORS` | ✅ |
| `resume_flag = "-S"` | Task 2 class attr | Task 2 `build_resume_command` test | ✅ |
| `default_command` | Task 2 class attr | Implicit in `DEFAULT_SPAWN_COMMANDS` | ✅ |

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-01-kimi-code-backend.md`.**

**Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Each task is self-contained (2–5 minutes of work), and a subagent can implement it with zero context of previous tasks beyond what's in the plan.

**2. Inline Execution** — Execute tasks in this session, batching related steps together with checkpoints for review.

**Which approach?**
