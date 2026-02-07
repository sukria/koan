"""Tests for restart_manager.py — file-based restart signaling."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from app.restart_manager import (
    RESTART_FILE,
    RESTART_EXIT_CODE,
    request_restart,
    check_restart,
    clear_restart,
    reexec_bridge,
)


class TestRequestRestart:
    """Tests for request_restart()."""

    def test_creates_restart_file(self, tmp_path):
        request_restart(tmp_path)
        assert (tmp_path / RESTART_FILE).exists()

    def test_restart_file_contains_timestamp(self, tmp_path):
        request_restart(tmp_path)
        content = (tmp_path / RESTART_FILE).read_text()
        assert "restart requested at" in content

    def test_overwrites_existing_file(self, tmp_path):
        (tmp_path / RESTART_FILE).write_text("old")
        request_restart(tmp_path)
        content = (tmp_path / RESTART_FILE).read_text()
        assert "restart requested at" in content


class TestCheckRestart:
    """Tests for check_restart()."""

    def test_returns_false_when_no_file(self, tmp_path):
        assert check_restart(tmp_path) is False

    def test_returns_true_when_file_exists(self, tmp_path):
        (tmp_path / RESTART_FILE).write_text("restart")
        assert check_restart(tmp_path) is True


class TestClearRestart:
    """Tests for clear_restart()."""

    def test_removes_restart_file(self, tmp_path):
        (tmp_path / RESTART_FILE).write_text("restart")
        clear_restart(tmp_path)
        assert not (tmp_path / RESTART_FILE).exists()

    def test_noop_when_no_file(self, tmp_path):
        # Should not raise
        clear_restart(tmp_path)
        assert not (tmp_path / RESTART_FILE).exists()


class TestReexecBridge:
    """Tests for reexec_bridge()."""

    @patch("app.restart_manager.os.execv")
    def test_calls_execv_with_python(self, mock_execv):
        reexec_bridge()
        mock_execv.assert_called_once()
        args = mock_execv.call_args[0]
        assert args[0] == sys.executable
        assert args[1][0] == sys.executable

    @patch("app.restart_manager.os.execv")
    def test_passes_sys_argv(self, mock_execv):
        with patch("app.restart_manager.sys.argv", ["bridge.py", "--flag"]):
            reexec_bridge()
        args = mock_execv.call_args[0]
        assert args[1] == [sys.executable, "bridge.py", "--flag"]


class TestConstants:
    """Tests for module constants."""

    def test_restart_file_name(self):
        assert RESTART_FILE == ".koan-restart"

    def test_restart_exit_code(self):
        assert RESTART_EXIT_CODE == 42


class TestFullCycle:
    """Integration tests for the request → check → clear cycle."""

    def test_full_lifecycle(self, tmp_path):
        # Initially no restart
        assert check_restart(tmp_path) is False

        # Request restart
        request_restart(tmp_path)
        assert check_restart(tmp_path) is True

        # Clear restart
        clear_restart(tmp_path)
        assert check_restart(tmp_path) is False

    def test_double_request_is_idempotent(self, tmp_path):
        request_restart(tmp_path)
        request_restart(tmp_path)
        assert check_restart(tmp_path) is True
        clear_restart(tmp_path)
        assert check_restart(tmp_path) is False
