"""Tests for run loop status tracking, interruptible sleep, and loop resilience.

Covers:
- .koan-status file lifecycle (written by run.sh, read by /status and /ping)
- has_pending_missions helper (used for sleep-skip logic)
- Status handler improvements (loop status in /status, /ping)
- Run.sh structure validation (set_status, has_pending_missions, interruptible sleep)
- Run.sh resilience (error recovery, input validation, safe parsing)
"""

import importlib.util
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def run_sh_content():
    """Load run.sh content for structural validation tests."""
    return (Path(__file__).parent.parent / "run.sh").read_text()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STATUS_HANDLER_PATH = (
    Path(__file__).parent.parent / "skills" / "core" / "status" / "handler.py"
)


def _load_status_handler():
    """Load the status handler module."""
    spec = importlib.util.spec_from_file_location("status_handler", _STATUS_HANDLER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _call_status_handler(tmp_path, command_name="status"):
    """Call the status handler with given context."""
    from app.skills import SkillContext

    instance_dir = tmp_path / "instance"
    instance_dir.mkdir(exist_ok=True)
    ctx = SkillContext(
        koan_root=tmp_path,
        instance_dir=instance_dir,
        command_name=command_name,
    )
    mod = _load_status_handler()
    return mod.handle(ctx)


def _call_ping_handler(tmp_path):
    """Call the ping handler with given context."""
    return _call_status_handler(tmp_path, command_name="ping")


# ---------------------------------------------------------------------------
# Status file lifecycle
# ---------------------------------------------------------------------------

class TestStatusFileLifecycle:
    """Tests for .koan-status file read/write behavior."""

    def test_status_shows_loop_status(self, tmp_path):
        """When .koan-status exists, /status shows it."""
        (tmp_path / "instance").mkdir()
        (tmp_path / "instance" / "missions.md").write_text(
            "# Missions\n\n## Pending\n\n## In Progress\n\n"
        )
        (tmp_path / ".koan-status").write_text("Run 5/20 — executing mission on koan")
        status = _call_status_handler(tmp_path)
        assert "Run 5/20" in status
        assert "executing mission on koan" in status

    def test_status_shows_idle_state(self, tmp_path):
        """When loop is sleeping, status shows idle with time."""
        (tmp_path / "instance").mkdir()
        (tmp_path / "instance" / "missions.md").write_text(
            "# Missions\n\n## Pending\n\n## In Progress\n\n"
        )
        (tmp_path / ".koan-status").write_text("Idle — sleeping 300s (14:35)")
        status = _call_status_handler(tmp_path)
        assert "Idle" in status
        assert "sleeping 300s" in status

    def test_status_shows_preparing(self, tmp_path):
        """Status shows 'preparing' between sleep and mission execution."""
        (tmp_path / "instance").mkdir()
        (tmp_path / "instance" / "missions.md").write_text(
            "# Missions\n\n## Pending\n\n## In Progress\n\n"
        )
        (tmp_path / ".koan-status").write_text("Run 3/20 — preparing")
        status = _call_status_handler(tmp_path)
        assert "preparing" in status

    def test_status_shows_post_mission(self, tmp_path):
        """Status shows post-mission processing phase."""
        (tmp_path / "instance").mkdir()
        (tmp_path / "instance" / "missions.md").write_text(
            "# Missions\n\n## Pending\n\n## In Progress\n\n"
        )
        (tmp_path / ".koan-status").write_text("Run 3/20 — post-mission processing")
        status = _call_status_handler(tmp_path)
        assert "post-mission" in status

    def test_status_no_file_shows_working(self, tmp_path):
        """When no .koan-status file, mode still shows Working."""
        (tmp_path / "instance").mkdir()
        (tmp_path / "instance" / "missions.md").write_text(
            "# Missions\n\n## Pending\n\n## In Progress\n\n"
        )
        status = _call_status_handler(tmp_path)
        assert "Working" in status
        # No "Loop:" line when status file doesn't exist
        assert "Loop:" not in status

    def test_status_empty_file_ignored(self, tmp_path):
        """Empty .koan-status file is treated as no status."""
        (tmp_path / "instance").mkdir()
        (tmp_path / "instance" / "missions.md").write_text(
            "# Missions\n\n## Pending\n\n## In Progress\n\n"
        )
        (tmp_path / ".koan-status").write_text("")
        status = _call_status_handler(tmp_path)
        assert "Loop:" not in status

    def test_status_paused_state(self, tmp_path):
        """When paused, .koan-status shows pause time."""
        (tmp_path / "instance").mkdir()
        (tmp_path / "instance" / "missions.md").write_text(
            "# Missions\n\n## Pending\n\n## In Progress\n\n"
        )
        (tmp_path / ".koan-pause").write_text("PAUSE")
        (tmp_path / ".koan-status").write_text("Paused (14:35)")
        status = _call_status_handler(tmp_path)
        assert "Paused" in status


# ---------------------------------------------------------------------------
# Ping with loop status
# ---------------------------------------------------------------------------

class TestPingWithLoopStatus:
    """Tests for /ping showing loop status."""

    @patch("subprocess.run")
    def test_ping_shows_status_when_running(self, mock_run, tmp_path):
        """When run loop is alive and has status, /ping shows it."""
        mock_run.return_value = MagicMock(returncode=0)
        (tmp_path / "instance").mkdir()
        (tmp_path / ".koan-status").write_text("Run 3/20 — executing mission on koan")
        result = _call_ping_handler(tmp_path)
        assert "✅ OK" in result
        assert "Run 3/20" in result
        assert "executing mission" in result

    @patch("subprocess.run")
    def test_ping_shows_idle_status(self, mock_run, tmp_path):
        """When run loop is idle, /ping shows it."""
        mock_run.return_value = MagicMock(returncode=0)
        (tmp_path / "instance").mkdir()
        (tmp_path / ".koan-status").write_text("Idle — sleeping 300s (14:35)")
        result = _call_ping_handler(tmp_path)
        assert "✅ OK" in result
        assert "Idle" in result

    @patch("subprocess.run")
    def test_ping_without_status_file(self, mock_run, tmp_path):
        """When no status file, /ping just shows OK."""
        mock_run.return_value = MagicMock(returncode=0)
        (tmp_path / "instance").mkdir()
        result = _call_ping_handler(tmp_path)
        assert result == "✅ OK"

    @patch("subprocess.run")
    def test_ping_empty_status_file(self, mock_run, tmp_path):
        """Empty status file treated as no status."""
        mock_run.return_value = MagicMock(returncode=0)
        (tmp_path / "instance").mkdir()
        (tmp_path / ".koan-status").write_text("")
        result = _call_ping_handler(tmp_path)
        assert result == "✅ OK"

    @patch("subprocess.run")
    def test_ping_paused_ignores_status(self, mock_run, tmp_path):
        """When paused, /ping shows paused — doesn't show loop status."""
        mock_run.return_value = MagicMock(returncode=0)
        (tmp_path / "instance").mkdir()
        (tmp_path / ".koan-pause").write_text("PAUSE")
        (tmp_path / ".koan-status").write_text("Paused (14:35)")
        result = _call_ping_handler(tmp_path)
        assert "⏸️" in result
        # Status shouldn't bleed into the paused message
        assert result.startswith("⏸️")


# ---------------------------------------------------------------------------
# has_pending_missions validation (via missions.py count_pending)
# ---------------------------------------------------------------------------

class TestPendingMissionDetection:
    """Tests that count_pending correctly detects pending missions."""

    def test_no_pending(self):
        from app.missions import count_pending
        content = "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n"
        assert count_pending(content) == 0

    def test_one_pending(self):
        from app.missions import count_pending
        content = "# Missions\n\n## Pending\n\n- fix the bug\n\n## In Progress\n\n## Done\n"
        assert count_pending(content) == 1

    def test_multiple_pending(self):
        from app.missions import count_pending
        content = "# Missions\n\n## Pending\n\n- fix bug\n- add feature\n- audit security\n\n## In Progress\n\n## Done\n"
        assert count_pending(content) == 3

    def test_french_section_names(self):
        from app.missions import count_pending
        content = "# Missions\n\n## En attente\n\n- fix bug\n\n## En cours\n\n## Terminées\n"
        assert count_pending(content) == 1

    def test_in_progress_not_counted(self):
        from app.missions import count_pending
        content = "# Missions\n\n## Pending\n\n## In Progress\n\n- working on stuff\n\n## Done\n"
        assert count_pending(content) == 0


# ---------------------------------------------------------------------------
# run.sh structure validation
# ---------------------------------------------------------------------------

class TestRunShStructure:
    """Validate run.sh has the expected functions and patterns."""

    def test_set_status_function_exists(self, run_sh_content):
        """run.sh must define set_status()."""
        assert "set_status()" in run_sh_content

    def test_set_status_writes_to_koan_status(self, run_sh_content):
        """set_status writes to .koan-status file."""
        assert '.koan-status' in run_sh_content

    def test_has_pending_missions_function_exists(self, run_sh_content):
        """run.sh must define has_pending_missions()."""
        assert "has_pending_missions()" in run_sh_content

    def test_interruptible_sleep_pattern(self, run_sh_content):
        """Sleep between runs should check for pending missions."""
        assert "has_pending_missions" in run_sh_content
        # Should have the skip-sleep logic
        assert "skipping sleep" in run_sh_content.lower() or "skip" in run_sh_content.lower()

    def test_status_set_on_mission_execution(self, run_sh_content):
        """Status should be set when executing a mission."""
        assert "executing mission" in run_sh_content

    def test_status_set_on_idle(self, run_sh_content):
        """Status should be set when sleeping."""
        assert "Idle" in run_sh_content

    def test_status_set_on_post_mission(self, run_sh_content):
        """Status should be set during post-mission processing."""
        assert "post-mission processing" in run_sh_content

    def test_status_set_on_preparing(self, run_sh_content):
        """Status should be set when preparing a run."""
        assert "preparing" in run_sh_content

    def test_status_cleanup_on_shutdown(self, run_sh_content):
        """Status file should be cleaned up on shutdown."""
        # cleanup function should remove status file
        assert "rm -f" in run_sh_content and ".koan-status" in run_sh_content

    def test_sleep_checks_for_stop(self, run_sh_content):
        """Interruptible sleep should also check for stop/pause requests."""
        # Find the sleep section and verify it checks for stop
        assert ".koan-stop" in run_sh_content

    def test_no_hard_sleep_at_end_of_loop(self, run_sh_content):
        """The old hard 'sleep $INTERVAL' at end of loop should be replaced."""
        # Look for the pattern: it should NOT be a bare "sleep $INTERVAL" followed by "done"
        lines = run_sh_content.splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped == "sleep $INTERVAL" and i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                # OK if it's inside the interruptible sleep loop (preceded by SLEEP_ELAPSED)
                # Not OK if followed directly by "done" (the old pattern)
                if next_line == "done":
                    pytest.fail(
                        f"Found hard 'sleep $INTERVAL' followed by 'done' at line {i+1}. "
                        "Should use interruptible sleep pattern."
                    )

    def test_set_status_bash_syntax(self):
        """Verify set_status function has valid bash syntax."""
        run_sh = Path(__file__).parent.parent / "run.sh"
        result = subprocess.run(
            ["bash", "-n", str(run_sh)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"Bash syntax error: {result.stderr}"


# ---------------------------------------------------------------------------
# Integration: status handler with various loop states
# ---------------------------------------------------------------------------

class TestStatusHandlerIntegration:
    """Integration tests for status handler with realistic loop state."""

    def test_full_status_during_mission(self, tmp_path):
        """Full /status output during mission execution."""
        inst = tmp_path / "instance"
        inst.mkdir()
        (inst / "missions.md").write_text(
            "# Missions\n\n## En attente\n\n- add feature\n\n"
            "## En cours\n\n- [project:koan] fix the bug\n\n## Terminées\n"
        )
        (tmp_path / ".koan-status").write_text("Run 3/20 — executing mission on koan")

        status = _call_status_handler(tmp_path)
        assert "Working" in status
        assert "Run 3/20" in status
        assert "executing mission" in status
        assert "fix the bug" in status
        assert "add feature" in status

    def test_full_status_during_idle(self, tmp_path):
        """Full /status output when loop is sleeping."""
        inst = tmp_path / "instance"
        inst.mkdir()
        (inst / "missions.md").write_text(
            "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n"
        )
        (tmp_path / ".koan-status").write_text("Idle — sleeping 300s (14:35)")

        status = _call_status_handler(tmp_path)
        assert "Working" in status
        assert "Idle" in status
        assert "sleeping" in status

    def test_full_status_during_preparation(self, tmp_path):
        """Full /status output when loop is preparing next run."""
        inst = tmp_path / "instance"
        inst.mkdir()
        (inst / "missions.md").write_text(
            "# Missions\n\n## Pending\n\n- audit security\n\n## In Progress\n\n## Done\n"
        )
        (tmp_path / ".koan-status").write_text("Run 7/20 — preparing")

        status = _call_status_handler(tmp_path)
        assert "Working" in status
        assert "preparing" in status
        assert "audit security" in status


# ---------------------------------------------------------------------------
# run.sh resilience fixes
# ---------------------------------------------------------------------------

class TestRunShResilience:
    """Tests for run.sh error-handling and resilience patterns."""

    def test_unknown_project_does_not_exit(self, run_sh_content):
        """Unknown project mission should continue the loop, not exit.

        Bug: prior code used 'exit 1' on unknown project name, killing
        the entire agent loop. Fix uses 'continue' to skip and move on.
        """
        # Find the block that handles unknown project
        lines = run_sh_content.splitlines()
        in_unknown_project_block = False
        found_continue = False
        found_exit = False
        for line in lines:
            stripped = line.strip()
            if "Mission references unknown project" in line:
                in_unknown_project_block = True
            if in_unknown_project_block:
                if stripped == "continue":
                    found_continue = True
                    break
                if stripped.startswith("exit "):
                    found_exit = True
                    break
                # Block ends at 'fi'
                if stripped == "fi":
                    break
        assert found_continue, (
            "Unknown project block should use 'continue', not 'exit'. "
            "Exiting kills the entire agent loop on a recoverable error."
        )
        assert not found_exit, (
            "Unknown project block still contains 'exit 1' — "
            "this kills the loop on a recoverable error."
        )

    def test_project_index_validated_before_use(self, run_sh_content):
        """RECOMMENDED_PROJECT_IDX must be validated before array access.

        Bug: usage tracker output was used directly as array index without
        checking if it's numeric or within bounds.
        """
        # Look for validation pattern near the autonomous mode project selection
        assert "Invalid project index" in run_sh_content, (
            "run.sh should log a warning when RECOMMENDED_PROJECT_IDX is invalid"
        )
        # Check for numeric validation regex
        assert "^[0-9]+$" in run_sh_content, (
            "run.sh should validate RECOMMENDED_PROJECT_IDX is numeric"
        )
        # Check for bounds check
        assert "PROJECT_NAMES[@]}" in run_sh_content, (
            "run.sh should check index against PROJECT_NAMES array length"
        )

    def test_quota_result_parsing_uses_ifs(self, run_sh_content):
        """Quota result pipe-delimited parsing should use IFS read, not cut.

        Bug: 'cut -d|' silently returns wrong fields if the delimiter is
        missing. IFS read is safer — empty fields stay empty.
        """
        # Should NOT use 'cut -d' for quota result parsing
        lines = run_sh_content.splitlines()
        for i, line in enumerate(lines):
            if "RESET_DISPLAY" in line and "cut -d" in line:
                pytest.fail(
                    f"Line {i+1}: quota result parsing uses 'cut -d' which is fragile. "
                    "Should use 'IFS=| read -r' instead."
                )
            if "RESUME_MSG" in line and "cut -d" in line:
                pytest.fail(
                    f"Line {i+1}: quota result parsing uses 'cut -d' which is fragile. "
                    "Should use 'IFS=| read -r' instead."
                )
        # Verify IFS-based parsing is used
        assert "IFS='|' read -r RESET_DISPLAY RESUME_MSG" in run_sh_content, (
            "Quota result should be parsed with IFS='|' read -r"
        )

    def test_unknown_project_increments_counter(self, run_sh_content):
        """When skipping unknown project, run counter should be incremented.

        Without this, the loop could retry the same broken mission forever
        in certain edge cases.
        """
        lines = run_sh_content.splitlines()
        in_unknown_block = False
        found_increment = False
        for line in lines:
            if "Mission references unknown project" in line:
                in_unknown_block = True
            if in_unknown_block:
                if "count=$((count + 1))" in line:
                    found_increment = True
                    break
                if line.strip() == "fi":
                    break
        assert found_increment, (
            "Unknown project block should increment the run counter "
            "to prevent infinite retry loops."
        )

    def test_unknown_project_notification_not_error_exit(self, run_sh_content):
        """Notification for unknown project should be a warning, not fatal."""
        # Find the notify line in the unknown project block
        lines = run_sh_content.splitlines()
        in_unknown_block = False
        for line in lines:
            if "Mission references unknown project" in line:
                in_unknown_block = True
            if in_unknown_block and "notify" in line:
                # Should use warning emoji, not error emoji
                assert "⚠️" in line or "warning" in line.lower() or "skipped" in line.lower(), (
                    "Unknown project notification should indicate a warning/skip, "
                    "not a fatal error."
                )
                break

    def test_fallback_project_index_is_zero(self, run_sh_content):
        """Invalid project index should fall back to index 0 (first project)."""
        lines = run_sh_content.splitlines()
        in_validation_block = False
        found_fallback = False
        for line in lines:
            if "Invalid project index" in line:
                in_validation_block = True
            if in_validation_block:
                if "PROJECT_IDX=0" in line:
                    found_fallback = True
                    break
                if line.strip() == "fi":
                    break
        assert found_fallback, (
            "Invalid project index should fall back to PROJECT_IDX=0"
        )
