# Kimi Code Installer + MCP Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the Kimi Code backend M1 integration by adding the missing installer module, wiring MCP server list/add/remove, fixing backend-string mismatches in hook handlers, and adding a user guide.

**Architecture:** Follow the established backend installer pattern (Claude Code / Codex / Gemini) with a Kimi-specific `repowire/installers/kimi_code.py` that writes lifecycle hooks to `~/.kimi-code/config.toml` and MCP servers to `~/.kimi-code/mcp.json`. Update `KimiCodeBackend.install()` to call it, add `_kimi_*` helpers to `peer_mcp.py`, and set `mcp_config_scope` on the backend. Fix `"kimi"` vs `"kimi-code"` backend string checks in `hooks/adapters.py` and `hooks/prompt_handler.py`.

**Tech Stack:** Python 3.12, TOML (stdlib `tomllib` / `tomli`), JSON, pytest.

---

## File Structure

| File | Responsibility |
|---|---|
| `repowire/installers/kimi_code.py` | New: install/remove hooks in `~/.kimi-code/config.toml` and MCP server in `~/.kimi-code/mcp.json` |
| `repowire/agent_backends.py` | Update `KimiCodeBackend.install()` to import/call the installer; set `mcp_config_scope`; implement `list/add/remove_mcp_servers()` |
| `repowire/peer_mcp.py` | Add `_kimi_list()`, `_kimi_add()`, `_kimi_remove()` helpers |
| `repowire/hooks/adapters.py` | Fix `hook_output()` to check `"kimi-code"` instead of `"kimi"` |
| `repowire/hooks/prompt_handler.py` | Fix `_budget_check_blocking()` to check `"kimi-code"` instead of `"kimi"` |
| `docs/guides/connect-kimi.md` | New user guide mirroring `connect-claude-code.md` |
| `tests/test_kimi_code_installer.py` | New tests for installer hooks + MCP read/write |
| `tests/test_peer_mcp_kimi.py` | New tests for `_kimi_list/add/remove()` |
| `tests/test_hooks_kimi.py` | Update `hook_output` test to use `"kimi-code"` and add `prompt_handler` budget-block test |

---

## Task 1: Fix backend-string mismatches in hook handlers

**Files:**
- Modify: `repowire/hooks/adapters.py`
- Modify: `repowire/hooks/prompt_handler.py`
- Test: `tests/test_hooks_kimi.py`

- [ ] **Step 1: Write failing tests for correct backend string**

```python
# tests/test_hooks_kimi.py
import io
import json
import sys
from unittest.mock import patch

from repowire.hooks.adapters import hook_output
from repowire.hooks.prompt_handler import main as prompt_main


def test_hook_output_kimi_code():
    """Kimi Code backend string is 'kimi-code', not 'kimi'."""
    captured = io.StringIO()
    sys.stdout = captured
    hook_output("kimi-code")
    sys.stdout = sys.__stdout__
    assert captured.getvalue().strip() == '{"decision": "allow"}'


def test_prompt_handler_budget_blocks_kimi_code():
    """Budget exhaustion should block UserPromptSubmit for kimi-code."""
    input_data = json.dumps({
        "hook_event_name": "UserPromptSubmit",
        "session_id": "session_abc",
        "cwd": "/tmp/test",
        "prompt": "hello",
    })
    with patch("repowire.hooks.prompt_handler.sys.stdin", io.StringIO(input_data)):
        with patch("repowire.hooks.prompt_handler.daemon_get") as mock_get:
            mock_get.return_value = {"remaining": 0, "used": 100000, "ceiling": 100000}
            captured = io.StringIO()
            with patch("repowire.hooks.prompt_handler.sys.stdout", captured):
                result = prompt_main(backend="kimi-code")
    assert result == 0
    assert "deny" in captured.getvalue()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_hooks_kimi.py -v
```

Expected: `test_hook_output_kimi_code` fails (no output); `test_prompt_handler_budget_blocks_kimi_code` fails (no deny).

- [ ] **Step 3: Fix backend string checks**

```python
# repowire/hooks/adapters.py
    if backend in ("gemini", "antigravity", "kimi-code"):
        print(json.dumps({"decision": "allow"}))
```

