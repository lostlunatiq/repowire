import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from repowire.session.history import (
    _kimi_resumable,
    runtime_session_validation_status,
)


def _make_kimi_session(base: Path, session_id: str, work_dir: str) -> Path:
    """Create a mock Kimi session dir with state.json."""
    sessions_dir = base / "sessions" / f"wd_{session_id}"
    sessions_dir.mkdir(parents=True)
    session_dir = sessions_dir / session_id
    session_dir.mkdir()
    (session_dir / "state.json").write_text("{}")
    return session_dir


def test_kimi_resumable_found() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        kimi_dir = tmp_path / ".kimi-code"
        kimi_dir.mkdir()
        session_dir = _make_kimi_session(kimi_dir, "session_abc123", "/tmp/test-project")

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


def test_kimi_resumable_wrong_peer_path() -> None:
    """Same sessionId but different workDir should be rejected."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        kimi_dir = tmp_path / ".kimi-code"
        kimi_dir.mkdir()
        session_dir = _make_kimi_session(kimi_dir, "session_abc123", "/tmp/test-project")

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
            result = _kimi_resumable("/tmp/other-project", "session_abc123")
            assert result is False


def test_kimi_resumable_not_found() -> None:
    with patch("repowire.session.history.Path.home", return_value=Path("/nonexistent")):
        result = _kimi_resumable("/tmp/nonexistent", "session_nope")
        assert result is False


def test_kimi_resumable_malformed_json() -> None:
    """Malformed lines should be skipped, valid line should still match."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        kimi_dir = tmp_path / ".kimi-code"
        kimi_dir.mkdir()
        session_dir = _make_kimi_session(kimi_dir, "session_abc123", "/tmp/test-project")

        index_path = kimi_dir / "session_index.jsonl"
        with open(index_path, "w") as f:
            f.write("this is not json\n")
            f.write('{"sessionId": "session_abc123"\n')  # incomplete JSON
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


def test_kimi_resumable_none_session_dir() -> None:
    """Entry with null sessionDir should not crash."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        kimi_dir = tmp_path / ".kimi-code"
        kimi_dir.mkdir()

        index_path = kimi_dir / "session_index.jsonl"
        with open(index_path, "w") as f:
            f.write(
                json.dumps(
                    {
                        "sessionId": "session_abc123",
                        "sessionDir": None,
                        "workDir": "/tmp/test-project",
                    }
                )
                + "\n"
            )

        with patch("repowire.session.history.Path.home", return_value=tmp_path):
            result = _kimi_resumable("/tmp/test-project", "session_abc123")
            assert result is False


def test_runtime_session_validation_status_kimi() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        kimi_dir = tmp_path / ".kimi-code"
        kimi_dir.mkdir()
        session_dir = _make_kimi_session(kimi_dir, "session_abc123", "/tmp/test-project")

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
