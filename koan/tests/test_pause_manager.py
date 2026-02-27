"""Tests for pause_manager.py — pause/resume state management."""

import json
import os
import subprocess
import sys

import pytest


class TestIsPaused:
    """Test is_paused function."""

    def test_not_paused_when_no_file(self, tmp_path):
        from app.pause_manager import is_paused

        assert is_paused(str(tmp_path)) is False

    def test_paused_when_file_exists(self, tmp_path):
        from app.pause_manager import is_paused

        (tmp_path / ".koan-pause").touch()
        assert is_paused(str(tmp_path)) is True

    def test_paused_with_empty_file(self, tmp_path):
        from app.pause_manager import is_paused

        (tmp_path / ".koan-pause").write_text("")
        assert is_paused(str(tmp_path)) is True


class TestGetPauseState:
    """Test get_pause_state function."""

    def test_returns_none_when_not_paused(self, tmp_path):
        from app.pause_manager import get_pause_state

        assert get_pause_state(str(tmp_path)) is None

    def test_returns_none_when_no_reason_file(self, tmp_path):
        from app.pause_manager import get_pause_state

        (tmp_path / ".koan-pause").touch()
        assert get_pause_state(str(tmp_path)) is None

    def test_reads_quota_pause(self, tmp_path):
        from app.pause_manager import get_pause_state

        (tmp_path / ".koan-pause").touch()
        (tmp_path / ".koan-pause-reason").write_text("quota\n1707000000\nresets 10am\n")

        state = get_pause_state(str(tmp_path))
        assert state is not None
        assert state.reason == "quota"
        assert state.timestamp == 1707000000
        assert state.display == "resets 10am"
        assert state.is_quota is True

    def test_reads_max_runs_pause(self, tmp_path):
        from app.pause_manager import get_pause_state

        (tmp_path / ".koan-pause").touch()
        (tmp_path / ".koan-pause-reason").write_text("max_runs\n1707000000\n\n")

        state = get_pause_state(str(tmp_path))
        assert state is not None
        assert state.reason == "max_runs"
        assert state.timestamp == 1707000000
        assert state.is_quota is False

    def test_handles_missing_lines(self, tmp_path):
        from app.pause_manager import get_pause_state

        (tmp_path / ".koan-pause").touch()
        (tmp_path / ".koan-pause-reason").write_text("quota\n")

        state = get_pause_state(str(tmp_path))
        assert state is not None
        assert state.reason == "quota"
        assert state.timestamp == 0
        assert state.display == ""

    def test_handles_reason_only(self, tmp_path):
        from app.pause_manager import get_pause_state

        (tmp_path / ".koan-pause").touch()
        (tmp_path / ".koan-pause-reason").write_text("manual\n")

        state = get_pause_state(str(tmp_path))
        assert state is not None
        assert state.reason == "manual"
        assert state.timestamp == 0

    def test_handles_invalid_timestamp(self, tmp_path):
        from app.pause_manager import get_pause_state

        (tmp_path / ".koan-pause").touch()
        (tmp_path / ".koan-pause-reason").write_text("quota\nnot_a_number\ninfo\n")

        state = get_pause_state(str(tmp_path))
        assert state is not None
        assert state.reason == "quota"
        assert state.timestamp == 0
        assert state.display == "info"

    def test_handles_empty_reason_file(self, tmp_path):
        from app.pause_manager import get_pause_state

        (tmp_path / ".koan-pause").touch()
        (tmp_path / ".koan-pause-reason").write_text("")

        assert get_pause_state(str(tmp_path)) is None

    def test_strips_whitespace(self, tmp_path):
        from app.pause_manager import get_pause_state

        (tmp_path / ".koan-pause").touch()
        (tmp_path / ".koan-pause-reason").write_text("  quota  \n  1707000000  \n  resets 10am  \n")

        state = get_pause_state(str(tmp_path))
        assert state.reason == "quota"
        assert state.timestamp == 1707000000
        assert state.display == "resets 10am"


