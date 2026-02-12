"""Tests for loop_manager.py — loop management utilities for the agent loop."""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# --- Test resolve_focus_area ---


class TestResolveFocusArea:
    """Test focus area resolution from autonomous mode."""

    def test_review_mode(self):
        from app.loop_manager import resolve_focus_area

        result = resolve_focus_area("review")
        assert "READ-ONLY" in result
        assert "review" in result.lower()

    def test_implement_mode(self):
        from app.loop_manager import resolve_focus_area

        result = resolve_focus_area("implement")
        assert "implementation" in result.lower()

    def test_deep_mode(self):
        from app.loop_manager import resolve_focus_area

        result = resolve_focus_area("deep")
        assert "deep work" in result.lower()

    def test_unknown_mode_fallback(self):
        from app.loop_manager import resolve_focus_area

        result = resolve_focus_area("unknown")
        assert result == "General autonomous work"

    def test_has_mission_overrides_mode(self):
        from app.loop_manager import resolve_focus_area

        result = resolve_focus_area("deep", has_mission=True)
        assert result == "Execute assigned mission"

    def test_has_mission_with_review(self):
        from app.loop_manager import resolve_focus_area

        result = resolve_focus_area("review", has_mission=True)
        assert result == "Execute assigned mission"

    def test_wait_mode(self):
        from app.loop_manager import resolve_focus_area

        result = resolve_focus_area("wait")
        assert "pause" in result.lower() or "exhausted" in result.lower()


# --- Test validate_projects ---


class TestValidateProjects:
    """Test project configuration validation."""

    def test_valid_projects(self, tmp_path):
        from app.loop_manager import validate_projects

        p1 = tmp_path / "proj1"
        p2 = tmp_path / "proj2"
        p1.mkdir()
        p2.mkdir()

        result = validate_projects([("proj1", str(p1)), ("proj2", str(p2))])
        assert result is None

    def test_empty_projects(self):
        from app.loop_manager import validate_projects

        result = validate_projects([])
        assert result is not None
        assert "No projects" in result

    def test_too_many_projects(self, tmp_path):
        from app.loop_manager import validate_projects

        projects = [(f"p{i}", str(tmp_path)) for i in range(51)]

        result = validate_projects(projects)
        assert result is not None
        assert "Max 50" in result

    def test_custom_max_projects(self, tmp_path):
        from app.loop_manager import validate_projects

        projects = [(f"p{i}", str(tmp_path)) for i in range(3)]

        # With max_projects=2, 3 projects should fail
        result = validate_projects(projects, max_projects=2)
        assert result is not None
        assert "Max 2" in result

    def test_missing_path(self, tmp_path):
        from app.loop_manager import validate_projects

        result = validate_projects([("proj1", "/nonexistent/path/xyz")])
        assert result is not None
        assert "does not exist" in result
        assert "proj1" in result

    def test_single_valid_project(self, tmp_path):
        from app.loop_manager import validate_projects

        result = validate_projects([("koan", str(tmp_path))])
        assert result is None


# --- Test lookup_project ---


class TestLookupProject:
    """Test project name to path lookup."""

    def test_found(self):
        from app.loop_manager import lookup_project

        result = lookup_project("web-app", [("koan", "/a"), ("web-app", "/b")])
        assert result == "/b"

    def test_not_found(self):
        from app.loop_manager import lookup_project

        result = lookup_project("unknown", [("koan", "/a"), ("web-app", "/b")])
        assert result is None

    def test_first_match(self):
        from app.loop_manager import lookup_project

        result = lookup_project("koan", [("koan", "/first"), ("koan", "/second")])
        assert result == "/first"

    def test_empty_list(self):
        from app.loop_manager import lookup_project

        result = lookup_project("anything", [])
        assert result is None


# --- Test format_project_list ---


class TestFormatProjectList:
    """Test project list formatting."""

    def test_sorted_output(self):
        from app.loop_manager import format_project_list

        result = format_project_list([("web-app", "/c"), ("backend", "/a"), ("koan", "/b")])
        lines = result.strip().split("\n")
        assert len(lines) == 3
        assert "backend" in lines[0]
        assert "koan" in lines[1]
        assert "web-app" in lines[2]

    def test_bullet_points(self):
        from app.loop_manager import format_project_list

        result = format_project_list([("proj", "/p")])
        assert "\u2022" in result  # bullet character

    def test_empty_list(self):
        from app.loop_manager import format_project_list

        result = format_project_list([])
        assert result == ""


