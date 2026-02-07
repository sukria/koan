"""Tests for the /status, /ping, /usage core skill handlers."""

from unittest.mock import patch, MagicMock

from app.skills import SkillContext


def _make_ctx(command_name, instance_dir, koan_root=None):
    """Create a minimal SkillContext for testing."""
    ctx = MagicMock(spec=SkillContext)
    ctx.command_name = command_name
    ctx.instance_dir = instance_dir
    ctx.koan_root = koan_root or instance_dir.parent
    ctx.args = ""
    return ctx


# ---------------------------------------------------------------------------
# handle() dispatch
# ---------------------------------------------------------------------------

class TestStatusDispatch:
    """Test the top-level handle() dispatcher."""

    def test_dispatch_to_status(self, tmp_path):
        instance = tmp_path / "instance"
        instance.mkdir()
        from skills.core.status.handler import handle
        ctx = _make_ctx("status", instance, tmp_path)
        result = handle(ctx)
        assert "Koan Status" in result

    def test_dispatch_to_ping(self, tmp_path):
        instance = tmp_path / "instance"
        instance.mkdir()
        from skills.core.status.handler import handle
        ctx = _make_ctx("ping", instance, tmp_path)
        with patch("skills.core.status.handler.subprocess") as mock_sub:
            mock_sub.run.return_value = MagicMock(returncode=1)
            result = handle(ctx)
        assert "Run loop" in result or "OK" in result

    def test_dispatch_to_usage(self, tmp_path):
        instance = tmp_path / "instance"
        instance.mkdir()
        from skills.core.status.handler import handle
        ctx = _make_ctx("usage", instance, tmp_path)
        result = handle(ctx)
        assert "Quota" in result


# ---------------------------------------------------------------------------
# _handle_status()
# ---------------------------------------------------------------------------

class TestHandleStatus:
    """Test /status output under various states."""

    def test_working_mode_no_files(self, tmp_path):
        """No pause/stop files = Working mode."""
        instance = tmp_path / "instance"
        instance.mkdir()
        from skills.core.status.handler import _handle_status
        ctx = _make_ctx("status", instance, tmp_path)
        result = _handle_status(ctx)
        assert "Working" in result
        assert "Paused" not in result

    def test_paused_mode_generic(self, tmp_path):
        """Pause file without reason = generic pause."""
        instance = tmp_path / "instance"
        instance.mkdir()
        (tmp_path / ".koan-pause").touch()
        from skills.core.status.handler import _handle_status
        ctx = _make_ctx("status", instance, tmp_path)
        result = _handle_status(ctx)
        assert "Paused" in result
        assert "/resume" in result

    def test_paused_mode_quota(self, tmp_path):
        """Pause with quota reason shows specific message."""
        instance = tmp_path / "instance"
        instance.mkdir()
        (tmp_path / ".koan-pause").touch()
        (tmp_path / ".koan-pause-reason").write_text("quota\n2026-02-07T12:00:00\nResets at 12:00")
        from skills.core.status.handler import _handle_status
        ctx = _make_ctx("status", instance, tmp_path)
        result = _handle_status(ctx)
        assert "quota" in result.lower()

    def test_paused_mode_max_runs(self, tmp_path):
        """Pause with max_runs reason shows specific message."""
        instance = tmp_path / "instance"
        instance.mkdir()
        (tmp_path / ".koan-pause").touch()
        (tmp_path / ".koan-pause-reason").write_text("max_runs\n2026-02-07T12:00:00\n")
        from skills.core.status.handler import _handle_status
        ctx = _make_ctx("status", instance, tmp_path)
        result = _handle_status(ctx)
        assert "max runs" in result.lower()

    def test_stopping_mode(self, tmp_path):
        """Stop file = Stopping mode."""
        instance = tmp_path / "instance"
        instance.mkdir()
        (tmp_path / ".koan-stop").touch()
        from skills.core.status.handler import _handle_status
        ctx = _make_ctx("status", instance, tmp_path)
        result = _handle_status(ctx)
        assert "Stopping" in result

    def test_stop_takes_precedence_over_pause(self, tmp_path):
        """If both stop and pause exist, stop wins."""
        instance = tmp_path / "instance"
        instance.mkdir()
        (tmp_path / ".koan-stop").touch()
        (tmp_path / ".koan-pause").touch()
        from skills.core.status.handler import _handle_status
        ctx = _make_ctx("status", instance, tmp_path)
        result = _handle_status(ctx)
        assert "Stopping" in result
        assert "Paused" not in result

    def test_loop_status_shown(self, tmp_path):
        """When .koan-status exists, its content is included."""
        instance = tmp_path / "instance"
        instance.mkdir()
        (tmp_path / ".koan-status").write_text("idle — no pending missions")
        from skills.core.status.handler import _handle_status
        ctx = _make_ctx("status", instance, tmp_path)
        result = _handle_status(ctx)
        assert "idle" in result

    def test_missions_shown(self, tmp_path):
        """Pending and in-progress missions are displayed."""
        instance = tmp_path / "instance"
        instance.mkdir()
        missions = instance / "missions.md"
        missions.write_text(
            "# Missions\n\n"
            "## Pending\n\n"
            "- [project:koan] fix the bug\n"
            "- [project:koan] add tests\n\n"
            "## In Progress\n\n"
            "- [project:koan] refactor module\n\n"
            "## Done\n\n"
            "- old task\n"
        )
        from skills.core.status.handler import _handle_status
        ctx = _make_ctx("status", instance, tmp_path)
        result = _handle_status(ctx)
        assert "Pending: 2" in result
        assert "In progress: 1" in result
        # Project tags should be stripped from display
        assert "[project:koan]" not in result
        assert "fix the bug" in result

    def test_no_missions_file(self, tmp_path):
        """If missions.md doesn't exist, still shows status."""
        instance = tmp_path / "instance"
        instance.mkdir()
        from skills.core.status.handler import _handle_status
        ctx = _make_ctx("status", instance, tmp_path)
        result = _handle_status(ctx)
        assert "Koan Status" in result

    def test_empty_missions(self, tmp_path):
        """missions.md with only Done items shows no pending/in-progress."""
        instance = tmp_path / "instance"
        instance.mkdir()
        missions = instance / "missions.md"
        missions.write_text(
            "# Missions\n\n"
            "## Pending\n\n"
            "## In Progress\n\n"
            "## Done\n\n"
            "- old task\n"
        )
        from skills.core.status.handler import _handle_status
        ctx = _make_ctx("status", instance, tmp_path)
        result = _handle_status(ctx)
        assert "Pending" not in result
        assert "In progress" not in result

    def test_multi_project_missions(self, tmp_path):
        """Missions from different projects are grouped separately."""
        instance = tmp_path / "instance"
        instance.mkdir()
        missions = instance / "missions.md"
        missions.write_text(
            "# Missions\n\n"
            "## Pending\n\n"
            "- [project:koan] fix the bug\n"
            "- [project:webapp] update CSS\n\n"
            "## In Progress\n\n"
            "## Done\n"
        )
        from skills.core.status.handler import _handle_status
        ctx = _make_ctx("status", instance, tmp_path)
        result = _handle_status(ctx)
        assert "koan" in result
        assert "webapp" in result


