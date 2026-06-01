
from repowire.agent_backends import (
    AGENT_BACKENDS,
    KimiCodeBackend,
    detect_mcp_backend,
)
from repowire.config.models import AgentType


def test_kimi_code_backend_registered() -> None:
    backend = AGENT_BACKENDS.get(AgentType.KIMI_CODE)
    assert backend is not None
    assert isinstance(backend, KimiCodeBackend)
    assert backend.agent_type == AgentType.KIMI_CODE
    assert backend.display_name == "Kimi Code"
    assert backend.cli_names == ("kimi",)
    assert backend.supports_resume is True
    assert backend.resume_flag == "-S"
    assert backend.default_command == "kimi -C --yolo --auto"


def test_detect_mcp_backend_explicit_env() -> None:
    env = {"REPOWIRE_BACKEND": "kimi-code"}
    assert detect_mcp_backend(env) == AgentType.KIMI_CODE


def test_kimi_mcp_runtime_matches_explicit() -> None:
    """Explicit REPOWIRE_BACKEND=kimi-code is authoritative."""
    env = {"REPOWIRE_BACKEND": "kimi-code"}
    assert KimiCodeBackend.mcp_runtime_matches(env) is True


def test_kimi_mcp_runtime_matches_session_id() -> None:
    """Forward-looking KIMI_CODE_SESSION_ID marker."""
    env = {"KIMI_CODE_SESSION_ID": "session_abc123"}
    assert KimiCodeBackend.mcp_runtime_matches(env) is True


def test_kimi_mcp_runtime_matches_no_marker() -> None:
    """Without explicit env or Kimi-specific marker, should not match."""
    env = {"PATH": "/usr/bin", "HOME": "/tmp"}
    assert KimiCodeBackend.mcp_runtime_matches(env) is False


def test_build_resume_command_kimi() -> None:
    backend = KimiCodeBackend()
    command = backend.build_resume_command("kimi -C --yolo --auto", "session_abc123")
    assert command == "kimi -C --yolo --auto -S session_abc123"
