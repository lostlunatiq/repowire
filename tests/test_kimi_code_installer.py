import json
from pathlib import Path
from unittest.mock import patch

from repowire.installers import kimi_code


def test_install_hooks_creates_config(tmp_path: Path) -> None:
    home = tmp_path
    config_path = home / ".kimi-code" / "config.toml"
    with patch.object(kimi_code, "KIMI_HOME", home / ".kimi-code"):
        with patch.object(kimi_code, "CONFIG_PATH", config_path):
            assert kimi_code.install_hooks() is True
    assert config_path.exists()
    text = config_path.read_text()
    assert 'event = "SessionStart"' in text
    assert 'event = "UserPromptSubmit"' in text
    assert 'event = "Stop"' in text
    assert "repowire hook session --backend=kimi-code" in text
    assert "repowire hook prompt --backend=kimi-code" in text
    assert "repowire hook stop --backend=kimi-code" in text


def test_install_hooks_preserves_existing_config(tmp_path: Path) -> None:
    home = tmp_path
    config_path = home / ".kimi-code" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("default_model = \"kimi-code/kimi-for-coding\"\n")
    with patch.object(kimi_code, "KIMI_HOME", home / ".kimi-code"):
        with patch.object(kimi_code, "CONFIG_PATH", config_path):
            kimi_code.install_hooks()
    text = config_path.read_text()
    assert 'default_model = "kimi-code/kimi-for-coding"' in text
    assert 'event = "SessionStart"' in text


def test_install_mcp_creates_mcp_json(tmp_path: Path) -> None:
    home = tmp_path
    mcp_path = home / ".kimi-code" / "mcp.json"
    with patch.object(kimi_code, "KIMI_HOME", home / ".kimi-code"):
        with patch.object(kimi_code, "MCP_PATH", mcp_path):
            assert kimi_code.install_mcp() is True
    assert mcp_path.exists()
    data = json.loads(mcp_path.read_text())
    assert "repowire" in data["mcpServers"]
    assert data["mcpServers"]["repowire"]["command"] == "repowire"
    assert data["mcpServers"]["repowire"]["args"] == ["mcp"]
    assert data["mcpServers"]["repowire"]["env"]["REPOWIRE_BACKEND"] == "kimi-code"


def test_uninstall_hooks_removes_repowire_entries(tmp_path: Path) -> None:
    home = tmp_path
    config_path = home / ".kimi-code" / "config.toml"
    with patch.object(kimi_code, "KIMI_HOME", home / ".kimi-code"):
        with patch.object(kimi_code, "CONFIG_PATH", config_path):
            kimi_code.install_hooks()
            assert kimi_code.uninstall_hooks() is True
    text = config_path.read_text()
    assert "repowire" not in text


def test_uninstall_hooks_preserves_user_hooks(tmp_path: Path) -> None:
    home = tmp_path
    config_path = home / ".kimi-code" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        '[[hooks]]\nevent = "Stop"\ncommand = "echo user-hook"\ntimeout = 5\n'
    )
    with patch.object(kimi_code, "KIMI_HOME", home / ".kimi-code"):
        with patch.object(kimi_code, "CONFIG_PATH", config_path):
            kimi_code.install_hooks()
            kimi_code.uninstall_hooks()
    text = config_path.read_text()
    assert "echo user-hook" in text
    assert "repowire" not in text


def test_uninstall_mcp_removes_repowire(tmp_path: Path) -> None:
    home = tmp_path
    mcp_path = home / ".kimi-code" / "mcp.json"
    with patch.object(kimi_code, "KIMI_HOME", home / ".kimi-code"):
        with patch.object(kimi_code, "MCP_PATH", mcp_path):
            kimi_code.install_mcp()
            assert kimi_code.uninstall_mcp() is True
    data = json.loads(mcp_path.read_text())
    assert "repowire" not in data.get("mcpServers", {})


def test_check_hooks_installed(tmp_path: Path) -> None:
    home = tmp_path
    config_path = home / ".kimi-code" / "config.toml"
    with patch.object(kimi_code, "KIMI_HOME", home / ".kimi-code"):
        with patch.object(kimi_code, "CONFIG_PATH", config_path):
            assert kimi_code.check_hooks_installed() is False
            kimi_code.install_hooks()
            assert kimi_code.check_hooks_installed() is True


def test_check_mcp_installed(tmp_path: Path) -> None:
    home = tmp_path
    mcp_path = home / ".kimi-code" / "mcp.json"
    with patch.object(kimi_code, "KIMI_HOME", home / ".kimi-code"):
        with patch.object(kimi_code, "MCP_PATH", mcp_path):
            assert kimi_code.check_mcp_installed() is False
            kimi_code.install_mcp()
            assert kimi_code.check_mcp_installed() is True
