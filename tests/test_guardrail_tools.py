from unittest.mock import AsyncMock, patch

import pytest

from repowire.mcp.server import create_mcp_server


def _tool(name: str):
    return create_mcp_server()._tool_manager._tools[name].fn


@pytest.mark.asyncio
async def test_lint_code_ruff_found() -> None:
    with (
        patch("repowire.mcp.server._ensure_registered", new_callable=AsyncMock),
        patch("shutil.which", return_value="/usr/bin/ruff"),
        patch("subprocess.run") as run,
    ):
        run.return_value.returncode = 0
        run.return_value.stdout = '[]'
        run.return_value.stderr = ''

        result = await _tool("lint_code")(files=["src/foo.py"], linter="ruff")

    assert result["passed"] is True
    assert result["exit_code"] == 0
    assert result["violations"] == []


@pytest.mark.asyncio
async def test_lint_code_linter_missing() -> None:
    with (
        patch("repowire.mcp.server._ensure_registered", new_callable=AsyncMock),
        patch("shutil.which", return_value=None),
    ):
        result = await _tool("lint_code")(files=["src/foo.py"], linter="ruff")

    assert result["passed"] is False
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_run_tests_pytest_found() -> None:
    with (
        patch("repowire.mcp.server._ensure_registered", new_callable=AsyncMock),
        patch("shutil.which", return_value="/usr/bin/pytest"),
        patch("subprocess.run") as run,
    ):
        run.return_value.returncode = 0
        run.return_value.stdout = '1 passed'
        run.return_value.stderr = ''

        result = await _tool("run_tests")(test_path="tests/", runner="pytest")

    assert result["passed"] is True
    assert result["exit_code"] == 0


@pytest.mark.asyncio
async def test_type_check_mypy_found() -> None:
    with (
        patch("repowire.mcp.server._ensure_registered", new_callable=AsyncMock),
        patch("shutil.which", return_value="/usr/bin/mypy"),
        patch("subprocess.run") as run,
    ):
        run.return_value.returncode = 0
        run.return_value.stdout = 'Success'
        run.return_value.stderr = ''

        result = await _tool("type_check")(files=["src/foo.py"], checker="mypy")

    assert result["passed"] is True
    assert result["exit_code"] == 0


@pytest.mark.asyncio
async def test_static_analyze_bandit_found() -> None:
    with (
        patch("repowire.mcp.server._ensure_registered", new_callable=AsyncMock),
        patch("shutil.which", return_value="/usr/bin/bandit"),
        patch("subprocess.run") as run,
    ):
        run.return_value.returncode = 0
        run.return_value.stdout = '{"results": []}'
        run.return_value.stderr = ''

        result = await _tool("static_analyze")(files=["src/foo.py"], analyzer="bandit")

    assert result["passed"] is True
    assert result["exit_code"] == 0


@pytest.mark.asyncio
async def test_get_budget_status() -> None:
    with (
        patch("repowire.mcp.server._ensure_registered", new_callable=AsyncMock),
        patch("repowire.mcp.server._get_my_peer_identifier", new_callable=AsyncMock) as peer,
        patch("repowire.mcp.server.daemon_request", new_callable=AsyncMock) as req,
    ):
        peer.return_value = "my-peer"
        req.return_value = {
            "budget_id": "my-peer",
            "used": 15000,
            "remaining": 85000,
            "ceiling": 100000,
            "warning_sent": False,
        }

        result = await _tool("get_budget_status")()

    assert result["used"] == 15000
    assert result["remaining"] == 85000
