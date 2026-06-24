"""Abacus AI CLI installer — MCP server configuration.

Abacus AI CLI loads MCP servers via the repeatable --mcp-config flag. Repowire
manages a dedicated file at ~/.abacusai/repowire-mcp.json so it can be added
and removed without touching any other Abacus AI configuration.
"""

from __future__ import annotations

import json
from pathlib import Path

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
    """Add the repowire MCP server to ~/.abacusai/repowire-mcp.json."""
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
    """Remove the repowire MCP server from ~/.abacusai/repowire-mcp.json."""
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
    """Check whether the repowire MCP server is configured for Abacus AI."""
    if not MCP_PATH.exists():
        return False
    data = _load_mcp_json(MCP_PATH)
    return "repowire" in data.get("mcpServers", {})
