"""Tests for the /restart command and restart_manager module."""

import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from app.restart_manager import (
    request_restart,
    check_restart,
    clear_restart,
    reexec_bridge,
    RESTART_FILE,
    RESTART_EXIT_CODE,
)


# ---------------------------------------------------------------------------
# restart_manager.py tests
# ---------------------------------------------------------------------------


class TestRequestRestart:
    def test_creates_restart_file(self, tmp_path):
        request_restart(tmp_path)
        assert (tmp_path / RESTART_FILE).exists()

    def test_restart_file_contains_timestamp(self, tmp_path):
        request_restart(tmp_path)
        content = (tmp_path / RESTART_FILE).read_text()
        assert "restart requested at" in content

    def test_overwrites_existing_file(self, tmp_path):
        restart_file = tmp_path / RESTART_FILE
        restart_file.write_text("old content")
        request_restart(tmp_path)
        content = restart_file.read_text()
        assert "restart requested at" in content
        assert "old content" not in content


class TestCheckRestart:
    def test_returns_true_when_file_exists(self, tmp_path):
        (tmp_path / RESTART_FILE).write_text("restart")
        assert check_restart(tmp_path) is True

    def test_returns_false_when_no_file(self, tmp_path):
        assert check_restart(tmp_path) is False

    def test_since_ignores_old_file(self, tmp_path):
        """File touched before `since` is treated as stale."""
        (tmp_path / RESTART_FILE).write_text("restart")
        # Set mtime in the past
        past = time.time() - 10
        os.utime(tmp_path / RESTART_FILE, (past, past))
        assert check_restart(tmp_path, since=time.time()) is False

    def test_since_detects_fresh_file(self, tmp_path):
        """File touched after `since` is detected."""
        since = time.time() - 10
        (tmp_path / RESTART_FILE).write_text("restart")
        assert check_restart(tmp_path, since=since) is True

    def test_since_zero_means_no_filter(self, tmp_path):
        """Default since=0 behaves like the old check (any file triggers)."""
        (tmp_path / RESTART_FILE).write_text("restart")
        # Set mtime far in the past
        past = time.time() - 1000
        os.utime(tmp_path / RESTART_FILE, (past, past))
        assert check_restart(tmp_path, since=0) is True


class TestClearRestart:
    def test_removes_restart_file(self, tmp_path):
        (tmp_path / RESTART_FILE).write_text("restart")
        clear_restart(tmp_path)
        assert not (tmp_path / RESTART_FILE).exists()

    def test_no_error_when_file_missing(self, tmp_path):
        # Should not raise
        clear_restart(tmp_path)


class TestReexecBridge:
    @patch("os.execv")
    def test_calls_execv_with_python_and_argv(self, mock_execv):
        with patch.object(sys, "argv", ["/path/to/awake.py"]):
            reexec_bridge()
        mock_execv.assert_called_once_with(
            sys.executable, [sys.executable, "/path/to/awake.py"]
        )

    @patch("os.execv")
    def test_preserves_argv_arguments(self, mock_execv):
        with patch.object(sys, "argv", ["/path/to/awake.py", "--debug"]):
            reexec_bridge()
        mock_execv.assert_called_once_with(
            sys.executable, [sys.executable, "/path/to/awake.py", "--debug"]
        )


class TestRestartExitCode:
    def test_exit_code_is_42(self):
        assert RESTART_EXIT_CODE == 42


# ---------------------------------------------------------------------------
# awake.py restart integration tests
# ---------------------------------------------------------------------------


