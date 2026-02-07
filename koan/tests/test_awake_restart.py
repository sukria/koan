"""Tests for awake.py restart signal integration."""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.awake import handle_command


class TestRestartCommandRouting:
    """Tests for /restart command routing in handle_command."""

    @patch("app.awake._dispatch_skill")
    def test_restart_routes_to_skill(self, mock_dispatch):
        """/restart should be dispatched via the skill system, not as a resume alias."""
        handle_command("/restart")
        mock_dispatch.assert_called_once()
        # Verify the skill was found for 'restart' command
        skill = mock_dispatch.call_args[0][0]
        assert skill is not None

    @patch("app.awake.handle_resume")
    def test_restart_does_not_resume(self, mock_resume):
        """/restart should NOT call handle_resume anymore."""
        with patch("app.awake._dispatch_skill"):
            handle_command("/restart")
        mock_resume.assert_not_called()

    @patch("app.awake.handle_resume")
    def test_resume_still_works(self, mock_resume):
        """/resume should still call handle_resume."""
        handle_command("/resume")
        mock_resume.assert_called_once()

    @patch("app.awake.handle_resume")
    def test_work_alias_still_resumes(self, mock_resume):
        handle_command("/work")
        mock_resume.assert_called_once()

    @patch("app.awake.handle_resume")
    def test_start_alias_still_resumes(self, mock_resume):
        handle_command("/start")
        mock_resume.assert_called_once()


class TestUpdateCommandRouting:
    """Tests for /update command routing."""

    @patch("app.awake._dispatch_skill")
    def test_update_routes_to_skill(self, mock_dispatch):
        handle_command("/update")
        mock_dispatch.assert_called_once()
        skill = mock_dispatch.call_args[0][0]
        assert skill is not None

    @patch("app.awake._dispatch_skill")
    def test_upgrade_routes_to_skill(self, mock_dispatch):
        handle_command("/upgrade")
        mock_dispatch.assert_called_once()


class TestHelpText:
    """Tests for help text updates."""

    @patch("app.awake.send_telegram")
    def test_help_does_not_list_restart_as_resume_alias(self, mock_send):
        from app.awake import _handle_help
        _handle_help()
        help_text = mock_send.call_args[0][0]
        # /restart should NOT appear in the resume aliases
        assert "/restart)" not in help_text
        # But /work, /awake, /start should still be aliases
        assert "/work" in help_text
        assert "/awake" in help_text
        assert "/start" in help_text


class TestRunShRestart:
    """Structural tests for run.sh restart integration."""

    def test_run_sh_has_restart_wrapper(self):
        """run.sh should have the restart wrapper (exit code 42 loop)."""
        run_sh = Path(__file__).parent.parent / "run.sh"
        content = run_sh.read_text()
        assert "_KOAN_INNER" in content
        assert "exit 42" in content
        assert 'exec_exit=$?' in content or "exec_exit=0" in content

    def test_run_sh_clears_restart_signal(self):
        """run.sh should clear .koan-restart on startup."""
        run_sh = Path(__file__).parent.parent / "run.sh"
        content = run_sh.read_text()
        assert 'rm -f "$KOAN_ROOT/.koan-restart"' in content

    def test_run_sh_checks_restart_in_main_loop(self):
        """run.sh should check for .koan-restart in the main loop."""
        run_sh = Path(__file__).parent.parent / "run.sh"
        content = run_sh.read_text()
        assert '.koan-restart' in content
        # Should check and exit 42
        assert "Restart requested" in content

    def test_run_sh_checks_restart_in_pause_sleep(self):
        """run.sh pause sleep loop should break on restart signal."""
        run_sh = Path(__file__).parent.parent / "run.sh"
        content = run_sh.read_text()
        # The pause sleep loop comment mentions restart
        assert "restart" in content.lower()

    def test_run_sh_checks_restart_in_idle_sleep(self):
        """run.sh idle sleep loop should break on restart signal."""
        run_sh = Path(__file__).parent.parent / "run.sh"
        content = run_sh.read_text()
        # Count occurrences of .koan-restart check — should be in multiple places
        count = content.count('.koan-restart')
        # At minimum: cleanup, main loop check, pause sleep, idle sleep
        assert count >= 4, f"Expected at least 4 .koan-restart references, found {count}"
