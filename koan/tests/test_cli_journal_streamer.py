"""Tests for cli_journal_streamer — tail thread, stderr append, lifecycle."""

import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def tmp_env(tmp_path):
    """Set up a temp instance directory with journal structure."""
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    (instance_dir / "journal").mkdir()
    return {
        "instance_dir": str(instance_dir),
        "project_name": "test-project",
    }


@pytest.fixture
def stdout_file(tmp_path):
    """Create a temp file for simulated subprocess stdout."""
    path = tmp_path / "stdout.txt"
    path.write_text("")
    return str(path)


@pytest.fixture
def stderr_file(tmp_path):
    """Create a temp file for simulated subprocess stderr."""
    path = tmp_path / "stderr.txt"
    path.write_text("")
    return str(path)


def _journal_content(tmp_env):
    """Read today's journal file if it exists, else return empty string."""
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    path = Path(tmp_env["instance_dir"]) / "journal" / today / "test-project.md"
    return path.read_text() if path.exists() else ""


class TestStartTailThread:
    """Test start_tail_thread / stop_tail_thread lifecycle."""

    def test_streams_new_content_to_journal(self, tmp_env, stdout_file):
        from app.cli_journal_streamer import start_tail_thread, stop_tail_thread

        thread, stop_event = start_tail_thread(
            stdout_file, tmp_env["instance_dir"], tmp_env["project_name"], run_num=1,
        )
        assert thread.is_alive()

        with open(stdout_file, "a") as f:
            f.write("line one\n")
            f.flush()

        time.sleep(2.0)
        stop_tail_thread(thread, stop_event)
        assert not thread.is_alive()

        content = _journal_content(tmp_env)
        assert "CLI Output" in content
        assert "line one" in content

    def test_writes_header_immediately(self, tmp_env, stdout_file):
        from app.cli_journal_streamer import start_tail_thread, stop_tail_thread

        thread, stop_event = start_tail_thread(
            stdout_file, tmp_env["instance_dir"], tmp_env["project_name"], run_num=3,
        )

        time.sleep(0.5)
        content = _journal_content(tmp_env)
        assert "Run 3" in content
        assert "🖥️" in content

        stop_tail_thread(thread, stop_event)

    def test_final_flush_on_stop(self, tmp_env, stdout_file):
        from app.cli_journal_streamer import start_tail_thread, stop_tail_thread

        thread, stop_event = start_tail_thread(
            stdout_file, tmp_env["instance_dir"], tmp_env["project_name"], run_num=1,
        )

        with open(stdout_file, "a") as f:
            f.write("final line\n")
            f.flush()

        stop_tail_thread(thread, stop_event)
        assert "final line" in _journal_content(tmp_env)

    def test_handles_missing_stdout_file(self, tmp_env, tmp_path):
        from app.cli_journal_streamer import start_tail_thread, stop_tail_thread

        nonexistent = str(tmp_path / "does-not-exist.txt")
        thread, stop_event = start_tail_thread(
            nonexistent, tmp_env["instance_dir"], tmp_env["project_name"], run_num=1,
        )
        time.sleep(1.5)
        stop_tail_thread(thread, stop_event)
        assert not thread.is_alive()


class TestAppendStderrToJournal:
    """Test append_stderr_to_journal."""

    def test_appends_stderr_on_error(self, tmp_env, stderr_file):
        from app.cli_journal_streamer import append_stderr_to_journal

        Path(stderr_file).write_text("Error: something went wrong\n")
        append_stderr_to_journal(
            stderr_file, tmp_env["instance_dir"], tmp_env["project_name"], run_num=2,
        )

        content = _journal_content(tmp_env)
        assert "CLI Errors" in content
        assert "Run 2" in content
        assert "something went wrong" in content

    def test_skips_empty_stderr(self, tmp_env, stderr_file):
        from app.cli_journal_streamer import append_stderr_to_journal

        Path(stderr_file).write_text("")
        append_stderr_to_journal(
            stderr_file, tmp_env["instance_dir"], tmp_env["project_name"], run_num=1,
        )
        assert _journal_content(tmp_env) == ""

    def test_skips_missing_stderr_file(self, tmp_env, tmp_path):
        from app.cli_journal_streamer import append_stderr_to_journal

        nonexistent = str(tmp_path / "no-such-file.txt")
        append_stderr_to_journal(
            nonexistent, tmp_env["instance_dir"], tmp_env["project_name"], run_num=1,
        )


class TestJournalStreamLifecycle:
    """Test start_journal_stream / stop_journal_stream high-level helpers."""

    def test_start_returns_handle_when_enabled(self, tmp_env, stdout_file):
        from app.cli_journal_streamer import start_journal_stream, stop_journal_stream

        with patch("app.config._load_config", return_value={"cli_output_journal": True}):
            handle = start_journal_stream(
                stdout_file, tmp_env["instance_dir"], tmp_env["project_name"], run_num=1,
            )
        assert handle is not None

        stop_journal_stream(handle, 0, stdout_file, tmp_env["instance_dir"], tmp_env["project_name"], 1)
        assert "CLI Output" in _journal_content(tmp_env)

    def test_start_returns_none_when_disabled(self, tmp_env, stdout_file):
        from app.cli_journal_streamer import start_journal_stream

        with patch("app.config._load_config", return_value={"cli_output_journal": False}):
            handle = start_journal_stream(
                stdout_file, tmp_env["instance_dir"], tmp_env["project_name"], run_num=1,
            )
        assert handle is None

    def test_stop_is_noop_with_none_handle(self, tmp_env, stderr_file):
        from app.cli_journal_streamer import stop_journal_stream

        # Should not raise
        stop_journal_stream(None, 1, stderr_file, tmp_env["instance_dir"], tmp_env["project_name"], 1)

    def test_stop_appends_stderr_on_nonzero_exit(self, tmp_env, stdout_file, stderr_file):
        from app.cli_journal_streamer import start_journal_stream, stop_journal_stream

        Path(stderr_file).write_text("fatal error\n")

        with patch("app.config._load_config", return_value={"cli_output_journal": True}):
            handle = start_journal_stream(
                stdout_file, tmp_env["instance_dir"], tmp_env["project_name"], run_num=5,
            )

        stop_journal_stream(handle, 1, stderr_file, tmp_env["instance_dir"], tmp_env["project_name"], 5)

        content = _journal_content(tmp_env)
        assert "CLI Errors" in content
        assert "fatal error" in content


class TestConfigOption:
    """Test get_cli_output_journal config function."""

    def test_default_is_true(self):
        from app.config import get_cli_output_journal
        with patch("app.config._load_config", return_value={}):
            assert get_cli_output_journal() is True

    def test_explicit_true(self):
        from app.config import get_cli_output_journal
        with patch("app.config._load_config", return_value={"cli_output_journal": True}):
            assert get_cli_output_journal() is True

    def test_explicit_false(self):
        from app.config import get_cli_output_journal
        with patch("app.config._load_config", return_value={"cli_output_journal": False}):
            assert get_cli_output_journal() is False