class TestHandleRestartCommand:
    """Test that /restart triggers restart flow, not resume."""

    @patch("app.command_handlers.send_telegram")
    def test_restart_creates_signal_file(self, mock_send, tmp_path):
        with patch("app.command_handlers.KOAN_ROOT", tmp_path):
            from app.command_handlers import handle_restart
            handle_restart()
        assert (tmp_path / RESTART_FILE).exists()
        mock_send.assert_called_once()
        assert "Restart" in mock_send.call_args[0][0]

    @patch("app.command_handlers.send_telegram")
    def test_restart_clears_pause_state(self, mock_send, tmp_path):
        pause_file = tmp_path / ".koan-pause"
        reason_file = tmp_path / ".koan-pause-reason"
        pause_file.write_text("PAUSE")
        reason_file.write_text("manual")

        with patch("app.command_handlers.KOAN_ROOT", tmp_path):
            from app.command_handlers import handle_restart
            handle_restart()

        assert not pause_file.exists()
        assert not reason_file.exists()

    @patch("app.command_handlers.send_telegram")
    def test_restart_dedup_skips_when_file_exists(self, mock_send, tmp_path):
        """Second /restart call is a no-op when file already exists (dedup)."""
        (tmp_path / RESTART_FILE).write_text("already pending")
        with patch("app.command_handlers.KOAN_ROOT", tmp_path):
            from app.command_handlers import handle_restart
            handle_restart()
        # Should not send telegram (dedup — already pending)
        mock_send.assert_not_called()

    @patch("app.command_handlers.send_telegram")
    def test_restart_works_after_stale_file_cleared(self, mock_send, tmp_path):
        """After main() clears the stale file, new /restart is honored."""
        # Simulate: stale file was cleared by main() after first poll
        # (no file present)
        with patch("app.command_handlers.KOAN_ROOT", tmp_path):
            from app.command_handlers import handle_restart
            handle_restart()
        assert (tmp_path / RESTART_FILE).exists()
        mock_send.assert_called_once()
        assert "Restart" in mock_send.call_args[0][0]

    @patch("app.command_handlers.handle_restart")
    def test_command_routes_restart_to_handler(self, mock_restart):
        from app.command_handlers import handle_command
        handle_command("/restart")
        mock_restart.assert_called_once()

    @patch("app.command_handlers.handle_resume")
    def test_restart_does_not_call_resume(self, mock_resume, tmp_path):
        with patch("app.command_handlers.send_telegram"):
            with patch("app.command_handlers.KOAN_ROOT", tmp_path):
                from app.command_handlers import handle_command
                handle_command("/restart")
        mock_resume.assert_not_called()

    @patch("app.command_handlers.handle_resume")
    def test_resume_aliases_still_work(self, mock_resume):
        """Verify /work, /awake, /start still call resume, not restart."""
        from app.command_handlers import handle_command
        for cmd in ["/resume", "/work", "/awake", "/start"]:
            mock_resume.reset_mock()
            handle_command(cmd)
            assert mock_resume.call_count == 1, f"{cmd} should call handle_resume"


class TestRestartLoopPrevention:
    """Verify the dedup mechanism that prevents infinite restart loops."""

    @patch("app.command_handlers.send_telegram")
    def test_stale_file_does_not_trigger_restart(self, mock_send, tmp_path):
        """A .koan-restart file from a previous incarnation is ignored."""
        # Create file in the past (previous incarnation)
        restart_file = tmp_path / RESTART_FILE
        restart_file.write_text("old restart")
        past = time.time() - 10
        os.utime(restart_file, (past, past))

        # New process starts now
        startup_time = time.time()
        assert check_restart(tmp_path, since=startup_time) is False

    @patch("app.command_handlers.send_telegram")
    def test_fresh_file_triggers_restart(self, mock_send, tmp_path):
        """A new .koan-restart file (after startup) triggers restart."""
        startup_time = time.time() - 10

        # File created after startup
        request_restart(tmp_path)
        assert check_restart(tmp_path, since=startup_time) is True

    @patch("app.command_handlers.send_telegram")
    def test_redelivered_restart_is_deduplicated(self, mock_send, tmp_path):
        """Simulates the restart loop scenario:
        1. /restart creates file
        2. Process re-execs (file still exists as dedup guard)
        3. Telegram re-delivers /restart in first poll
        4. handle_restart() sees file exists → no-op
        5. main() clears file after first poll
        6. Future /restart works normally
        """
        # Step 1: First /restart
        with patch("app.command_handlers.KOAN_ROOT", tmp_path):
            from app.command_handlers import handle_restart
            handle_restart()
        assert (tmp_path / RESTART_FILE).exists()
        assert mock_send.call_count == 1

        # Step 3-4: Re-delivered /restart — file still exists → dedup
        mock_send.reset_mock()
        with patch("app.command_handlers.KOAN_ROOT", tmp_path):
            handle_restart()
        mock_send.assert_not_called()

        # Step 5: main() clears the file after first poll
        clear_restart(tmp_path)
        assert not (tmp_path / RESTART_FILE).exists()

        # Step 6: New /restart is now honored
        mock_send.reset_mock()
        with patch("app.command_handlers.KOAN_ROOT", tmp_path):
            handle_restart()
        assert (tmp_path / RESTART_FILE).exists()
        mock_send.assert_called_once()


