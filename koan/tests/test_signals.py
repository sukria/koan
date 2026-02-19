"""Tests for app.signals — centralized signal file constants."""

import pytest
from app.signals import (
    DAILY_REPORT_FILE,
    DEBUG_LOG_FILE,
    FOCUS_FILE,
    HEARTBEAT_FILE,
    PAUSE_FILE,
    PAUSE_REASON_FILE,
    PID_FILE_PREFIX,
    PROJECT_FILE,
    QUOTA_RESET_FILE,
    RESTART_FILE,
    SHUTDOWN_FILE,
    STATUS_FILE,
    STOP_FILE,
    VERBOSE_FILE,
    pid_file,
)


class TestSignalConstants:
    """Verify signal file constants have the expected values."""

    def test_stop_file(self):
        assert STOP_FILE == ".koan-stop"

    def test_shutdown_file(self):
        assert SHUTDOWN_FILE == ".koan-shutdown"

    def test_restart_file(self):
        assert RESTART_FILE == ".koan-restart"

    def test_pause_file(self):
        assert PAUSE_FILE == ".koan-pause"

    def test_pause_reason_file(self):
        assert PAUSE_REASON_FILE == ".koan-pause-reason"

    def test_status_file(self):
        assert STATUS_FILE == ".koan-status"

    def test_project_file(self):
        assert PROJECT_FILE == ".koan-project"

    def test_focus_file(self):
        assert FOCUS_FILE == ".koan-focus"

    def test_heartbeat_file(self):
        assert HEARTBEAT_FILE == ".koan-heartbeat"

    def test_verbose_file(self):
        assert VERBOSE_FILE == ".koan-verbose"

    def test_debug_log_file(self):
        assert DEBUG_LOG_FILE == ".koan-debug.log"

    def test_daily_report_file(self):
        assert DAILY_REPORT_FILE == ".koan-daily-report"

    def test_quota_reset_file(self):
        assert QUOTA_RESET_FILE == ".koan-quota-reset"

    def test_pid_file_prefix(self):
        assert PID_FILE_PREFIX == ".koan-pid-"


class TestPidFile:
    """Verify pid_file() helper."""

    def test_pid_file_run(self):
        assert pid_file("run") == ".koan-pid-run"

    def test_pid_file_awake(self):
        assert pid_file("awake") == ".koan-pid-awake"

    def test_pid_file_ollama(self):
        assert pid_file("ollama") == ".koan-pid-ollama"

    def test_pid_file_custom(self):
        assert pid_file("dashboard") == ".koan-pid-dashboard"

    def test_pid_file_uses_prefix(self):
        result = pid_file("test")
        assert result.startswith(PID_FILE_PREFIX)


class TestConstantConsistency:
    """Verify constants are used consistently across modules."""

    def test_all_constants_start_with_dot_koan(self):
        """All signal file names must start with .koan- prefix."""
        constants = [
            STOP_FILE, SHUTDOWN_FILE, RESTART_FILE,
            PAUSE_FILE, PAUSE_REASON_FILE,
            STATUS_FILE, PROJECT_FILE, FOCUS_FILE,
            HEARTBEAT_FILE, VERBOSE_FILE,
            DEBUG_LOG_FILE, DAILY_REPORT_FILE, QUOTA_RESET_FILE,
        ]
        for c in constants:
            assert c.startswith(".koan-"), f"{c} doesn't start with .koan-"

    def test_all_constants_are_strings(self):
        """All signal file constants must be strings."""
        constants = [
            STOP_FILE, SHUTDOWN_FILE, RESTART_FILE,
            PAUSE_FILE, PAUSE_REASON_FILE,
            STATUS_FILE, PROJECT_FILE, FOCUS_FILE,
            HEARTBEAT_FILE, VERBOSE_FILE,
            DEBUG_LOG_FILE, DAILY_REPORT_FILE, QUOTA_RESET_FILE,
            PID_FILE_PREFIX,
        ]
        for c in constants:
            assert isinstance(c, str), f"{c} is not a string"

    def test_no_duplicate_constants(self):
        """All signal file names must be unique."""
        constants = [
            STOP_FILE, SHUTDOWN_FILE, RESTART_FILE,
            PAUSE_FILE, PAUSE_REASON_FILE,
            STATUS_FILE, PROJECT_FILE, FOCUS_FILE,
            HEARTBEAT_FILE, VERBOSE_FILE,
            DEBUG_LOG_FILE, DAILY_REPORT_FILE, QUOTA_RESET_FILE,
        ]
        assert len(constants) == len(set(constants)), "Duplicate signal file names found"

    def test_shutdown_manager_uses_signals(self):
        """shutdown_manager must import from signals, not define its own."""
        from app import shutdown_manager
        assert not hasattr(shutdown_manager, "_SHUTDOWN_FILE_LOCAL")
        # Verify it uses the same constant
        import app.signals
        assert shutdown_manager.SHUTDOWN_FILE is app.signals.SHUTDOWN_FILE

    def test_restart_manager_uses_signals(self):
        """restart_manager must import from signals, not define its own."""
        from app import restart_manager
        import app.signals
        assert restart_manager.RESTART_FILE is app.signals.RESTART_FILE

    def test_focus_manager_uses_signals(self):
        """focus_manager must import from signals, not define its own."""
        from app import focus_manager
        import app.signals
        assert focus_manager.FOCUS_FILE is app.signals.FOCUS_FILE
