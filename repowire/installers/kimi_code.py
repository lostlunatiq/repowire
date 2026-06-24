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
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text)
    tmp.replace(path)


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


def _render_hook_block(event: str, command: str, timeout: int = 30) -> str:
    return (
        f"[[hooks]]\n"
        f'event = "{event}"\n'
        f'command = "{command}"\n'
        f"timeout = {timeout}\n"
    )


def _remove_repowire_hooks(content: str) -> str:
    """Remove repowire-managed [[hooks]] blocks from config.toml text.

    A [[hooks]] block starts with `[[hooks]]` and ends at the next `[[` or
    `[section]` line. We remove blocks whose `command` contains "repowire".
    """
    lines = content.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped == "[[hooks]]":
            block_lines = [line]
            j = i + 1
            while j < len(lines):
                next_line = lines[j]
                next_stripped = next_line.strip()
                if next_stripped.startswith("[[") or (
                    next_stripped.startswith("[") and next_stripped.endswith("]")
                ):
                    break
                block_lines.append(next_line)
                j += 1
            block_text = "".join(block_lines)
            if "repowire" not in block_text:
                out.extend(block_lines)
            i = j
            continue
        out.append(line)
        i += 1
    return "".join(out).rstrip() + "\n"


def _repowire_hook_command(event: str) -> str:
    canonical = event.lower()
    canonical = canonical.replace("userpromptsubmit", "prompt")
    canonical = canonical.replace("sessionstart", "session")
    return f"repowire hook {canonical} --backend=kimi-code"


def install_hooks() -> bool:
    """Install repowire hooks into ~/.kimi-code/config.toml."""
    content = CONFIG_PATH.read_text() if CONFIG_PATH.exists() else ""
    content = _remove_repowire_hooks(content)
    blocks = []
    for event in HOOK_EVENTS:
        cmd = _repowire_hook_command(event)
        blocks.append(_render_hook_block(event, cmd))
    if content and not content.endswith("\n"):
        content += "\n"
    content += "\n".join(blocks)
    _atomic_write_text(CONFIG_PATH, content)
    return True


def uninstall_hooks() -> bool:
    """Remove repowire hooks from ~/.kimi-code/config.toml."""
    if not CONFIG_PATH.exists():
        return False
    original = CONFIG_PATH.read_text()
    cleaned = _remove_repowire_hooks(original)
    if cleaned == original:
        return False
    _atomic_write_text(CONFIG_PATH, cleaned)
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
    content = CONFIG_PATH.read_text()
    # Crude check: any repowire hook command in a hooks block
    in_hooks = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped == "[[hooks]]":
            in_hooks = True
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            in_hooks = False
            continue
        if in_hooks and "repowire" in stripped and "command" in stripped:
            return True
    return False


def check_mcp_installed() -> bool:
    """Check if repowire MCP server is configured in Kimi Code."""
    if not MCP_PATH.exists():
        return False
    data = _load_mcp_json(MCP_PATH)
    return "repowire" in data.get("mcpServers", {})
