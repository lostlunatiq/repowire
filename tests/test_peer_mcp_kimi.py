import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from repowire.peer_mcp import (
    BackendError,
    McpServerSpec,
    ServerNotFoundError,
    _kimi_add,
    _kimi_list,
    _kimi_remove,
)


def test_kimi_list_empty() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with patch("repowire.peer_mcp.KIMI_MCP_PATH", tmp_path / "mcp.json"):
            assert _kimi_list() == []


def test_kimi_add_and_list() -> None:
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


def test_kimi_add_url() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with patch("repowire.peer_mcp.KIMI_MCP_PATH", tmp_path / "mcp.json"):
            spec = McpServerSpec(name="http", url="https://example.com/mcp")
            _kimi_add(spec)
            servers = _kimi_list()
            assert len(servers) == 1
            assert servers[0].type == "http"
            assert servers[0].url == "https://example.com/mcp"


def test_kimi_add_overwrites_existing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with patch("repowire.peer_mcp.KIMI_MCP_PATH", tmp_path / "mcp.json"):
            _kimi_add(McpServerSpec(name="srv", command="node", args=["old.js"]))
            _kimi_add(McpServerSpec(name="srv", command="node", args=["new.js"]))
            servers = _kimi_list()
            assert len(servers) == 1
            assert servers[0].args == ["new.js"]


def test_kimi_add_corrupted_mcp_json_raises() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        mcp_path = tmp_path / "mcp.json"
        mcp_path.write_text("not json")
        with patch("repowire.peer_mcp.KIMI_MCP_PATH", mcp_path):
            with pytest.raises(BackendError):
                _kimi_add(McpServerSpec(name="x", command="node"))


def test_kimi_remove() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with patch("repowire.peer_mcp.KIMI_MCP_PATH", tmp_path / "mcp.json"):
            spec = McpServerSpec(name="rm", command="node", args=["server.js"])
            _kimi_add(spec)
            _kimi_remove("rm")
            assert _kimi_list() == []


def test_kimi_remove_missing_raises() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with patch("repowire.peer_mcp.KIMI_MCP_PATH", tmp_path / "mcp.json"):
            with pytest.raises(ServerNotFoundError):
                _kimi_remove("missing")


def test_kimi_remove_persists_other_servers() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with patch("repowire.peer_mcp.KIMI_MCP_PATH", tmp_path / "mcp.json"):
            _kimi_add(McpServerSpec(name="keep", command="node"))
            _kimi_add(McpServerSpec(name="drop", command="node"))
            _kimi_remove("drop")
            servers = _kimi_list()
            assert [s.name for s in servers] == ["keep"]
            # Verify JSON on disk still has mcpServers wrapper
            data = json.loads(tmp_path.joinpath("mcp.json").read_text())
            assert "keep" in data["mcpServers"]