```python
# repowire/hooks/prompt_handler.py
    if backend in ("gemini", "kimi-code", "antigravity"):
        print(json.dumps({"decision": "deny", "reason": reason}))
        return reason
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_hooks_kimi.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add repowire/hooks/adapters.py repowire/hooks/prompt_handler.py tests/test_hooks_kimi.py
git commit -m "fix: use 'kimi-code' backend string in hook handlers"
```

---

## Task 2: Add Kimi Code installer module

**Files:**
- Create: `repowire/installers/kimi_code.py`
- Test: `tests/test_kimi_code_installer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_kimi_code_installer.py
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from repowire.installers import kimi_code


def test_install_hooks_creates_config(tmp_path):
    home = tmp_path
    config_path = home / ".kimi-code" / "config.toml"
    with patch("repowire.installers.kimi_code.KIMI_HOME", home / ".kimi-code"):
        with patch("repowire.installers.kimi_code.CONFIG_PATH", config_path):
            assert kimi_code.install_hooks() is True
    assert config_path.exists()
    text = config_path.read_text()
    assert 'event = "SessionStart"' in text
    assert 'event = "UserPromptSubmit"' in text
    assert 'event = "Stop"' in text
    assert "repowire hook session --backend=kimi-code" in text
    assert "repowire hook prompt --backend=kimi-code" in text
    assert "repowire hook stop --backend=kimi-code" in text


def test_install_mcp_creates_mcp_json(tmp_path):
    home = tmp_path
    mcp_path = home / ".kimi-code" / "mcp.json"
    with patch("repowire.installers.kimi_code.KIMI_HOME", home / ".kimi-code"):
        with patch("repowire.installers.kimi_code.MCP_PATH", mcp_path):
            assert kimi_code.install_mcp() is True
    assert mcp_path.exists()
    data = json.loads(mcp_path.read_text())
    assert "repowire" in data["mcpServers"]
    assert data["mcpServers"]["repowire"]["command"] == "repowire"
    assert data["mcpServers"]["repowire"]["args"] == ["mcp"]
    assert data["mcpServers"]["repowire"]["env"]["REPOWIRE_BACKEND"] == "kimi-code"


def test_uninstall_hooks_removes_repowire_entries(tmp_path):
    home = tmp_path
    config_path = home / ".kimi-code" / "config.toml"
    with patch("repowire.installers.kimi_code.KIMI_HOME", home / ".kimi-code"):
        with patch("repowire.installers.kimi_code.CONFIG_PATH", config_path):
            kimi_code.install_hooks()
            assert kimi_code.uninstall_hooks() is True
    text = config_path.read_text()
    assert "repowire" not in text


def test_uninstall_mcp_removes_repowire(tmp_path):
    home = tmp_path
    mcp_path = home / ".kimi-code" / "mcp.json"
    with patch("repowire.installers.kimi_code.KIMI_HOME", home / ".kimi-code"):
        with patch("repowire.installers.kimi_code.MCP_PATH", mcp_path):
            kimi_code.install_mcp()
            assert kimi_code.uninstall_mcp() is True
    data = json.loads(mcp_path.read_text())
    assert "repowire" not in data.get("mcpServers", {})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_kimi_code_installer.py -v
```

Expected: ImportError for `repowire.installers.kimi_code`.

- [ ] **Step 3: Implement installer module**