# --- Test create_pending_file ---


class TestCreatePendingFile:
    """Test pending.md file creation."""

    def test_mission_pending(self, tmp_path):
        from app.loop_manager import create_pending_file

        instance = str(tmp_path / "instance")
        os.makedirs(os.path.join(instance, "journal"), exist_ok=True)

        path = create_pending_file(
            instance_dir=instance,
            project_name="koan",
            run_num=3,
            max_runs=20,
            autonomous_mode="implement",
            mission_title="Fix the bug",
        )

        content = Path(path).read_text()
        assert "# Mission: Fix the bug" in content
        assert "Project: koan" in content
        assert "Run: 3/20" in content
        assert "Mode: implement" in content
        assert "---" in content

    def test_autonomous_pending(self, tmp_path):
        from app.loop_manager import create_pending_file

        instance = str(tmp_path / "instance")
        os.makedirs(os.path.join(instance, "journal"), exist_ok=True)

        path = create_pending_file(
            instance_dir=instance,
            project_name="web-app",
            run_num=1,
            max_runs=25,
            autonomous_mode="deep",
        )

        content = Path(path).read_text()
        assert "# Autonomous run" in content
        assert "Project: web-app" in content
        assert "Run: 1/25" in content
        assert "Mode: deep" in content

    def test_creates_journal_directory(self, tmp_path):
        from app.loop_manager import create_pending_file

        instance = str(tmp_path / "instance")
        os.makedirs(os.path.join(instance, "journal"), exist_ok=True)

        create_pending_file(
            instance_dir=instance,
            project_name="koan",
            run_num=1,
            max_runs=20,
            autonomous_mode="implement",
        )

        # Should have created today's journal directory
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        assert os.path.isdir(os.path.join(instance, "journal", today))

    def test_returns_path(self, tmp_path):
        from app.loop_manager import create_pending_file

        instance = str(tmp_path / "instance")
        os.makedirs(os.path.join(instance, "journal"), exist_ok=True)

        path = create_pending_file(
            instance_dir=instance,
            project_name="koan",
            run_num=1,
            max_runs=20,
            autonomous_mode="implement",
        )

        assert path.endswith("pending.md")
        assert os.path.isfile(path)

    def test_mission_mode_fallback(self, tmp_path):
        from app.loop_manager import create_pending_file

        instance = str(tmp_path / "instance")
        os.makedirs(os.path.join(instance, "journal"), exist_ok=True)

        # Empty autonomous mode with mission title should show "mission"
        path = create_pending_file(
            instance_dir=instance,
            project_name="koan",
            run_num=1,
            max_runs=20,
            autonomous_mode="",
            mission_title="Do stuff",
        )

        content = Path(path).read_text()
        assert "Mode: mission" in content


# --- Test interruptible_sleep ---