class TestShouldAutoResume:
    """Test should_auto_resume function."""

    def test_quota_resumes_when_time_reached(self):
        from app.pause_manager import PauseState, should_auto_resume

        state = PauseState(reason="quota", timestamp=1000, display="resets 10am")
        assert should_auto_resume(state, now=1001) is True

    def test_quota_resumes_at_exact_time(self):
        from app.pause_manager import PauseState, should_auto_resume

        state = PauseState(reason="quota", timestamp=1000, display="resets 10am")
        assert should_auto_resume(state, now=1000) is True

    def test_quota_stays_paused_before_reset(self):
        from app.pause_manager import PauseState, should_auto_resume

        state = PauseState(reason="quota", timestamp=2000, display="resets 10am")
        assert should_auto_resume(state, now=1000) is False

    def test_quota_with_zero_timestamp_stays_paused(self):
        from app.pause_manager import PauseState, should_auto_resume

        state = PauseState(reason="quota", timestamp=0, display="unknown")
        assert should_auto_resume(state, now=1000) is False

    def test_max_runs_resumes_after_5h(self):
        from app.pause_manager import PauseState, should_auto_resume

        pause_time = 1000
        five_hours = 5 * 60 * 60
        state = PauseState(reason="max_runs", timestamp=pause_time, display="")
        assert should_auto_resume(state, now=pause_time + five_hours) is True

    def test_max_runs_stays_paused_before_5h(self):
        from app.pause_manager import PauseState, should_auto_resume

        pause_time = 1000
        four_hours = 4 * 60 * 60
        state = PauseState(reason="max_runs", timestamp=pause_time, display="")
        assert should_auto_resume(state, now=pause_time + four_hours) is False

    def test_max_runs_resumes_at_exact_5h(self):
        from app.pause_manager import PauseState, should_auto_resume

        pause_time = 1000
        five_hours = 5 * 60 * 60
        state = PauseState(reason="max_runs", timestamp=pause_time, display="")
        assert should_auto_resume(state, now=pause_time + five_hours) is True

    def test_max_runs_with_zero_timestamp_stays_paused(self):
        from app.pause_manager import PauseState, should_auto_resume

        state = PauseState(reason="max_runs", timestamp=0, display="")
        assert should_auto_resume(state, now=1000) is False

    def test_unknown_reason_uses_5h_cooldown(self):
        from app.pause_manager import PauseState, should_auto_resume

        pause_time = 1000
        five_hours = 5 * 60 * 60
        state = PauseState(reason="custom_reason", timestamp=pause_time, display="")
        assert should_auto_resume(state, now=pause_time + five_hours) is True

    def test_uses_current_time_when_now_not_provided(self):
        from app.pause_manager import PauseState, should_auto_resume

        # Far past timestamp should always resume
        state = PauseState(reason="quota", timestamp=1, display="")
        assert should_auto_resume(state) is True

        # Far future timestamp should not resume
        state = PauseState(reason="quota", timestamp=9999999999, display="")
        assert should_auto_resume(state) is False


class TestCreatePause:
    """Test create_pause function."""

    def test_creates_both_files(self, tmp_path):
        from app.pause_manager import create_pause

        create_pause(str(tmp_path), "quota", 1707000000, "resets 10am")

        assert (tmp_path / ".koan-pause").exists()
        assert (tmp_path / ".koan-pause-reason").exists()

    def test_writes_correct_format(self, tmp_path):
        from app.pause_manager import create_pause

        create_pause(str(tmp_path), "quota", 1707000000, "resets 10am")

        content = (tmp_path / ".koan-pause-reason").read_text()
        lines = content.strip().splitlines()
        assert lines[0] == "quota"
        assert lines[1] == "1707000000"
        assert lines[2] == "resets 10am"

    def test_creates_max_runs_pause(self, tmp_path):
        from app.pause_manager import create_pause

        create_pause(str(tmp_path), "max_runs", 1707000000)

        content = (tmp_path / ".koan-pause-reason").read_text()
        lines = content.strip().splitlines()
        assert lines[0] == "max_runs"
        assert lines[1] == "1707000000"

    def test_defaults_timestamp_to_current_time(self, tmp_path):
        import time

        from app.pause_manager import create_pause

        before = int(time.time())
        create_pause(str(tmp_path), "max_runs")
        after = int(time.time())

        content = (tmp_path / ".koan-pause-reason").read_text()
        ts = int(content.strip().splitlines()[1])
        assert before <= ts <= after

    def test_empty_display(self, tmp_path):
        from app.pause_manager import create_pause

        create_pause(str(tmp_path), "max_runs", 1000, "")

        content = (tmp_path / ".koan-pause-reason").read_text()
        lines = content.splitlines()
        assert len(lines) >= 3
        assert lines[2] == ""

    def test_overwrites_existing_pause(self, tmp_path):
        from app.pause_manager import create_pause

        create_pause(str(tmp_path), "quota", 1000, "old")
        create_pause(str(tmp_path), "max_runs", 2000, "new")

        content = (tmp_path / ".koan-pause-reason").read_text()
        lines = content.strip().splitlines()
        assert lines[0] == "max_runs"
        assert lines[1] == "2000"
        assert lines[2] == "new"


