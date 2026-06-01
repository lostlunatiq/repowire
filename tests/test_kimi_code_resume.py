import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from repowire.session.history import (
    _kimi_resumable,
    runtime_session_validation_status,
)


def test_kimi_resumable_found() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        kimi_dir = tmp_path / ".kimi-code"
        kimi_dir.mkdir()
        sessions_dir = kimi_dir / "sessions" / "wd_test_abc123"
        sessions_dir.mkdir(parents=True)
        session_dir = sessions_dir / "session_abc123"
        session_dir.mkdir()

        index_path = kimi_dir / "session_index.jsonl"
        with open(index_path, "w") as f:
            f.write(
                json.dumps(
                    {
                        "sessionId": "session_abc123",
                        "sessionDir": str(session_dir),
                        "workDir": "/tmp/test-project",
                    }
                )
                + "\n"
            )

        with patch("repowire.session.history.Path.home", return_value=tmp_path):
            result = _kimi_resumable("/tmp/test-project", "session_abc123")
            assert result is True


def test_kimi_resumable_not_found() -> None:
    with patch("repowire.session.history.Path.home", return_value=Path("/nonexistent")):
        result = _kimi_resumable("/tmp/nonexistent", "session_nope")
        assert result is False


def test_runtime_session_validation_status_kimi() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        kimi_dir = tmp_path / ".kimi-code"
        kimi_dir.mkdir()
        sessions_dir = kimi_dir / "sessions" / "wd_test_abc123"
        sessions_dir.mkdir(parents=True)
        session_dir = sessions_dir / "session_abc123"
        session_dir.mkdir()

        index_path = kimi_dir / "session_index.jsonl"
        with open(index_path, "w") as f:
            f.write(
                json.dumps(
                    {
                        "sessionId": "session_abc123",
                        "sessionDir": str(session_dir),
                        "workDir": "/tmp/test-project",
                    }
                )
                + "\n"
            )

        with patch("repowire.session.history.Path.home", return_value=tmp_path):
            status = runtime_session_validation_status(
                "/tmp/test-project", "kimi-code", "session_abc123"
            )
            assert status == "resumable"

            status_missing = runtime_session_validation_status(
                "/tmp/test-project", "kimi-code", "session_nope"
            )
            assert status_missing == "stale_missing_file"