class TestInterruptibleSleep:
    """Test interruptible sleep with wake-on-event."""

    def test_timeout(self, tmp_path):
        from app.loop_manager import interruptible_sleep

        koan_root = str(tmp_path / "root")
        instance = str(tmp_path / "instance")
        os.makedirs(koan_root, exist_ok=True)
        os.makedirs(instance, exist_ok=True)

        # Very short interval + check interval — should timeout immediately
        result = interruptible_sleep(
            interval=1,
            koan_root=koan_root,
            instance_dir=instance,
            check_interval=1,
        )
        assert result == "timeout"

    def test_stop_file_wakes(self, tmp_path):
        from app.loop_manager import interruptible_sleep

        koan_root = str(tmp_path / "root")
        instance = str(tmp_path / "instance")
        os.makedirs(koan_root, exist_ok=True)
        os.makedirs(instance, exist_ok=True)

        # Pre-create stop file
        Path(os.path.join(koan_root, ".koan-stop")).touch()

        result = interruptible_sleep(
            interval=60,
            koan_root=koan_root,
            instance_dir=instance,
            check_interval=1,
        )
        assert result == "stop"

    def test_pause_file_wakes(self, tmp_path):
        from app.loop_manager import interruptible_sleep

        koan_root = str(tmp_path / "root")
        instance = str(tmp_path / "instance")
        os.makedirs(koan_root, exist_ok=True)
        os.makedirs(instance, exist_ok=True)

        # Pre-create pause file
        Path(os.path.join(koan_root, ".koan-pause")).touch()

        result = interruptible_sleep(
            interval=60,
            koan_root=koan_root,
            instance_dir=instance,
            check_interval=1,
        )
        assert result == "pause"

    def test_restart_file_wakes(self, tmp_path):
        from app.loop_manager import interruptible_sleep

        koan_root = str(tmp_path / "root")
        instance = str(tmp_path / "instance")
        os.makedirs(koan_root, exist_ok=True)
        os.makedirs(instance, exist_ok=True)

        # Pre-create restart file
        Path(os.path.join(koan_root, ".koan-restart")).touch()

        result = interruptible_sleep(
            interval=60,
            koan_root=koan_root,
            instance_dir=instance,
            check_interval=1,
        )
        assert result == "restart"

    def test_shutdown_file_wakes(self, tmp_path):
        from app.loop_manager import interruptible_sleep

        koan_root = str(tmp_path / "root")
        instance = str(tmp_path / "instance")
        os.makedirs(koan_root, exist_ok=True)
        os.makedirs(instance, exist_ok=True)

        # Pre-create shutdown file
        Path(os.path.join(koan_root, ".koan-shutdown")).touch()

        result = interruptible_sleep(
            interval=60,
            koan_root=koan_root,
            instance_dir=instance,
            check_interval=1,
        )
        assert result == "shutdown"

    def test_mission_wakes(self, tmp_path):
        from app.loop_manager import interruptible_sleep

        koan_root = str(tmp_path / "root")
        instance = str(tmp_path / "instance")
        os.makedirs(koan_root, exist_ok=True)
        os.makedirs(instance, exist_ok=True)

        # Create a missions.md with a pending mission
        missions_md = Path(instance) / "missions.md"
        missions_md.write_text("## Pending\n\n- Fix the bug\n\n## Done\n")

        result = interruptible_sleep(
            interval=60,
            koan_root=koan_root,
            instance_dir=instance,
            check_interval=1,
        )
        assert result == "mission"

    def test_priority_stop_over_pause(self, tmp_path):
        from app.loop_manager import interruptible_sleep

        koan_root = str(tmp_path / "root")
        instance = str(tmp_path / "instance")
        os.makedirs(koan_root, exist_ok=True)
        os.makedirs(instance, exist_ok=True)

        # Both stop and pause — stop is checked first (after mission)
        Path(os.path.join(koan_root, ".koan-stop")).touch()
        Path(os.path.join(koan_root, ".koan-pause")).touch()

        result = interruptible_sleep(
            interval=60,
            koan_root=koan_root,
            instance_dir=instance,
            check_interval=1,
        )
        assert result == "stop"


# --- Test internal helpers ---


class TestCheckHelpers:
    """Test file-checking helper functions."""

    def test_check_signal_file_stop_exists(self, tmp_path):
        from app.loop_manager import _check_signal_file

        Path(tmp_path / ".koan-stop").touch()
        assert _check_signal_file(str(tmp_path), ".koan-stop") is True

    def test_check_signal_file_stop_missing(self, tmp_path):
        from app.loop_manager import _check_signal_file

        assert _check_signal_file(str(tmp_path), ".koan-stop") is False

    def test_check_signal_file_pause_exists(self, tmp_path):
        from app.loop_manager import _check_signal_file

        Path(tmp_path / ".koan-pause").touch()
        assert _check_signal_file(str(tmp_path), ".koan-pause") is True

    def test_check_signal_file_pause_missing(self, tmp_path):
        from app.loop_manager import _check_signal_file

        assert _check_signal_file(str(tmp_path), ".koan-pause") is False

    def test_check_signal_file_shutdown_exists(self, tmp_path):
        from app.loop_manager import _check_signal_file

        Path(tmp_path / ".koan-shutdown").touch()
        assert _check_signal_file(str(tmp_path), ".koan-shutdown") is True

    def test_check_signal_file_shutdown_missing(self, tmp_path):
        from app.loop_manager import _check_signal_file

        assert _check_signal_file(str(tmp_path), ".koan-shutdown") is False

    def test_check_signal_file_restart_exists(self, tmp_path):
        from app.loop_manager import _check_signal_file

        Path(tmp_path / ".koan-restart").touch()
        assert _check_signal_file(str(tmp_path), ".koan-restart") is True

    def test_check_signal_file_restart_missing(self, tmp_path):
        from app.loop_manager import _check_signal_file

        assert _check_signal_file(str(tmp_path), ".koan-restart") is False

    def test_check_pending_missions_with_missions(self, tmp_path):
        from app.loop_manager import check_pending_missions

        missions = tmp_path / "missions.md"
        missions.write_text("## Pending\n\n- Do something\n\n## Done\n")

        assert check_pending_missions(str(tmp_path)) is True

    def test_check_pending_missions_empty(self, tmp_path):
        from app.loop_manager import check_pending_missions

        missions = tmp_path / "missions.md"
        missions.write_text("## Pending\n\n## Done\n")

        assert check_pending_missions(str(tmp_path)) is False

    def test_check_pending_missions_no_file(self, tmp_path):
        from app.loop_manager import check_pending_missions

        assert check_pending_missions(str(tmp_path)) is False