# ---------------------------------------------------------------------------
# _handle_ping()
# ---------------------------------------------------------------------------

class TestHandlePing:
    """Test /ping run loop liveness check."""

    def test_run_loop_alive(self, tmp_path):
        """pgrep succeeds = run loop alive."""
        from skills.core.status.handler import _handle_ping
        ctx = _make_ctx("ping", tmp_path, tmp_path)
        with patch("skills.core.status.handler.subprocess") as mock_sub:
            mock_sub.run.return_value = MagicMock(returncode=0)
            result = _handle_ping(ctx)
        assert "OK" in result

    def test_run_loop_dead(self, tmp_path):
        """pgrep fails = run loop not running."""
        from skills.core.status.handler import _handle_ping
        ctx = _make_ctx("ping", tmp_path, tmp_path)
        with patch("skills.core.status.handler.subprocess") as mock_sub:
            mock_sub.run.return_value = MagicMock(returncode=1)
            result = _handle_ping(ctx)
        assert "not running" in result
        assert "make run" in result

    def test_run_loop_alive_but_paused(self, tmp_path):
        """Run loop alive + pause file = paused status."""
        (tmp_path / ".koan-pause").touch()
        from skills.core.status.handler import _handle_ping
        ctx = _make_ctx("ping", tmp_path, tmp_path)
        with patch("skills.core.status.handler.subprocess") as mock_sub:
            mock_sub.run.return_value = MagicMock(returncode=0)
            result = _handle_ping(ctx)
        assert "paused" in result.lower()
        assert "/resume" in result

    def test_run_loop_alive_but_stopping(self, tmp_path):
        """Run loop alive + stop file = stopping status."""
        (tmp_path / ".koan-stop").touch()
        from skills.core.status.handler import _handle_ping
        ctx = _make_ctx("ping", tmp_path, tmp_path)
        with patch("skills.core.status.handler.subprocess") as mock_sub:
            mock_sub.run.return_value = MagicMock(returncode=0)
            result = _handle_ping(ctx)
        assert "stopping" in result.lower()

    def test_pgrep_exception(self, tmp_path):
        """pgrep throws exception = treated as dead."""
        from skills.core.status.handler import _handle_ping
        ctx = _make_ctx("ping", tmp_path, tmp_path)
        with patch("skills.core.status.handler.subprocess") as mock_sub:
            mock_sub.run.side_effect = OSError("pgrep not found")
            result = _handle_ping(ctx)
        assert "not running" in result

    def test_pgrep_timeout(self, tmp_path):
        """pgrep times out = treated as dead."""
        import subprocess as real_sub
        from skills.core.status.handler import _handle_ping
        ctx = _make_ctx("ping", tmp_path, tmp_path)
        with patch("skills.core.status.handler.subprocess") as mock_sub:
            mock_sub.run.side_effect = real_sub.TimeoutExpired("pgrep", 5)
            result = _handle_ping(ctx)
        assert "not running" in result