class TestRemovePause:
    """Test remove_pause function."""

    def test_removes_both_files(self, tmp_path):
        from app.pause_manager import remove_pause

        (tmp_path / ".koan-pause").touch()
        (tmp_path / ".koan-pause-reason").write_text("quota\n1000\ninfo\n")

        remove_pause(str(tmp_path))

        assert not (tmp_path / ".koan-pause").exists()
        assert not (tmp_path / ".koan-pause-reason").exists()

    def test_removes_pause_without_reason(self, tmp_path):
        from app.pause_manager import remove_pause

        (tmp_path / ".koan-pause").touch()

        remove_pause(str(tmp_path))

        assert not (tmp_path / ".koan-pause").exists()

    def test_noop_when_not_paused(self, tmp_path):
        from app.pause_manager import remove_pause

        # Should not raise
        remove_pause(str(tmp_path))

    def test_removes_reason_before_pause(self, tmp_path):
        """Reason file must be removed before the pause signal file.

        If interrupted between the two removals, the system should still
        report as paused (reason gone + pause present) rather than the
        reverse (pause gone + orphan reason file).
        """
        from app.pause_manager import is_paused, remove_pause

        pause_file = tmp_path / ".koan-pause"
        reason_file = tmp_path / ".koan-pause-reason"
        pause_file.touch()
        reason_file.write_text("quota\n1000\ninfo\n")

        from unittest.mock import patch

        removal_order = []
        original_remove = os.remove

        def tracking_remove(path):
            name = os.path.basename(path)
            removal_order.append(name)
            original_remove(path)

        with patch("app.pause_manager.os.remove", side_effect=tracking_remove):
            remove_pause(str(tmp_path))

        assert removal_order == [".koan-pause-reason", ".koan-pause"], \
            "reason file must be removed before the pause signal file"


class TestCheckAndResume:
    """Test check_and_resume function."""

    def test_returns_none_when_not_paused(self, tmp_path):
        from app.pause_manager import check_and_resume

        assert check_and_resume(str(tmp_path)) is None

    def test_orphan_pause_stays_paused(self, tmp_path):
        from app.pause_manager import check_and_resume, is_paused

        (tmp_path / ".koan-pause").touch()
        msg = check_and_resume(str(tmp_path))
        assert msg is None, "Orphan pause should stay paused"
        assert is_paused(str(tmp_path)), "Pause file should remain"

    def test_auto_resumes_quota_past_reset(self, tmp_path, monkeypatch):
        from app.pause_manager import check_and_resume

        (tmp_path / ".koan-pause").touch()
        (tmp_path / ".koan-pause-reason").write_text("quota\n1000\nresets 10am\n")

        # Mock time to be past reset
        monkeypatch.setattr("app.pause_manager.time.time", lambda: 2000)

        msg = check_and_resume(str(tmp_path))
        assert msg is not None
        assert "quota reset time reached" in msg
        assert "resets 10am" in msg
        assert not (tmp_path / ".koan-pause").exists()
        assert not (tmp_path / ".koan-pause-reason").exists()

    def test_stays_paused_before_quota_reset(self, tmp_path, monkeypatch):
        from app.pause_manager import check_and_resume

        (tmp_path / ".koan-pause").touch()
        (tmp_path / ".koan-pause-reason").write_text("quota\n5000\nresets 10am\n")

        monkeypatch.setattr("app.pause_manager.time.time", lambda: 1000)

        msg = check_and_resume(str(tmp_path))
        assert msg is None
        assert (tmp_path / ".koan-pause").exists()

    def test_auto_resumes_max_runs_after_5h(self, tmp_path, monkeypatch):
        from app.pause_manager import check_and_resume

        pause_time = 1000
        (tmp_path / ".koan-pause").touch()
        (tmp_path / ".koan-pause-reason").write_text(f"max_runs\n{pause_time}\n\n")

        monkeypatch.setattr("app.pause_manager.time.time", lambda: pause_time + 5 * 3600)

        msg = check_and_resume(str(tmp_path))
        assert msg is not None
        assert "5h have passed" in msg
        assert not (tmp_path / ".koan-pause").exists()

    def test_stays_paused_max_runs_before_5h(self, tmp_path, monkeypatch):
        from app.pause_manager import check_and_resume

        pause_time = 1000
        (tmp_path / ".koan-pause").touch()
        (tmp_path / ".koan-pause-reason").write_text(f"max_runs\n{pause_time}\n\n")

        monkeypatch.setattr("app.pause_manager.time.time", lambda: pause_time + 3600)

        msg = check_and_resume(str(tmp_path))
        assert msg is None
        assert (tmp_path / ".koan-pause").exists()