```python
# repowire/installers/kimi_code.py
"""Kimi Code installer — hooks and MCP server configuration.

Kimi Code stores lifecycle hooks in ~/.kimi-code/config.toml under the
[[hooks]] array, and MCP servers in ~/.kimi-code/mcp.json under mcpServers.
"""

from __future__ import annotations

import json
from pathlib import Path

KIMI_HOME = Path.home() / ".kimi-code"
CONFIG_PATH = KIMI_HOME / "config.toml"
MCP_PATH = KIMI_HOME / "mcp.json"

HOOK_EVENTS = ["SessionStart", "UserPromptSubmit", "Stop"]


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text(text)
    tmp.replace(path)


def _load_config_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        import tomllib

        return tomllib.loads(path.read_text())
    except Exception:
        return {}


def _save_config_toml(path: Path, data: dict) -> None:
    import tomli_w

    _atomic_write_text(path, tomli_w.dumps(data))


def _load_mcp_json(path: Path) -> dict:
    if not path.exists():
        return {"mcpServers": {}}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"mcpServers": {}}


def _save_mcp_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def _is_repowire_hook(entry: dict) -> bool:
    return "repowire" in entry.get("command", "")


_REPOWIRE_HOOKS = {
    "SessionStart": {
        "event": "SessionStart",
        "command": "repowire hook session --backend=kimi-code",
        "timeout": 30,
    },
    "UserPromptSubmit": {
        "event": "UserPromptSubmit",
        "command": "repowire hook prompt --backend=kimi-code",
        "timeout": 30,
    },
    "Stop": {
        "event": "Stop",
        "command": "repowire hook stop --backend=kimi-code",
        "timeout": 30,
    },
}


def install_hooks() -> bool:
    """Install repowire hooks into ~/.kimi-code/config.toml."""
    data = _load_config_toml(CONFIG_PATH)
    hooks = list(data.get("hooks", []))
    hooks = [h for h in hooks if not _is_repowire_hook(h)]
    for event in HOOK_EVENTS:
        hooks.append(dict(_REPOWIRE_HOOKS[event]))
    data["hooks"] = hooks
    _save_config_toml(CONFIG_PATH, data)
    return True


def uninstall_hooks() -> bool:
    """Remove repowire hooks from ~/.kimi-code/config.toml."""
    if not CONFIG_PATH.exists():
        return False
    data = _load_config_toml(CONFIG_PATH)
    hooks = list(data.get("hooks", []))
    original_len = len(hooks)
    hooks = [h for h in hooks if not _is_repowire_hook(h)]
    if len(hooks) == original_len:
        return False
    if hooks:
        data["hooks"] = hooks
    else:
        data.pop("hooks", None)
    _save_config_toml(CONFIG_PATH, data)
    return True


def install_mcp() -> bool:
    """Add repowire MCP server to ~/.kimi-code/mcp.json."""
    data = _load_mcp_json(MCP_PATH)
    servers = data.setdefault("mcpServers", {})
    servers["repowire"] = {
        "command": "repowire",
        "args": ["mcp"],
        "env": {"REPOWIRE_BACKEND": "kimi-code"},
    }
    _save_mcp_json(MCP_PATH, data)
    return True


def uninstall_mcp() -> bool:
    """Remove repowire MCP server from ~/.kimi-code/mcp.json."""
    if not MCP_PATH.exists():
        return False
    data = _load_mcp_json(MCP_PATH)
    servers = data.get("mcpServers", {})
    if "repowire" not in servers:
        return False
    del servers["repowire"]
    if not servers:
        data.pop("mcpServers", None)
    _save_mcp_json(MCP_PATH, data)
    return True


def check_hooks_installed() -> bool:
    """Check if repowire hooks are configured in Kimi Code."""
    if not CONFIG_PATH.exists():
        return False
    data = _load_config_toml(CONFIG_PATH)
    return any(_is_repowire_hook(h) for h in data.get("hooks", []))


def check_mcp_installed() -> bool:
    """Check if repowire MCP server is configured in Kimi Code."""
    if not MCP_PATH.exists():
        return False
    data = _load_mcp_json(MCP_PATH)
    return "repowire" in data.get("mcpServers", {})
```

Note: If `tomli_w` is not a project dependency, use manual TOML string rendering instead (like Codex installer does for `config.toml`). Verify with `grep -r tomli_w pyproject.toml uv.lock`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_kimi_code_installer.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add repowire/installers/kimi_code.py tests/test_kimi_code_installer.py
git commit -m "feat: add Kimi Code installer for hooks and MCP config"
```

---

## Task 3: Wire installer into KimiCodeBackend

**Files:**
- Modify: `repowire/agent_backends.py`
- Test: `tests/test_kimi_code_backend.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_kimi_code_backend.py (append)
from unittest.mock import patch


def test_kimi_backend_install_calls_installer():
    backend = KimiCodeBackend()
    with patch("repowire.installers.kimi_code.install_hooks") as mock_hooks:
        with patch("repowire.installers.kimi_code.install_mcp") as mock_mcp:
            messages = backend.install()
    mock_hooks.assert_called_once()
    mock_mcp.assert_called_once()
    assert any("hooks installed" in m.text for m in messages)
    assert any("MCP server configured" in m.text for m in messages)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_kimi_code_backend.py::test_kimi_backend_install_calls_installer -v
