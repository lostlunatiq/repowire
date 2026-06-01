import os
from unittest.mock import patch

from repowire.agent_backends import (
    AGENT_BACKENDS,
    KimiCodeBackend,
    build_resume_command,
    detect_mcp_backend,
)
from repowire.agent_types import AgentType


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


def test_detect_mcp_backend_kimi_marker() -> None:
    """Kimi is detected when ~/.kimi-code exists and kimi binary is in PATH."""
    with patch.dict(os.environ, {"PATH": "/Users/test/.kimi-code/bin:/usr/bin"}, clear=True):
        with patch("shutil.which", return_value="/Users/test/.kimi-code/bin/kimi"):
            with patch.object(
                KimiCodeBackend, "mcp_runtime_matches", return_value=True
            ):
                result = detect_mcp_backend(os.environ)
                assert result == AgentType.KIMI_CODE


def test_build_resume_command_kimi() -> None:
    backend = KimiCodeBackend()
    command = backend.build_resume_command("kimi -C --yolo --auto", "session_abc123")
    assert command == "kimi -C --yolo --auto -S session_abc123"