class TestPauseStateDataclass:
    """Test PauseState properties."""

    def test_is_quota_true(self):
        from app.pause_manager import PauseState

        state = PauseState(reason="quota", timestamp=0, display="")
        assert state.is_quota is True

    def test_is_quota_false_for_max_runs(self):
        from app.pause_manager import PauseState

        state = PauseState(reason="max_runs", timestamp=0, display="")
        assert state.is_quota is False

    def test_is_quota_false_for_other(self):
        from app.pause_manager import PauseState

        state = PauseState(reason="manual", timestamp=0, display="")
        assert state.is_quota is False


class TestRoundTrip:
    """Test create → get → resume cycle."""

    def test_create_then_get(self, tmp_path):
        from app.pause_manager import create_pause, get_pause_state

        create_pause(str(tmp_path), "quota", 1707000000, "resets 10am (Europe/Paris)")

        state = get_pause_state(str(tmp_path))
        assert state is not None
        assert state.reason == "quota"
        assert state.timestamp == 1707000000
        assert state.display == "resets 10am (Europe/Paris)"

    def test_create_then_remove_then_get(self, tmp_path):
        from app.pause_manager import create_pause, get_pause_state, remove_pause

        create_pause(str(tmp_path), "quota", 1707000000, "resets 10am")
        remove_pause(str(tmp_path))

        assert get_pause_state(str(tmp_path)) is None

    def test_full_lifecycle_quota(self, tmp_path, monkeypatch):
        from app.pause_manager import (
            check_and_resume,
            create_pause,
            is_paused,
        )

        # Not paused initially
        assert is_paused(str(tmp_path)) is False

        # Create quota pause with reset at 2000
        create_pause(str(tmp_path), "quota", 2000, "resets 10am")
        assert is_paused(str(tmp_path)) is True

        # Before reset time — stay paused
        monkeypatch.setattr("app.pause_manager.time.time", lambda: 1500)
        assert check_and_resume(str(tmp_path)) is None
        assert is_paused(str(tmp_path)) is True

        # After reset time — auto-resume
        monkeypatch.setattr("app.pause_manager.time.time", lambda: 2500)
        msg = check_and_resume(str(tmp_path))
        assert msg is not None
        assert is_paused(str(tmp_path)) is False

    def test_full_lifecycle_max_runs(self, tmp_path, monkeypatch):
        from app.pause_manager import (
            check_and_resume,
            create_pause,
            is_paused,
        )

        pause_time = 10000

        # Create max_runs pause
        create_pause(str(tmp_path), "max_runs", pause_time)
        assert is_paused(str(tmp_path)) is True

        # 1h later — stay paused
        monkeypatch.setattr("app.pause_manager.time.time", lambda: pause_time + 3600)
        assert check_and_resume(str(tmp_path)) is None

        # 5h later — auto-resume
        monkeypatch.setattr("app.pause_manager.time.time", lambda: pause_time + 5 * 3600)
        msg = check_and_resume(str(tmp_path))
        assert msg is not None
        assert "5h have passed" in msg
        assert is_paused(str(tmp_path)) is False