```

Expected: FAIL — installer functions not called (current implementation catches ModuleNotFoundError and returns info message).

- [ ] **Step 3: Update KimiCodeBackend**

```python
# repowire/agent_backends.py
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
        return bool(env.get("KIMI_CODE_SESSION_ID"))

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

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_kimi_code_backend.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add repowire/agent_backends.py tests/test_kimi_code_backend.py
git commit -m "feat: wire Kimi Code installer and MCP config into backend"
```

---

## Task 4: Add Kimi MCP helpers to peer_mcp.py

**Files:**
- Modify: `repowire/peer_mcp.py`
- Test: `tests/test_peer_mcp_kimi.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_peer_mcp_kimi.py
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from repowire.peer_mcp import (
    DuplicateServerError,
    McpServerSpec,
    ServerNotFoundError,
    _kimi_add,
    _kimi_list,
    _kimi_remove,
)


def test_kimi_list_empty():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with patch("repowire.peer_mcp.KIMI_MCP_PATH", tmp_path / "mcp.json"):
            assert _kimi_list() == []


def test_kimi_add_and_list():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with patch("repowire.peer_mcp.KIMI_MCP_PATH", tmp_path / "mcp.json"):
            spec = McpServerSpec(name="test", command="node", args=["server.js"])
            _kimi_add(spec)
            servers = _kimi_list()
            assert len(servers) == 1
            assert servers[0].name == "test"
            assert servers[0].command == "node"
            assert servers[0].args == ["server.js"]


def test_kimi_add_duplicate_raises():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with patch("repowire.peer_mcp.KIMI_MCP_PATH", tmp_path / "mcp.json"):
            spec = McpServerSpec(name="dup", command="node", args=["server.js"])
            _kimi_add(spec)
            try:
                _kimi_add(spec)
                raise AssertionError("expected DuplicateServerError")
            except DuplicateServerError:
                pass


def test_kimi_remove():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with patch("repowire.peer_mcp.KIMI_MCP_PATH", tmp_path / "mcp.json"):
            spec = McpServerSpec(name="rm", command="node", args=["server.js"])
            _kimi_add(spec)
            _kimi_remove("rm")
            assert _kimi_list() == []


def test_kimi_remove_missing_raises():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with patch("repowire.peer_mcp.KIMI_MCP_PATH", tmp_path / "mcp.json"):
            try:
                _kimi_remove("missing")
                raise AssertionError("expected ServerNotFoundError")
            except ServerNotFoundError:
                pass
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_peer_mcp_kimi.py -v
```

Expected: ImportError for `_kimi_list`.

- [ ] **Step 3: Implement helpers**

```python
# repowire/peer_mcp.py (append after gemini helpers)

# ---------------------------------------------------------------------------
# kimi-code: edit ~/.kimi-code/mcp.json mcpServers block
# ---------------------------------------------------------------------------

KIMI_MCP_PATH = Path.home() / ".kimi-code" / "mcp.json"


def _kimi_load() -> dict[str, Any]:
    if not KIMI_MCP_PATH.exists():
        return {"mcpServers": {}}
    try:
        return json.loads(KIMI_MCP_PATH.read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise BackendError(f"failed to read kimi mcp config: {e}") from e


def _kimi_save(data: dict[str, Any]) -> None:
    _atomic_write_text(KIMI_MCP_PATH, json.dumps(data, indent=2))


def _kimi_list() -> list[McpServerEntry]:
    data = _kimi_load()
    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict):
        return []
    out: list[McpServerEntry] = []
    for name, body in servers.items():
        if not isinstance(body, dict):
            continue
        env = body.get("env", {})
        env_keys: list[str] = [str(k) for k in env.keys()] if isinstance(env, dict) else []
        url = body.get("url")
        srv_type = "http" if url else "stdio"
        out.append(
            McpServerEntry(
                name=name,
                scope="user",
                type=srv_type,
                command=body.get("command") if isinstance(body.get("command"), str) else None,
                args=list(body.get("args", []) or []),
                url=url if isinstance(url, str) else None,
                env_keys=env_keys,
            )
        )
    return out


def _kimi_add(spec: McpServerSpec) -> None:
    data = _kimi_load()
    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise BackendError("kimi mcpServers must be an object")
    if spec.name in servers:
        raise DuplicateServerError(f"server {spec.name!r} already exists")
    entry: dict[str, Any] = {}
    if spec.command:
        entry["command"] = spec.command
    if spec.args:
        entry["args"] = list(spec.args)
    if spec.url:
        entry["url"] = spec.url
    if spec.env:
        entry["env"] = dict(spec.env)
    servers[spec.name] = entry
    data["mcpServers"] = servers
    _kimi_save(data)