class TestHelpIncludesRestart:
    @patch("app.command_handlers.send_telegram")
    def test_help_lists_restart_command(self, mock_send):
        from app.command_handlers import _handle_help
        _handle_help()
        help_text = mock_send.call_args[0][0]
        assert "/restart" in help_text
        assert "restart both" in help_text.lower()

    @patch("app.command_handlers.send_telegram")
    def test_help_resume_aliases_exclude_restart(self, mock_send):
        from app.command_handlers import _handle_help
        _handle_help()
        help_text = mock_send.call_args[0][0]
        # Find the /resume line and check /restart is not listed as alias
        for line in help_text.split("\n"):
            if "/resume" in line and "alias" in line:
                assert "/restart" not in line


class TestMainLoopRestartDetection:
    """Test that the main loop restart detection code is present and correct."""

    def test_main_imports_restart_functions(self):
        """Verify main() has the restart check/reexec/clear imports."""
        import inspect
        from app.awake import main
        source = inspect.getsource(main)
        assert "check_restart" in source
        assert "clear_restart" in source
        assert "reexec_bridge" in source

    def test_main_records_startup_time(self):
        """Verify main() records startup_time before the main loop."""
        import inspect
        from app.awake import main
        source = inspect.getsource(main)
        startup_idx = source.index("startup_time")
        while_idx = source.index("while True:")
        assert startup_idx < while_idx, "startup_time should be set before main loop"

    def test_main_uses_since_in_check(self):
        """Verify main() passes since=startup_time to check_restart."""
        import inspect
        from app.awake import main
        source = inspect.getsource(main)
        assert "since=startup_time" in source

    def test_main_checks_restart_in_loop(self):
        """Verify main() checks for restart signal inside the poll loop."""
        import inspect
        from app.awake import main
        source = inspect.getsource(main)
        while_idx = source.index("while True:")
        # check_restart should appear after the while loop starts
        check_idx = source.index("check_restart(KOAN_ROOT", while_idx)
        assert check_idx > while_idx

    def test_main_clears_stale_file_after_first_poll(self):
        """Verify main() clears restart file after the first poll cycle."""
        import inspect
        from app.awake import main
        source = inspect.getsource(main)
        while_idx = source.index("while True:")
        # clear_restart should appear inside the loop (after first poll)
        clear_idx = source.index("clear_restart(KOAN_ROOT)", while_idx)
        assert clear_idx > while_idx
        # And it should be guarded by first_poll
        assert "first_poll" in source


# ---------------------------------------------------------------------------
# run.sh restart tests
# ---------------------------------------------------------------------------


class TestRunShRestartStructure:
    """Verify run.sh has the restart wrapper and signal detection."""

    @pytest.fixture
    def run_sh_content(self):
        run_sh = Path(__file__).parent.parent / "run.sh"
        return run_sh.read_text()

    def test_has_restart_wrapper(self, run_sh_content):
        assert "_KOAN_INNER" in run_sh_content
        assert "exit 42" in run_sh_content

    def test_wrapper_checks_exit_code_42(self, run_sh_content):
        assert 'exec_exit" -eq 42' in run_sh_content

    def test_detects_koan_restart_file(self, run_sh_content):
        assert ".koan-restart" in run_sh_content

    def test_records_start_time(self, run_sh_content):
        """run.sh should record KOAN_START_TIME for mtime comparison."""
        assert "KOAN_START_TIME=$(date +%s)" in run_sh_content

    def test_compares_mtime_before_exit_42(self, run_sh_content):
        """run.sh should compare .koan-restart mtime with KOAN_START_TIME."""
        assert "RESTART_MTIME" in run_sh_content
        assert "KOAN_START_TIME" in run_sh_content

    def test_interruptible_sleep_checks_restart(self, run_sh_content):
        # The sleep loop should check for .koan-restart
        assert '[ -f "$KOAN_ROOT/.koan-restart" ] && break' in run_sh_content

    def test_pause_sleep_checks_restart(self, run_sh_content):
        # The pause mode 5s sleep should also check for restart
        lines = run_sh_content.split("\n")
        in_pause_loop = False
        found = False
        for line in lines:
            if "for ((s=0" in line:
                in_pause_loop = True
            if in_pause_loop and ".koan-restart" in line:
                found = True
                break
            if in_pause_loop and "done" in line:
                in_pause_loop = False
        assert found, "Pause mode sleep loop should check for .koan-restart"

    def test_valid_bash_syntax(self, run_sh_content):
        """Verify run.sh has valid bash syntax."""
        import subprocess
        result = subprocess.run(
            ["bash", "-n", str(Path(__file__).parent.parent / "run.sh")],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"Syntax error: {result.stderr}"
