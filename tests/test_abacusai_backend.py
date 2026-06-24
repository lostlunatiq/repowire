from unittest.mock import patch

from repowire.agent_backends import (
    AGENT_BACKENDS,
    AbacusAIBackend,
    detect_mcp_backend,
)
from repowire.config.models import AgentType


def test_abacusai_backend_registered() -> None:
    backend = AGENT_BACKENDS.get(AgentType.ABACUSAI)
    assert backend is not None
    assert isinstance(backend, AbacusAIBackend)
    assert backend.agent_type == AgentType.ABACUSAI
    assert backend.display_name == "Abacus AI"
    assert backend.cli_names == ("abacusai",)
    assert backend.supports_resume is True
    assert backend.resume_flag == "--resume"
    assert "abacusai" in backend.default_command
    assert backend.mcp_config_scope is not None


def test_abacusai_backend_install_calls_installer() -> None:
    backend = AbacusAIBackend()
    with patch("repowire.installers.abacusai.install_mcp") as mock_mcp:
        messages = backend.install()
    mock_mcp.assert_called_once()
    assert any("MCP server configured" in m.text for m in messages)


def test_detect_mcp_backend_explicit_env() -> None:
    env = {"REPOWIRE_BACKEND": "abacusai"}
    assert detect_mcp_backend(env) == AgentType.ABACUSAI


def test_abacusai_mcp_runtime_matches_explicit() -> None:
    env = {"REPOWIRE_BACKEND": "abacusai"}
    assert AbacusAIBackend.mcp_runtime_matches(env) is True


def test_abacusai_mcp_runtime_matches_session_id() -> None:
    env = {"ABACUSAI_SESSION_ID": "session_abc123"}
    assert AbacusAIBackend.mcp_runtime_matches(env) is True


def test_abacusai_mcp_runtime_matches_no_marker() -> None:
    env = {"PATH": "/usr/bin", "HOME": "/tmp"}
    assert AbacusAIBackend.mcp_runtime_matches(env) is False


def test_build_resume_command_abacusai() -> None:
    backend = AbacusAIBackend()
    command = backend.build_resume_command("abacusai --permission-mode yolo", "session_abc123")
    assert command == "abacusai --permission-mode yolo --resume session_abc123"