class TestCLI:
    """Test CLI interface (run as __main__)."""

    def _run_cli(self, *args):
        """Run pause_manager.py as CLI command."""
        result = subprocess.run(
            [sys.executable, "-m", "app.pause_manager", *args],
            capture_output=True,
            text=True,
            cwd=os.path.join(os.path.dirname(__file__), ".."),
        )
        return result

    def test_status_not_paused(self, tmp_path):
        result = self._run_cli("status", str(tmp_path))
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["paused"] is False

    def test_status_paused(self, tmp_path):
        from app.pause_manager import create_pause

        create_pause(str(tmp_path), "quota", 1707000000, "resets 10am")

        result = self._run_cli("status", str(tmp_path))
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["paused"] is True
        assert data["reason"] == "quota"
        assert data["timestamp"] == 1707000000
        assert data["display"] == "resets 10am"

    def test_create_and_status(self, tmp_path):
        result = self._run_cli("create", str(tmp_path), "max_runs", "1000", "")
        assert result.returncode == 0

        result = self._run_cli("status", str(tmp_path))
        data = json.loads(result.stdout)
        assert data["paused"] is True
        assert data["reason"] == "max_runs"

    def test_remove(self, tmp_path):
        from app.pause_manager import create_pause

        create_pause(str(tmp_path), "quota", 1000, "info")

        result = self._run_cli("remove", str(tmp_path))
        assert result.returncode == 0

        result = self._run_cli("status", str(tmp_path))
        data = json.loads(result.stdout)
        assert data["paused"] is False

    def test_check_not_paused(self, tmp_path):
        result = self._run_cli("check", str(tmp_path))
        # Exit 1 = still paused or not paused (no resume action taken)
        assert result.returncode == 1

    def test_check_resumes_past_quota(self, tmp_path):
        from app.pause_manager import create_pause

        # Reset time in the past
        create_pause(str(tmp_path), "quota", 1, "old reset")

        result = self._run_cli("check", str(tmp_path))
        assert result.returncode == 0
        assert "quota reset time reached" in result.stdout

    def test_check_stays_paused_future_quota(self, tmp_path):
        from app.pause_manager import create_pause

        # Reset time far in the future
        create_pause(str(tmp_path), "quota", 9999999999, "future reset")

        result = self._run_cli("check", str(tmp_path))
        assert result.returncode == 1

    def test_unknown_command(self, tmp_path):
        result = self._run_cli("bogus", str(tmp_path))
        assert result.returncode == 1

    def test_no_args(self):
        result = self._run_cli()
        assert result.returncode == 1


class TestOrphanPauseStaysPaused:
    """Tests for orphan .koan-pause handling (missing reason file).

    Orphan .koan-pause files (missing or empty reason) stay paused as a safe
    default.  The user can always /resume manually.  The old behavior
    auto-resumed orphans, which overrode user-initiated /pause when the
    reason file was lost (e.g. start_on_pause cleanup, crash).
    """

    def test_orphan_pause_stays_paused(self, tmp_path):
        """Orphan .koan-pause with no reason file should stay paused."""
        from app.pause_manager import check_and_resume, is_paused

        (tmp_path / ".koan-pause").touch()
        # No .koan-pause-reason

        msg = check_and_resume(str(tmp_path))
        assert msg is None, "Orphan pause should stay paused (safe default)"
        assert is_paused(str(tmp_path)), "Pause file should still exist"

    def test_orphan_pause_with_empty_reason_file(self, tmp_path):
        """Orphan .koan-pause with empty reason file should stay paused."""
        from app.pause_manager import check_and_resume, is_paused

        (tmp_path / ".koan-pause").touch()
        (tmp_path / ".koan-pause-reason").write_text("")

        msg = check_and_resume(str(tmp_path))
        assert msg is None, "Orphan pause should stay paused"
        assert is_paused(str(tmp_path))

    def test_orphan_stays_paused_repeatedly(self, tmp_path):
        """Calling check_and_resume on orphan always returns None."""
        from app.pause_manager import check_and_resume, is_paused

        (tmp_path / ".koan-pause").touch()

        for _ in range(3):
            msg = check_and_resume(str(tmp_path))
            assert msg is None
            assert is_paused(str(tmp_path))

    def test_normal_pause_not_treated_as_orphan(self, tmp_path, monkeypatch):
        """A valid pause (with reason file) should NOT be treated as orphan."""
        from app.pause_manager import check_and_resume, create_pause

        create_pause(str(tmp_path), "quota", 9999999999, "future")
        monkeypatch.setattr("app.pause_manager.time.time", lambda: 1000)

        msg = check_and_resume(str(tmp_path))
        assert msg is None  # Still paused, not orphan
        assert (tmp_path / ".koan-pause").exists()

    def test_orphan_pause_lifecycle(self, tmp_path):
        """Full lifecycle: create pause, delete reason, stays paused."""
        from app.pause_manager import check_and_resume, create_pause, is_paused

        create_pause(str(tmp_path), "quota", 9999999999, "future")
        assert is_paused(str(tmp_path)) is True

        # Simulate crash: reason file deleted but pause file remains
        (tmp_path / ".koan-pause-reason").unlink()
        assert is_paused(str(tmp_path)) is True  # Still "paused"

        # check_and_resume should NOT auto-resume — stays paused
        msg = check_and_resume(str(tmp_path))
        assert msg is None
        assert is_paused(str(tmp_path)) is True

    def test_orphan_cleared_by_manual_resume(self, tmp_path):
        """Orphan pause can be cleared via remove_pause (like /resume does)."""
        from app.pause_manager import check_and_resume, is_paused, remove_pause

        (tmp_path / ".koan-pause").touch()

        # Stays paused
        assert check_and_resume(str(tmp_path)) is None
        assert is_paused(str(tmp_path))

        # Manual resume clears it
        remove_pause(str(tmp_path))
        assert not is_paused(str(tmp_path))