# --- Test GitHub notification backoff ---


class TestGitHubNotificationBackoff:
    """Test exponential backoff for GitHub notification polling."""

    def setup_method(self):
        """Reset backoff state before each test."""
        from app.loop_manager import reset_github_backoff
        reset_github_backoff()

    def test_effective_interval_starts_at_base(self):
        from app.loop_manager import _get_effective_check_interval, _GITHUB_CHECK_INTERVAL
        assert _get_effective_check_interval() == _GITHUB_CHECK_INTERVAL

    def test_effective_interval_doubles_on_empty(self):
        import app.loop_manager as lm
        lm._consecutive_empty_checks = 1
        assert lm._get_effective_check_interval() == 120
        lm._consecutive_empty_checks = 2
        assert lm._get_effective_check_interval() == 240
        lm._consecutive_empty_checks = 3
        assert lm._get_effective_check_interval() == 300  # capped

    def test_effective_interval_capped_at_max(self):
        import app.loop_manager as lm
        lm._consecutive_empty_checks = 10
        assert lm._get_effective_check_interval() == lm._GITHUB_MAX_CHECK_INTERVAL

    def test_reset_clears_state(self):
        import app.loop_manager as lm
        lm._consecutive_empty_checks = 5
        lm._last_github_check = 999.0
        lm.reset_github_backoff()
        assert lm._consecutive_empty_checks == 0
        assert lm._last_github_check == 0

    @patch("app.loop_manager._load_github_config")
    @patch("app.loop_manager._build_skill_registry")
    @patch("app.loop_manager._get_known_repos_from_projects")
    @patch("app.utils.load_config")
    def test_empty_notifications_increments_backoff(
        self, mock_config, mock_repos, mock_registry, mock_gh_config, tmp_path
    ):
        import app.loop_manager as lm
        from app.loop_manager import process_github_notifications

        mock_config.return_value = {}
        mock_gh_config.return_value = {"bot_username": "bot", "max_age": 300}
        mock_registry.return_value = MagicMock()
        mock_repos.return_value = set()

        with patch("app.projects_config.load_projects_config", return_value={}), \
             patch("app.github_notifications.fetch_unread_notifications", return_value=[]):
            result = process_github_notifications(str(tmp_path), str(tmp_path))

        assert result == 0
        assert lm._consecutive_empty_checks == 1

    @patch("app.loop_manager._load_github_config")
    @patch("app.loop_manager._build_skill_registry")
    @patch("app.loop_manager._get_known_repos_from_projects")
    @patch("app.utils.load_config")
    def test_found_notifications_resets_backoff(
        self, mock_config, mock_repos, mock_registry, mock_gh_config, tmp_path
    ):
        import app.loop_manager as lm
        from app.loop_manager import process_github_notifications

        lm._consecutive_empty_checks = 3  # simulate previous backoff

        mock_config.return_value = {}
        mock_gh_config.return_value = {"bot_username": "bot", "max_age": 300}
        mock_registry.return_value = MagicMock()
        mock_repos.return_value = set()

        fake_notif = {"id": "1", "subject": {"url": "https://api.github.com/repos/o/r/issues/1"}}
        with patch("app.projects_config.load_projects_config", return_value={}), \
             patch("app.github_notifications.fetch_unread_notifications", return_value=[fake_notif]), \
             patch("app.github_command_handler.process_single_notification", return_value=(True, None)):
            result = process_github_notifications(str(tmp_path), str(tmp_path))

        assert result == 1
        assert lm._consecutive_empty_checks == 0

    @patch("app.loop_manager._load_github_config")
    @patch("app.loop_manager._build_skill_registry")
    @patch("app.loop_manager._get_known_repos_from_projects")
    @patch("app.utils.load_config")
    def test_backoff_throttles_subsequent_checks(
        self, mock_config, mock_repos, mock_registry, mock_gh_config, tmp_path
    ):
        import app.loop_manager as lm
        from app.loop_manager import process_github_notifications

        mock_config.return_value = {}
        mock_gh_config.return_value = {"bot_username": "bot", "max_age": 300}
        mock_registry.return_value = MagicMock()
        mock_repos.return_value = set()

        # First call: succeeds, sets backoff
        with patch("app.projects_config.load_projects_config", return_value={}), \
             patch("app.github_notifications.fetch_unread_notifications", return_value=[]):
            process_github_notifications(str(tmp_path), str(tmp_path))

        assert lm._consecutive_empty_checks == 1
        # Effective interval is now 120s

        # Second call immediately after: should be throttled (last check was just now)
        result = process_github_notifications(str(tmp_path), str(tmp_path))
        assert result == 0
        # Counter stays at 1 (throttled, didn't actually check)
        assert lm._consecutive_empty_checks == 1

    @patch("app.loop_manager._load_github_config")
    @patch("app.loop_manager._build_skill_registry")
    @patch("app.loop_manager._get_known_repos_from_projects")
    @patch("app.utils.load_config")
    def test_consecutive_empty_checks_accumulate(
        self, mock_config, mock_repos, mock_registry, mock_gh_config, tmp_path
    ):
        import app.loop_manager as lm
        from app.loop_manager import process_github_notifications

        mock_config.return_value = {}
        mock_gh_config.return_value = {"bot_username": "bot", "max_age": 300}
        mock_registry.return_value = MagicMock()
        mock_repos.return_value = set()

        # Simulate multiple checks by resetting last_check each time
        for i in range(4):
            lm._last_github_check = 0  # force past throttle
            with patch("app.projects_config.load_projects_config", return_value={}), \
                 patch("app.github_notifications.fetch_unread_notifications", return_value=[]):
                process_github_notifications(str(tmp_path), str(tmp_path))

        assert lm._consecutive_empty_checks == 4
        # After 4 empty: 60 * 2^4 = 960 → capped at 300
        assert lm._get_effective_check_interval() == 300

    def test_config_disabled_does_not_affect_backoff(self, tmp_path):
        import app.loop_manager as lm
        from app.loop_manager import process_github_notifications

        lm._consecutive_empty_checks = 2

        with patch("app.utils.load_config", return_value={}), \
             patch("app.loop_manager._load_github_config", return_value=None):
            result = process_github_notifications(str(tmp_path), str(tmp_path))

        assert result == 0
        # Config disabled = early return, backoff unchanged
        assert lm._consecutive_empty_checks == 2

    @patch("app.loop_manager._load_github_config")
    @patch("app.loop_manager._build_skill_registry")
    @patch("app.loop_manager._get_known_repos_from_projects")
    @patch("app.utils.load_config")
    def test_notifications_with_no_missions_still_resets(
        self, mock_config, mock_repos, mock_registry, mock_gh_config, tmp_path
    ):
        """Notifications present but all fail to create missions — still resets backoff."""
        import app.loop_manager as lm
        from app.loop_manager import process_github_notifications

        lm._consecutive_empty_checks = 5

        mock_config.return_value = {}
        mock_gh_config.return_value = {"bot_username": "bot", "max_age": 300}
        mock_registry.return_value = MagicMock()
        mock_repos.return_value = set()

        fake_notif = {"id": "1", "subject": {"url": "https://api.github.com/repos/o/r/issues/1"}}
        with patch("app.projects_config.load_projects_config", return_value={}), \
             patch("app.github_notifications.fetch_unread_notifications", return_value=[fake_notif]), \
             patch("app.github_command_handler.process_single_notification", return_value=(False, "error")), \
             patch("app.loop_manager._post_error_for_notification"):
            result = process_github_notifications(str(tmp_path), str(tmp_path))

        assert result == 0
        # Notifications were present (non-empty list), so backoff resets
        assert lm._consecutive_empty_checks == 0

    def test_exception_does_not_reset_backoff(self, tmp_path):
        """Exception during check should not touch backoff state."""
        import app.loop_manager as lm
        from app.loop_manager import process_github_notifications

        lm._consecutive_empty_checks = 3

        with patch("app.utils.load_config", side_effect=RuntimeError("boom")):
            result = process_github_notifications(str(tmp_path), str(tmp_path))

        assert result == 0
        assert lm._consecutive_empty_checks == 3


