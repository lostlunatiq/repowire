import json
from pathlib import Path
from unittest.mock import patch

from repowire.installers import abacusai


def test_install_mcp_creates_mcp_json(tmp_path: Path) -> None:
    home = tmp_path
    mcp_path = home / ".abacusai" / "repowire-mcp.json"
    with patch.object(abacusai, "ABACUSAI_HOME", home / ".abacusai"):
        with patch.object(abacusai, "MCP_PATH", mcp_path):
            assert abacusai.install_mcp() is True
    assert mcp_path.exists()
    data = json.loads(mcp_path.read_text())
    assert "repowire" in data["mcpServers"]
    assert data["mcpServers"]["repowire"]["command"] == "repowire"
    assert data["mcpServers"]["repowire"]["args"] == ["mcp"]
    assert data["mcpServers"]["repowire"]["env"]["REPOWIRE_BACKEND"] == "abacusai"


def test_uninstall_mcp_removes_repowire(tmp_path: Path) -> None:
    home = tmp_path
    mcp_path = home / ".abacusai" / "repowire-mcp.json"
    with patch.object(abacusai, "ABACUSAI_HOME", home / ".abacusai"):
        with patch.object(abacusai, "MCP_PATH", mcp_path):
            abacusai.install_mcp()
            assert abacusai.uninstall_mcp() is True
    data = json.loads(mcp_path.read_text())
    assert "repowire" not in data.get("mcpServers", {})


def test_check_mcp_installed(tmp_path: Path) -> None:
    home = tmp_path
    mcp_path = home / ".abacusai" / "repowire-mcp.json"
    with patch.object(abacusai, "ABACUSAI_HOME", home / ".abacusai"):
        with patch.object(abacusai, "MCP_PATH", mcp_path):
            assert abacusai.check_mcp_installed() is False
            abacusai.install_mcp()
            assert abacusai.check_mcp_installed() is True
