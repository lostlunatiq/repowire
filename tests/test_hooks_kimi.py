import io
import sys

from repowire.hooks.adapters import hook_output


def test_hook_output_kimi() -> None:
    """Kimi uses the plugin system like Gemini; it needs explicit allow output."""
    captured = io.StringIO()
    sys.stdout = captured
    hook_output("kimi")
    sys.stdout = sys.__stdout__
    assert captured.getvalue().strip() == '{"decision": "allow"}'