# --- Test CLI interface ---


class TestCLI:
    """Test CLI interface."""

    def test_resolve_focus_cli(self):
        result = subprocess.run(
            [sys.executable, "-m", "app.loop_manager", "resolve-focus",
             "--mode", "deep"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 0
        assert "deep work" in result.stdout.lower()

    def test_resolve_focus_with_mission(self):
        result = subprocess.run(
            [sys.executable, "-m", "app.loop_manager", "resolve-focus",
             "--mode", "review", "--has-mission"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 0
        assert "Execute assigned mission" in result.stdout

    def test_create_pending_cli(self, tmp_path):
        instance = tmp_path / "instance"
        instance.mkdir()
        (instance / "journal").mkdir()

        result = subprocess.run(
            [sys.executable, "-m", "app.loop_manager", "create-pending",
             "--instance", str(instance),
             "--project-name", "koan",
             "--run-num", "5",
             "--max-runs", "20",
             "--autonomous-mode", "deep",
             "--mission-title", "Test mission"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 0
        assert "pending.md" in result.stdout

        # Verify the file was created
        pending = instance / "journal" / "pending.md"
        assert pending.exists()
        content = pending.read_text()
        assert "# Mission: Test mission" in content

    def test_unknown_subcommand(self):
        result = subprocess.run(
            [sys.executable, "-m", "app.loop_manager", "unknown-cmd"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode != 0

    def test_no_subcommand(self):
        result = subprocess.run(
            [sys.executable, "-m", "app.loop_manager"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode != 0

    def test_interruptible_sleep_cli(self, tmp_path):
        """Test CLI with very short interval — should timeout quickly."""
        koan_root = tmp_path / "root"
        instance = tmp_path / "instance"
        koan_root.mkdir()
        instance.mkdir()

        result = subprocess.run(
            [sys.executable, "-m", "app.loop_manager", "interruptible-sleep",
             "--interval", "1",
             "--koan-root", str(koan_root),
             "--instance", str(instance),
             "--check-interval", "1"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).parent.parent),
            timeout=10,
        )
        assert result.returncode == 0
        assert "timeout" in result.stdout.strip()

    def test_validate_projects_cli(self, tmp_path, monkeypatch):
        """Test validate-projects CLI."""
        proj = tmp_path / "myproj"
        proj.mkdir()
        monkeypatch.setenv("KOAN_PROJECTS", f"myproj:{proj}")

        result = subprocess.run(
            [sys.executable, "-m", "app.loop_manager", "validate-projects"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).parent.parent),
            env={**os.environ, "KOAN_PROJECTS": f"myproj:{proj}"},
        )
        assert result.returncode == 0
        assert "myproj" in result.stdout

    def test_lookup_project_cli(self, tmp_path):
        """Test lookup-project CLI."""
        proj = tmp_path / "koan"
        proj.mkdir()

        result = subprocess.run(
            [sys.executable, "-m", "app.loop_manager", "lookup-project",
             "--name", "koan"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).parent.parent),
            env={**os.environ, "KOAN_PROJECTS": f"koan:{proj}"},
        )
        assert result.returncode == 0
        assert str(proj) in result.stdout

    def test_lookup_project_not_found(self, tmp_path):
        """Test lookup-project CLI with unknown project."""
        proj = tmp_path / "koan"
        proj.mkdir()

        result = subprocess.run(
            [sys.executable, "-m", "app.loop_manager", "lookup-project",
             "--name", "unknown"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).parent.parent),
            env={**os.environ, "KOAN_PROJECTS": f"koan:{proj}"},
        )
        assert result.returncode != 0
        assert "Unknown project" in result.stderr
