"""Tests for shutdown_manager.py — shutdown signal management."""

import importlib.util
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Import handler via importlib (skills are not in the Python package path)
# ---------------------------------------------------------------------------

HANDLER_PATH = Path(__file__).parent.parent / "skills" / "core" / "shutdown" / "handler.py"


def _load_handler():
    spec = importlib.util.spec_from_file_location("shutdown_handler", str(HANDLER_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestRequestShutdown:
    """Test request_shutdown function."""

    def test_creates_shutdown_file(self, tmp_path):
        from app.shutdown_manager import request_shutdown

        request_shutdown(str(tmp_path))
        assert (tmp_path / ".koan-shutdown").exists()

    def test_file_contains_timestamp(self, tmp_path):
        from app.shutdown_manager import request_shutdown

        before = int(time.time())
        request_shutdown(str(tmp_path))
        after = int(time.time())

        content = (tmp_path / ".koan-shutdown").read_text().strip()
        ts = int(content)
        assert before <= ts <= after

    def test_overwrites_existing_file(self, tmp_path):
        from app.shutdown_manager import request_shutdown

        (tmp_path / ".koan-shutdown").write_text("999")
        request_shutdown(str(tmp_path))

        content = (tmp_path / ".koan-shutdown").read_text().strip()
        ts = int(content)
        assert ts != 999


class TestIsShutdownRequested:
    """Test is_shutdown_requested function."""

    def test_no_file_returns_false(self, tmp_path):
        from app.shutdown_manager import is_shutdown_requested

        assert is_shutdown_requested(str(tmp_path), 1000) is False

    def test_shutdown_after_start_returns_true(self, tmp_path):
        from app.shutdown_manager import is_shutdown_requested

        start_time = 1000
        (tmp_path / ".koan-shutdown").write_text("2000")
        assert is_shutdown_requested(str(tmp_path), start_time) is True

    def test_shutdown_at_start_returns_true(self, tmp_path):
        from app.shutdown_manager import is_shutdown_requested

        start_time = 1000
        (tmp_path / ".koan-shutdown").write_text("1000")
        assert is_shutdown_requested(str(tmp_path), start_time) is True

    def test_stale_shutdown_returns_false(self, tmp_path):
        from app.shutdown_manager import is_shutdown_requested

        start_time = 2000
        (tmp_path / ".koan-shutdown").write_text("1000")
        assert is_shutdown_requested(str(tmp_path), start_time) is False

    def test_stale_shutdown_cleans_up_file(self, tmp_path):
        from app.shutdown_manager import is_shutdown_requested

        start_time = 2000
        (tmp_path / ".koan-shutdown").write_text("1000")
        is_shutdown_requested(str(tmp_path), start_time)
        assert not (tmp_path / ".koan-shutdown").exists()

    def test_invalid_content_returns_false(self, tmp_path):
        from app.shutdown_manager import is_shutdown_requested

        (tmp_path / ".koan-shutdown").write_text("not-a-number")
        assert is_shutdown_requested(str(tmp_path), 1000) is False

    def test_empty_file_returns_false(self, tmp_path):
        from app.shutdown_manager import is_shutdown_requested

        (tmp_path / ".koan-shutdown").write_text("")
        assert is_shutdown_requested(str(tmp_path), 1000) is False


class TestClearShutdown:
    """Test clear_shutdown function."""

    def test_removes_file(self, tmp_path):
        from app.shutdown_manager import clear_shutdown

        (tmp_path / ".koan-shutdown").write_text("12345")
        clear_shutdown(str(tmp_path))
        assert not (tmp_path / ".koan-shutdown").exists()

    def test_noop_when_no_file(self, tmp_path):
        from app.shutdown_manager import clear_shutdown

        # Should not raise
        clear_shutdown(str(tmp_path))


class TestShutdownSkillHandler:
    """Test the /shutdown skill handler."""

    @pytest.fixture
    def handler(self):
        return _load_handler()

    def test_handler_creates_shutdown_file(self, tmp_path, handler):
        class FakeCtx:
            koan_root = tmp_path
            instance_dir = tmp_path / "instance"
            command_name = "shutdown"
            args = ""
            send_message = lambda self, msg: None
            handle_chat = None

        result = handler.handle(FakeCtx())
        assert (tmp_path / ".koan-shutdown").exists()
        assert result is not None
        assert "Shutdown" in result

    def test_handler_returns_user_facing_message(self, tmp_path, handler):
        class FakeCtx:
            koan_root = tmp_path
            instance_dir = tmp_path / "instance"
            command_name = "shutdown"
            args = ""
            send_message = lambda self, msg: None
            handle_chat = None

        result = handler.handle(FakeCtx())
        assert isinstance(result, str)
        assert len(result) > 0


class TestIntegration:
    """Test the full shutdown lifecycle."""

    def test_request_then_check(self, tmp_path):
        from app.shutdown_manager import request_shutdown, is_shutdown_requested

        start_time = int(time.time()) - 1  # Started 1s ago
        request_shutdown(str(tmp_path))
        assert is_shutdown_requested(str(tmp_path), start_time) is True

    def test_stale_request_ignored_on_restart(self, tmp_path):
        from app.shutdown_manager import request_shutdown, is_shutdown_requested

        # Simulate: shutdown was requested
        request_shutdown(str(tmp_path))

        # Simulate: process restarts (start_time is now)
        time.sleep(0.01)  # Ensure start_time > shutdown_time
        new_start_time = int(time.time()) + 1  # Future start time
        assert is_shutdown_requested(str(tmp_path), new_start_time) is False

    def test_request_clear_request_again(self, tmp_path):
        from app.shutdown_manager import (
            request_shutdown,
            is_shutdown_requested,
            clear_shutdown,
        )

        start_time = int(time.time()) - 1
        request_shutdown(str(tmp_path))
        assert is_shutdown_requested(str(tmp_path), start_time) is True

        clear_shutdown(str(tmp_path))
        assert is_shutdown_requested(str(tmp_path), start_time) is False

        # New shutdown after clearing
        request_shutdown(str(tmp_path))
        assert is_shutdown_requested(str(tmp_path), start_time) is True