# ---------------------------------------------------------------------------
# _handle_usage()
# ---------------------------------------------------------------------------

class TestHandleUsage:
    """Test /usage detailed quota and progress display."""

    def test_no_usage_file(self, tmp_path):
        """No usage.md = fallback message."""
        instance = tmp_path / "instance"
        instance.mkdir()
        from skills.core.status.handler import _handle_usage
        ctx = _make_ctx("usage", instance, tmp_path)
        result = _handle_usage(ctx)
        assert "No quota data" in result

    def test_usage_file_present(self, tmp_path):
        """usage.md content is displayed."""
        instance = tmp_path / "instance"
        instance.mkdir()
        (instance / "usage.md").write_text("Session: 45%\nWeekly: 12%\n")
        from skills.core.status.handler import _handle_usage
        ctx = _make_ctx("usage", instance, tmp_path)
        result = _handle_usage(ctx)
        assert "45%" in result
        assert "Quota" in result

    def test_empty_usage_file(self, tmp_path):
        """Empty usage.md = fallback message."""
        instance = tmp_path / "instance"
        instance.mkdir()
        (instance / "usage.md").write_text("")
        from skills.core.status.handler import _handle_usage
        ctx = _make_ctx("usage", instance, tmp_path)
        result = _handle_usage(ctx)
        assert "No quota data" in result

    def test_missions_in_usage(self, tmp_path):
        """Missions are included in /usage output."""
        instance = tmp_path / "instance"
        instance.mkdir()
        missions = instance / "missions.md"
        missions.write_text(
            "# Missions\n\n"
            "## Pending\n\n"
            "- task one\n"
            "- task two\n\n"
            "## In Progress\n\n"
            "- current task\n\n"
            "## Done\n\n"
            "- done1\n- done2\n- done3\n"
        )
        from skills.core.status.handler import _handle_usage
        ctx = _make_ctx("usage", instance, tmp_path)
        result = _handle_usage(ctx)
        assert "Pending (2)" in result
        assert "In progress" in result
        assert "Done: 3" in result

    def test_no_missions_file_in_usage(self, tmp_path):
        """No missions.md = 'No missions' message."""
        instance = tmp_path / "instance"
        instance.mkdir()
        from skills.core.status.handler import _handle_usage
        ctx = _make_ctx("usage", instance, tmp_path)
        result = _handle_usage(ctx)
        assert "No missions" in result

    def test_pending_md_included(self, tmp_path):
        """Current run progress from pending.md is included."""
        instance = tmp_path / "instance"
        instance.mkdir()
        journal_dir = instance / "journal"
        journal_dir.mkdir()
        pending = journal_dir / "pending.md"
        pending.write_text("# Mission: fix something\n---\n10:00 — Reading code\n10:05 — Writing tests")
        from skills.core.status.handler import _handle_usage
        ctx = _make_ctx("usage", instance, tmp_path)
        result = _handle_usage(ctx)
        assert "fix something" in result
        assert "Current" in result

    def test_no_pending_md(self, tmp_path):
        """No pending.md = 'No run in progress' message."""
        instance = tmp_path / "instance"
        instance.mkdir()
        from skills.core.status.handler import _handle_usage
        ctx = _make_ctx("usage", instance, tmp_path)
        result = _handle_usage(ctx)
        assert "No run in progress" in result

    def test_long_pending_truncated(self, tmp_path):
        """Very long pending.md is truncated."""
        instance = tmp_path / "instance"
        instance.mkdir()
        journal_dir = instance / "journal"
        journal_dir.mkdir()
        pending = journal_dir / "pending.md"
        pending.write_text("x" * 2000)
        from skills.core.status.handler import _handle_usage
        ctx = _make_ctx("usage", instance, tmp_path)
        result = _handle_usage(ctx)
        # Content should be truncated to last 1500 chars
        assert "..." in result
