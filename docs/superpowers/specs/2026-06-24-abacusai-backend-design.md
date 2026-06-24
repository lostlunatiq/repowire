# Abacus AI CLI Backend Integration Design

> **Status:** Ready for implementation  
> **Scope:** Add Abacus AI CLI (`abacusai`) as a first-class Repowire backend: spawn, resume, MCP server management, and mesh registration via MCP lazy registration.  
> **Assumption:** "abacuscli" refers to the public **Abacus AI CLI** (`abacusai`) installed at `~/.abacusai/bin/abacusai`. If this is a different tool, the config paths and CLI name in this spec need to be adjusted.

---

## Table of Contents

1. [Background & Constraints](#1-background--constraints)
2. [Approaches Considered](#2-approaches-considered)
3. [Selected Design](#3-selected-design)
4. [Components](#4-components)
5. [Testing Strategy](#5-testing-strategy)
6. [Risks & Mitigations](#6-risks--mitigations)

---

## 1. Background & Constraints

The local environment has `abacusai` installed:

- CLI path: `~/.abacusai/bin/abacusai`
- Resume: `abacusai --resume <id>` and `abacusai -c`
- Auto-approval: `--permission-mode yolo` and `--auto-accept-edits`
- MCP: `--mcp-config <file>` (repeatable), config file shape is `{"mcpServers": {...}}` (same as Kimi/Gemini)
- No native lifecycle hook system (no `SessionStart` / `UserPromptSubmit` / `Stop` hooks exposed in `--help`)

Because `abacusai` does not fire lifecycle hooks, the traditional Repowire hook-driven registration path is unavailable. The daemon-side MCP server already supports **lazy registration**: the first MCP tool call from the runtime registers the peer via `/peers`. We rely on that for mesh identity, and accept that inbound tmux injection requires an additional sidecar if full bidirectional mesh delivery is needed later.

---

## 2. Approaches Considered

### A. Full hook emulation via wrapper script
Create a `repowire-abacusai` wrapper that runs `repowire hook session`, starts the existing `websocket_hook.py` sidecar, and then `exec`s `abacusai`. This would give Repowire the same hook events and ws-hook inbound injection it has for Claude/Kimi/Gemini.

- **Pros:** Full feature parity with hook backends.
- **Cons:** Fragile (wrapper must hide SessionStart output from the terminal, manage sidecar lifetime, and survive `abacusai` process replacement); over-engineered for an initial integration.
- **Verdict:** Deferred.

### B. Backend-only integration (spawn + resume + MCP)
Add `AbacusAIBackend` and an installer that writes a dedicated MCP config file (`~/.abacusai/repowire-mcp.json`). The default spawn command includes `--mcp-config ~/.abacusai/repowire-mcp.json`. Mesh registration happens lazily when the agent calls a Repowire MCP tool.

- **Pros:** Minimal, follows existing backend patterns, gives users immediate value (spawn, resume, MCP tools).
- **Cons:** No automatic SessionStart registration; no inbound ask/notify tmux injection until a sidecar is added; peer status is not updated on prompt/submit.
- **Verdict:** Selected for this slice.

### C. MCP-native HTTP registration only
Skip backend-specific spawn integration and rely entirely on the daemon-mounted Streamable HTTP MCP endpoint.

- **Pros:** No per-runtime installer needed.
- **Cons:** Does not let `repowire spawn --backend abacusai` work; loses resume and per-backend command defaults.
- **Verdict:** Rejected.

---

## 3. Selected Design

Add an `abacusai` backend entry following the same pattern as `KimiCodeBackend` and `GeminiBackend`, but with an installer that targets the Abacus AI CLI's file-based MCP config rather than lifecycle hooks.

### 3.1 Runtime capabilities

| Capability | Abacus AI CLI |
|---|---|
| Mesh peer registration | Lazy via MCP tool call |
| Session resume | `abacusai --resume <id>` |
| Spawn command | `abacusai --permission-mode yolo --auto-accept-edits --mcp-config ~/.abacusai/repowire-mcp.json` |
| MCP server config | `~/.abacusai/repowire-mcp.json` |
| Native hooks | None |
| Inbound tmux injection | Not in this slice |

### 3.2 Detection priority

1. `REPOWIRE_BACKEND=abacusai` explicit env var.
2. `ABACUSAI_SESSION_ID` env var (forward-looking marker for future releases).
3. `~/.abacusai` config directory marker + `abacusai` on `PATH`.
4. Fallback to other backends.

---

## 4. Components

### 4.1 `repowire/agent_types.py`

Add:

```python
ABACUSAI = "abacusai"
```

### 4.2 `repowire/agent_backends.py`

Add `AbacusAIBackend` singleton:

```python
class AbacusAIBackend(AgentBackend):
    agent_type = AgentType.ABACUSAI
    display_name = "Abacus AI"
    cli_names = ("abacusai",)
    config_markers = (Path.home() / ".abacusai",)
    default_command = (
        "abacusai --permission-mode yolo --auto-accept-edits "
        "--mcp-config ~/.abacusai/repowire-mcp.json"
    )
    supports_resume = True
    resume_strategy = "abacusai_conversation"
    resume_flag = "--resume"
    mcp_config_scope = McpConfigScope(
        owner="backend",
        effective_scope="backend_global",
        label="Abacus AI global backend config",
        description=(
            "Abacus AI MCP edits target a dedicated user-level config file "
            "loaded via --mcp-config."
        ),
    )

    @classmethod
    def mcp_runtime_matches(cls, env: Mapping[str, str]) -> bool:
        if super().mcp_runtime_matches(env):
            return True
        return bool(env.get("ABACUSAI_SESSION_ID"))

    def install(self, options=None):
        from repowire.installers.abacusai import install_mcp
        messages: list[BackendInstallMessage] = []
        try:
            install_mcp()
            messages.append(
                BackendInstallMessage(
                    "success", "Abacus AI MCP server configured"
                )
            )
        except Exception as e:
            messages.append(
                BackendInstallMessage(
                    "error", f"Failed to configure Abacus AI MCP: {e}"
                )
            )
        messages.append(
            BackendInstallMessage(
                "info",
                "abacusai does not expose lifecycle hooks; mesh registration "
                "happens lazily when the agent calls a Repowire MCP tool.",
            )
        )
        return messages

    def list_mcp_servers(self, peer):
        from repowire import peer_mcp
        return peer_mcp._abacusai_list()

    def add_mcp_server(self, peer, spec):
        from repowire import peer_mcp
        peer_mcp._abacusai_add(spec)

    def remove_mcp_server(self, peer, name):
        from repowire import peer_mcp
        peer_mcp._abacusai_remove(name)
```

Register it in `AGENT_BACKENDS` and include `AgentType.ABACUSAI` in the `detect_mcp_backend()` loop.

### 4.3 `repowire/installers/abacusai.py`

New installer module:

```python
from pathlib import Path
import json

ABACUSAI_HOME = Path.home() / ".abacusai"
MCP_PATH = ABACUSAI_HOME / "repowire-mcp.json"


def _load_mcp_json(path: Path) -> dict:
    if not path.exists():
        return {"mcpServers": {}}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"mcpServers": {}}


def _save_mcp_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def install_mcp() -> bool:
    data = _load_mcp_json(MCP_PATH)
    servers = data.setdefault("mcpServers", {})
    servers["repowire"] = {
        "command": "repowire",
        "args": ["mcp"],
        "env": {"REPOWIRE_BACKEND": "abacusai"},
    }
    _save_mcp_json(MCP_PATH, data)
    return True


def uninstall_mcp() -> bool:
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


def check_mcp_installed() -> bool:
    if not MCP_PATH.exists():
        return False
    data = _load_mcp_json(MCP_PATH)
    return "repowire" in data.get("mcpServers", {})
```

### 4.4 `repowire/peer_mcp.py`

Add helpers mirroring `_kimi_*` but using `ABACUSAI_MCP_PATH = Path.home() / ".abacusai" / "repowire-mcp.json"`.

### 4.5 `repowire/cli.py` / `setup`

- The existing `repowire setup` auto-detect flow iterates `AGENT_BACKENDS`, so it will pick up `AbacusAIBackend` automatically.
- Update the "no agents detected" message to mention `abacusai`.
- Add `_uninstall_abacusai()` to the global uninstall command and call it alongside the other backends.

### 4.6 Docs

Add `docs/guides/connect-abacusai.md` describing:

1. `repowire setup` (auto-detects `abacusai`).
2. The dedicated MCP file location (`~/.abacusai/repowire-mcp.json`).
3. Limitations: no native hooks, lazy registration, no inbound tmux injection yet.
4. Update `docs/guides/index.md`.

---

## 5. Testing Strategy

| Test | Location |
|---|---|
| Backend registered and attributes | `tests/test_abacusai_backend.py` |
| MCP install/uninstall/check | `tests/test_abacusai_installer.py` |
| `_abacusai_list/add/remove` | `tests/test_peer_mcp_abacusai.py` |
| `detect_mcp_backend` explicit env | `tests/test_abacusai_backend.py` |
| Corrupted MCP file raises `BackendError` | `tests/test_peer_mcp_abacusai.py` |

All tests mock the config home (`tmp_path`) by patching module-level path constants, following the Kimi test pattern.

---

## 6. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| `abacusai` CLI name or flag changes | Keep command in `default_command`; users can override via `daemon.spawn.commands`. |
| No hooks means no automatic registration | Documented; MCP lazy registration covers outbound mesh use. |
| Inbound ask/notify cannot inject without a sidecar | Out of scope for this slice; future work can add a wrapper/sidecar if needed. |
| Confusion with a different tool named `abacuscli` | Spec and docs state the assumption clearly; user can correct if wrong. |
| `~/.abacusai` may not exist until first login | Installer creates the directory; `detect_installed` falls back to `cli_names`. |