class TestManualPauseNeverAutoResumes:
    """Tests that manual pauses (reason='manual') never auto-resume.

    Manual pauses represent explicit user intent via /pause or /sleep.
    Only /resume should clear them — no timeout, no orphan cleanup.
    """

    def test_manual_pause_never_auto_resumes_via_should_auto_resume(self):
        """should_auto_resume always returns False for manual pauses."""
        from app.pause_manager import PauseState, should_auto_resume

        pause_time = 1000
        five_hours = 5 * 60 * 60
        state = PauseState(reason="manual", timestamp=pause_time, display="paused via Telegram")
        # Even after 5h, manual pause should NOT auto-resume
        assert should_auto_resume(state, now=pause_time + five_hours) is False
        assert should_auto_resume(state, now=pause_time + 10 * five_hours) is False

    def test_manual_pause_never_auto_resumes_via_check_and_resume(self, tmp_path, monkeypatch):
        """check_and_resume never resumes a manual pause regardless of time elapsed."""
        from app.pause_manager import check_and_resume, create_pause, is_paused

        pause_time = 1000
        create_pause(str(tmp_path), "manual", pause_time, "paused via Telegram")

        # Even 24h later, should stay paused
        monkeypatch.setattr("app.pause_manager.time.time", lambda: pause_time + 24 * 3600)
        msg = check_and_resume(str(tmp_path))
        assert msg is None
        assert is_paused(str(tmp_path))

    def test_manual_pause_with_zero_timestamp(self):
        """Manual pause with zero timestamp should not auto-resume."""
        from app.pause_manager import PauseState, should_auto_resume

        state = PauseState(reason="manual", timestamp=0, display="")
        assert should_auto_resume(state, now=9999999999) is False

    def test_manual_pause_cleared_only_by_resume(self, tmp_path, monkeypatch):
        """Manual pause stays until explicitly removed via /resume."""
        from app.pause_manager import (
            check_and_resume,
            create_pause,
            is_paused,
            remove_pause,
        )

        create_pause(str(tmp_path), "manual", 1000, "paused via Telegram")

        # Time passes — still paused
        monkeypatch.setattr("app.pause_manager.time.time", lambda: 999999)
        assert check_and_resume(str(tmp_path)) is None
        assert is_paused(str(tmp_path))

        # Only remove_pause (triggered by /resume) clears it
        remove_pause(str(tmp_path))
        assert not is_paused(str(tmp_path))

    def test_manual_pause_survives_start_on_pause_reason_deletion(self, tmp_path):
        """Regression: start_on_pause deleting reason should not override manual pause.

        Even if something removes the reason file, the orphan handler
        should not auto-resume — it stays paused as a safe default.
        """
        from app.pause_manager import check_and_resume, create_pause, is_paused

        create_pause(str(tmp_path), "manual", 1000, "paused via Telegram")

        # Simulate start_on_pause deleting reason file
        (tmp_path / ".koan-pause-reason").unlink()

        # Orphan should stay paused (not auto-resume)
        msg = check_and_resume(str(tmp_path))
        assert msg is None
        assert is_paused(str(tmp_path))

    def test_system_pauses_still_auto_resume(self, tmp_path, monkeypatch):
        """Non-manual pauses (quota, max_runs) still auto-resume as before."""
        from app.pause_manager import check_and_resume, create_pause

        # Quota pause with past reset time
        create_pause(str(tmp_path), "quota", 1000, "resets 10am")
        monkeypatch.setattr("app.pause_manager.time.time", lambda: 2000)
        msg = check_and_resume(str(tmp_path))
        assert msg is not None
        assert "quota reset time reached" in msg


