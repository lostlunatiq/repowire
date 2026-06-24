import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from repowire.peer_mcp import (
    BackendError,
    McpServerSpec,
    ServerNotFoundError,
    _abacusai_add,
    _abacusai_list,
    _abacusai_remove,
)


def test_abacusai_list_empty() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with patch("repowire.peer_mcp.ABACUSAI_MCP_PATH", tmp_path / "mcp.json"):
            assert _abacusai_list() == []


def test_abacusai_add_and_list() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with patch("repowire.peer_mcp.ABACUSAI_MCP_PATH", tmp_path / "mcp.json"):
            spec = McpServerSpec(name="test", command="node", args=["server.js"])
            _abacusai_add(spec)
            servers = _abacusai_list()
            assert len(servers) == 1
            assert servers[0].name == "test"
            assert servers[0].command == "node"
            assert servers[0].args == ["server.js"]


def test_abacusai_add_url() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with patch("repowire.peer_mcp.ABACUSAI_MCP_PATH", tmp_path / "mcp.json"):
            spec = McpServerSpec(name="http", url="https://example.com/mcp")
            _abacusai_add(spec)
            servers = _abacusai_list()
            assert len(servers) == 1
            assert servers[0].type == "http"
            assert servers[0].url == "https://example.com/mcp"


def test_abacusai_add_overwrites_existing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with patch("repowire.peer_mcp.ABACUSAI_MCP_PATH", tmp_path / "mcp.json"):
            _abacusai_add(McpServerSpec(name="srv", command="node", args=["old.js"]))
            _abacusai_add(McpServerSpec(name="srv", command="node", args=["new.js"]))
            servers = _abacusai_list()
            assert len(servers) == 1
            assert servers[0].args == ["new.js"]


def test_abacusai_add_corrupted_mcp_json_raises() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        mcp_path = tmp_path / "mcp.json"
        mcp_path.write_text("not json")
        with patch("repowire.peer_mcp.ABACUSAI_MCP_PATH", mcp_path):
            with pytest.raises(BackendError):
                _abacusai_add(McpServerSpec(name="x", command="node"))


def test_abacusai_remove() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with patch("repowire.peer_mcp.ABACUSAI_MCP_PATH", tmp_path / "mcp.json"):
            spec = McpServerSpec(name="rm", command="node", args=["server.js"])
            _abacusai_add(spec)
            _abacusai_remove("rm")
            assert _abacusai_list() == []


def test_abacusai_remove_missing_raises() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with patch("repowire.peer_mcp.ABACUSAI_MCP_PATH", tmp_path / "mcp.json"):
            with pytest.raises(ServerNotFoundError):
                _abacusai_remove("missing")


def test_abacusai_remove_persists_other_servers() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with patch("repowire.peer_mcp.ABACUSAI_MCP_PATH", tmp_path / "mcp.json"):
            _abacusai_add(McpServerSpec(name="keep", command="node"))
            _abacusai_add(McpServerSpec(name="drop", command="node"))
            _abacusai_remove("drop")
            servers = _abacusai_list()
            assert [s.name for s in servers] == ["keep"]
            data = json.loads(tmp_path.joinpath("mcp.json").read_text())
            assert "keep" in data["mcpServers"]
