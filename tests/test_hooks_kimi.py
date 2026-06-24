import io
import json
import sys
from unittest.mock import patch

from repowire.hooks.adapters import hook_output
from repowire.hooks.prompt_handler import main as prompt_main


def test_hook_output_kimi_code() -> None:
    """Kimi Code backend string is 'kimi-code'; it needs explicit allow output."""
    captured = io.StringIO()
    sys.stdout = captured
    hook_output("kimi-code")
    sys.stdout = sys.__stdout__
    assert captured.getvalue().strip() == '{"decision": "allow"}'


def test_prompt_handler_budget_blocks_kimi_code() -> None:
    """Budget exhaustion should block UserPromptSubmit for kimi-code."""
    input_data = json.dumps({
        "hook_event_name": "UserPromptSubmit",
        "session_id": "session_abc",
        "cwd": "/tmp/test",
        "prompt": "hello",
    })
    with patch("repowire.hooks.prompt_handler.sys.stdin", io.StringIO(input_data)):
        with patch("repowire.hooks.utils.daemon_get") as mock_get:
            mock_get.return_value = {"remaining": 0, "used": 100000, "ceiling": 100000}
            with patch("repowire.hooks.prompt_handler.update_status", return_value=True):
                captured = io.StringIO()
                with patch("repowire.hooks.prompt_handler.sys.stdout", captured):
                    result = prompt_main(backend="kimi-code")
    assert result == 0
    output = captured.getvalue()
    assert "deny" in output
    assert "Token budget exhausted" in output