class TestBudgetLoopRegression:
    """Regression tests for the budget exhaustion infinite loop bug.

    The bug: when usage_tracker decided "wait" mode, the agent loop called
    `pause_manager create quota` without a timestamp. This defaulted
    to `time.time()` (now), making should_auto_resume() immediately
    return True — causing an instant resume -> re-pause -> resume loop.

    The fix: the agent loop now passes the session reset timestamp from
    usage_estimator.py, ensuring the pause always has a future timestamp.
    """

    def test_quota_pause_with_now_timestamp_resumes_immediately(self, tmp_path, monkeypatch):
        """Demonstrates the bug: quota pause with current time = instant resume."""
        from app.pause_manager import create_pause, check_and_resume

        now = 1000000
        monkeypatch.setattr("app.pause_manager.time.time", lambda: now)

        # BUG: create pause with timestamp = now (old behavior)
        create_pause(str(tmp_path), "quota", now, "")

        msg = check_and_resume(str(tmp_path))
        assert msg is not None, "Bug confirmed: pause with current timestamp auto-resumes immediately"

    def test_quota_pause_with_future_timestamp_stays_paused(self, tmp_path, monkeypatch):
        """Demonstrates the fix: quota pause with future timestamp stays paused."""
        from app.pause_manager import create_pause, check_and_resume

        now = 1000000
        future_reset = now + 5 * 3600  # 5 hours from now
        monkeypatch.setattr("app.pause_manager.time.time", lambda: now)

        create_pause(str(tmp_path), "quota", future_reset, "reset at 15:00")

        msg = check_and_resume(str(tmp_path))
        assert msg is None, "Fix confirmed: pause with future timestamp stays paused"

    def test_quota_pause_with_future_timestamp_resumes_at_reset(self, tmp_path, monkeypatch):
        """After waiting, the pause correctly auto-resumes at reset time."""
        from app.pause_manager import create_pause, check_and_resume

        now = 1000000
        future_reset = now + 5 * 3600

        create_pause(str(tmp_path), "quota", future_reset, "reset at 15:00")

        # Still paused 1 hour later
        monkeypatch.setattr("app.pause_manager.time.time", lambda: now + 3600)
        assert check_and_resume(str(tmp_path)) is None

        # Resumes at reset time
        monkeypatch.setattr("app.pause_manager.time.time", lambda: future_reset + 1)
        msg = check_and_resume(str(tmp_path))
        assert msg is not None
        assert "quota reset time reached" in msg


class TestCreatePauseAtomicWrite:
    """Test that create_pause uses atomic_write for thread safety."""

    def test_uses_atomic_write_for_both_files(self, tmp_path):
        from unittest.mock import patch

        from app.pause_manager import create_pause

        with patch("app.utils.atomic_write") as mock_aw:
            create_pause(str(tmp_path), "quota", 1707000000, "resets 10am")
            assert mock_aw.call_count == 2
            # First call: reason file
            reason_path = str(mock_aw.call_args_list[0][0][0])
            assert reason_path.endswith(".koan-pause-reason")
            content = mock_aw.call_args_list[0][0][1]
            assert "quota" in content
            assert "1707000000" in content
            # Second call: pause signal file (atomic, not touch())
            pause_path = str(mock_aw.call_args_list[1][0][0])
            assert pause_path.endswith(".koan-pause")
            assert mock_aw.call_args_list[1][0][1] == ""