def _kimi_remove(name: str) -> None:
    data = _kimi_load()
    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict) or name not in servers:
        raise ServerNotFoundError(f"server {name!r} not configured")
    del servers[name]
    _kimi_save(data)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_peer_mcp_kimi.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add repowire/peer_mcp.py tests/test_peer_mcp_kimi.py
git commit -m "feat: add Kimi Code MCP list/add/remove helpers"
```

---

## Task 5: Add user guide

**Files:**
- Create: `docs/guides/connect-kimi.md`

- [ ] **Step 1: Read `docs/guides/connect-claude-code.md` as template**

```bash
cat docs/guides/connect-claude-code.md
```

- [ ] **Step 2: Write `docs/guides/connect-kimi.md`**

Mirror the Claude guide structure but use Kimi-specific commands:
- Install Kimi Code CLI from `https://code.kimi.com`
- Run `repowire setup --backend kimi-code` (or `repowire kimi setup` if such command exists — verify in `repowire/cli.py`)
- Verify hooks with `repowire kimi status` (verify command exists)
- Start a Kimi session and confirm peer appears at `http://localhost:8377/peers`

If no dedicated `repowire kimi` command exists, document using `repowire setup` which auto-detects installed backends.

- [ ] **Step 3: Verify guide renders**

```bash
python -m markdown docs/guides/connect-kimi.md > /dev/null || echo "markdown module optional"
```

- [ ] **Step 4: Commit**

```bash
git add docs/guides/connect-kimi.md
git commit -m "docs: add Kimi Code connection guide"
```

---

## Task 6: Full quality gates

- [ ] **Step 1: Run targeted tests**

```bash
pytest tests/test_kimi_code_backend.py tests/test_kimi_code_resume.py tests/test_kimi_code_agent_type.py tests/test_kimi_code_installer.py tests/test_peer_mcp_kimi.py tests/test_hooks_kimi.py -v
```

Expected: All PASS.

- [ ] **Step 2: Run full test suite**

```bash
pytest tests/ -x -q
```

Expected: All PASS.

- [ ] **Step 3: Run linters**

```bash
ruff check repowire/ tests/
```

Expected: No errors.

- [ ] **Step 4: Run type checker**

```bash
uv run ty check repowire/ tests/
```

Expected: No type errors.

- [ ] **Step 5: Commit**

```bash
git commit -m "test: verify full suite passes with Kimi installer and MCP"
```

---

## Spec Coverage Check

| Spec Requirement | Task |
|---|---|
| Kimi hooks installed in `~/.kimi-code/config.toml` | Task 2 |
| Kimi MCP server configured in `~/.kimi-code/mcp.json` | Task 2 |
| `KimiCodeBackend.install()` installs hooks + MCP | Task 3 |
| `KimiCodeBackend` supports MCP list/add/remove | Tasks 3 & 4 |
| Backend string consistency (`kimi-code`) | Task 1 |
| User guide for connecting Kimi | Task 5 |
| Tests for installer and MCP helpers | Tasks 2 & 4 |

**Gaps:** None for this focused M1-completion scope.

---

## Placeholder Scan

- [x] No "TBD", "TODO", "implement later", "fill in details"
- [x] Every test contains actual test code
- [x] Every implementation step contains actual code snippets
- [x] No references to undefined types/functions

## Type Consistency Check

| Name | First Definition | Later Uses | Consistent? |
|---|---|---|---|
| `KIMI_HOME` / `CONFIG_PATH` / `MCP_PATH` | Task 2 installer | Task 2 tests | ✅ |
| `_kimi_list/add/remove` | Task 4 peer_mcp | Task 3 backend methods, Task 4 tests | ✅ |
| `backend="kimi-code"` | Task 1 handlers | Task 2 installer commands, Task 1 tests | ✅ |

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-23-kimi-code-installer-and-mcp.md`.**

**Two execution options:**

**1. Subagent-Driven (recommended)** — Dispatch a fresh subagent per task, review between tasks.

**2. Inline Execution** — Execute tasks in this session, batching related steps together with checkpoints.

**Recommended:** Inline Execution because the changes are tightly coupled (installer → backend → peer_mcp) and the total scope is small.
