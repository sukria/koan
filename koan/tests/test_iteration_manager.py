"""Tests for app.iteration_manager — per-iteration planning."""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("KOAN_ROOT", "/tmp/test-koan")

from app.iteration_manager import (
    _check_focus,
    _check_schedule,
    _get_known_project_names,
    _get_project_by_index,
    _get_usage_decision,
    _inject_recurring,
    _pick_mission,
    _refresh_usage,
    _resolve_focus_area,
    _resolve_project_path,
    _should_contemplate,
    plan_iteration,
)


# === Helper fixtures ===


@pytest.fixture
def instance_dir(tmp_path):
    """Create a minimal instance directory."""
    inst = tmp_path / "instance"
    inst.mkdir()
    (inst / "journal").mkdir()
    (inst / "memory" / "global").mkdir(parents=True)
    (inst / "memory" / "projects").mkdir(parents=True)
    return inst


@pytest.fixture
def koan_root(tmp_path):
    """Create a KOAN_ROOT directory."""
    root = tmp_path / "koan-root"
    root.mkdir()
    return root


@pytest.fixture
def usage_state(tmp_path):
    """Create a usage state file path."""
    return tmp_path / "usage_state.json"


PROJECTS_STR = "koan:/path/to/koan;backend:/path/to/backend;webapp:/path/to/webapp"


# === Tests: _resolve_project_path ===


class TestResolveProjectPath:

    def test_finds_existing_project(self):
        assert _resolve_project_path("koan", PROJECTS_STR) == "/path/to/koan"
        assert _resolve_project_path("backend", PROJECTS_STR) == "/path/to/backend"
        assert _resolve_project_path("webapp", PROJECTS_STR) == "/path/to/webapp"

    def test_returns_none_for_unknown(self):
        assert _resolve_project_path("unknown", PROJECTS_STR) is None

    def test_empty_projects_string(self):
        assert _resolve_project_path("koan", "") is None

    def test_single_project(self):
        assert _resolve_project_path("only", "only:/single/path") == "/single/path"

    def test_handles_whitespace(self):
        assert _resolve_project_path("proj", "  proj : /the/path  ") == "/the/path"


class TestGetProjectByIndex:

    def test_first_project(self):
        name, path = _get_project_by_index(PROJECTS_STR, 0)
        assert name == "koan"
        assert path == "/path/to/koan"

    def test_second_project(self):
        name, path = _get_project_by_index(PROJECTS_STR, 1)
        assert name == "backend"
        assert path == "/path/to/backend"

    def test_index_clamped_high(self):
        name, path = _get_project_by_index(PROJECTS_STR, 99)
        assert name == "webapp"  # Last project

    def test_index_clamped_low(self):
        name, path = _get_project_by_index(PROJECTS_STR, -1)
        assert name == "koan"  # First project

    def test_empty_projects(self):
        name, path = _get_project_by_index("", 0)
        assert name == "default"


class TestGetKnownProjectNames:

    def test_extracts_sorted_names(self):
        names = _get_known_project_names(PROJECTS_STR)
        assert names == ["backend", "koan", "webapp"]

    def test_single_project(self):
        names = _get_known_project_names("solo:/path")
        assert names == ["solo"]

    def test_empty_string(self):
        names = _get_known_project_names("")
        assert names == []


# === Tests: _resolve_focus_area ===


class TestResolveFocusArea:

    def test_mission_mode(self):
        assert _resolve_focus_area("deep", has_mission=True) == "Execute assigned mission"

    def test_review_mode(self):
        result = _resolve_focus_area("review", has_mission=False)
        assert "review" in result.lower() or "READ-ONLY" in result

    def test_implement_mode(self):
        result = _resolve_focus_area("implement", has_mission=False)
        assert "implementation" in result.lower() or "implement" in result.lower()

    def test_deep_mode(self):
        result = _resolve_focus_area("deep", has_mission=False)
        assert "deep" in result.lower() or "refactoring" in result.lower()

    def test_wait_mode(self):
        result = _resolve_focus_area("wait", has_mission=False)
        assert "pause" in result.lower() or "exhausted" in result.lower()

    def test_unknown_mode(self):
        result = _resolve_focus_area("unknown", has_mission=False)
        assert "General" in result


# === Tests: _refresh_usage ===


class TestRefreshUsage:

    def test_skips_on_first_run(self):
        """Count=0 means first run — don't refresh."""
        with patch("app.iteration_manager.sys") as _:
            # Should not call cmd_refresh when count=0
            _refresh_usage(Path("/fake/state"), Path("/fake/usage.md"), count=0)

    @patch("app.usage_estimator.cmd_refresh")
    def test_calls_refresh_after_first_run(self, mock_refresh, tmp_path):
        state = tmp_path / "usage_state.json"
        usage_md = tmp_path / "usage.md"
        _refresh_usage(state, usage_md, count=1)
        mock_refresh.assert_called_once_with(state, usage_md)

    def test_handles_refresh_error_gracefully(self, tmp_path):
        """Errors in refresh don't crash the iteration."""
        with patch("app.usage_estimator.cmd_refresh", side_effect=Exception("boom")):
            # Should not raise
            _refresh_usage(tmp_path / "state", tmp_path / "usage.md", count=1)


# === Tests: _get_usage_decision ===


class TestGetUsageDecision:

    def test_returns_fallback_on_missing_file(self, tmp_path):
        result = _get_usage_decision(tmp_path / "nonexistent.md", 0, PROJECTS_STR)
        assert result["mode"] in ("wait", "review", "implement", "deep")
        assert isinstance(result["available_pct"], int)
        assert isinstance(result["display_lines"], list)

    def test_parses_usage_file(self, tmp_path):
        usage_md = tmp_path / "usage.md"
        usage_md.write_text(
            "Session (5hr) : 30% (reset in 2h30m)\n"
            "Weekly (7 day) : 20% (Resets in 5d)\n"
        )
        result = _get_usage_decision(usage_md, 3, PROJECTS_STR)
        assert result["mode"] == "deep"  # 70% available (100-30-safety)
        assert result["available_pct"] >= 50
        assert len(result["display_lines"]) == 2
        assert "Session" in result["display_lines"][0]
        assert "Weekly" in result["display_lines"][1]

    def test_high_usage_returns_wait(self, tmp_path):
        usage_md = tmp_path / "usage.md"
        usage_md.write_text(
            "Session (5hr) : 97% (reset in 1h)\n"
            "Weekly (7 day) : 50% (Resets in 3d)\n"
        )
        result = _get_usage_decision(usage_md, 5, PROJECTS_STR)
        assert result["mode"] == "wait"

    def test_medium_usage_returns_implement(self, tmp_path):
        usage_md = tmp_path / "usage.md"
        usage_md.write_text(
            "Session (5hr) : 60% (reset in 2h)\n"
            "Weekly (7 day) : 40% (Resets in 4d)\n"
        )
        result = _get_usage_decision(usage_md, 3, PROJECTS_STR)
        assert result["mode"] == "implement"  # 30% available


# === Tests: _inject_recurring ===


class TestInjectRecurring:

    def test_returns_empty_when_no_recurring_file(self, instance_dir):
        result = _inject_recurring(instance_dir)
        assert result == []

    @patch("app.recurring.check_and_inject", return_value=["test daily task"])
    def test_returns_injected_descriptions(self, mock_inject, instance_dir):
        (instance_dir / "recurring.json").write_text("{}")
        result = _inject_recurring(instance_dir)
        assert result == ["test daily task"]

    def test_handles_error_gracefully(self, instance_dir):
        (instance_dir / "recurring.json").write_text("{}")
        with patch("app.recurring.check_and_inject", side_effect=Exception("boom")):
            result = _inject_recurring(instance_dir)
            assert result == []


# === Tests: _pick_mission ===


class TestPickMission:

    @patch("app.pick_mission.pick_mission", return_value="koan:Fix the bug")
    def test_returns_project_and_title(self, mock_pick):
        project, title = _pick_mission(Path("/instance"), PROJECTS_STR, 1, "deep", "")
        assert project == "koan"
        assert title == "Fix the bug"

    @patch("app.pick_mission.pick_mission", return_value="")
    def test_returns_none_for_autonomous(self, mock_pick):
        project, title = _pick_mission(Path("/instance"), PROJECTS_STR, 1, "deep", "")
        assert project is None
        assert title is None

    @patch("app.pick_mission.pick_mission", side_effect=Exception("boom"))
    def test_handles_error_gracefully(self, mock_pick):
        project, title = _pick_mission(Path("/instance"), PROJECTS_STR, 1, "deep", "")
        assert project is None
        assert title is None

    @patch("app.pick_mission.pick_mission", return_value="backend:Deploy v2.1")
    def test_parses_colon_in_title(self, mock_pick):
        project, title = _pick_mission(Path("/instance"), PROJECTS_STR, 1, "deep", "")
        assert project == "backend"
        assert title == "Deploy v2.1"


# === Tests: _should_contemplate ===


class TestShouldContemplate:

    @patch("random.randint", return_value=5)
    def test_contemplates_when_roll_succeeds(self, mock_rand):
        assert _should_contemplate("deep", False, 10) is True

    @patch("random.randint", return_value=15)
    def test_skips_when_roll_fails(self, mock_rand):
        assert _should_contemplate("deep", False, 10) is False

    def test_skips_in_wait_mode(self):
        assert _should_contemplate("wait", False, 10) is False

    def test_skips_in_review_mode(self):
        assert _should_contemplate("review", False, 10) is False

    def test_skips_when_focus_active(self):
        assert _should_contemplate("deep", True, 50) is False

    @patch("random.randint", return_value=5)
    def test_schedule_deep_hours_boosts_chance(self, mock_rand):
        """During deep hours, contemplative chance is tripled."""
        from app.schedule_manager import ScheduleState
        schedule = ScheduleState(in_deep_hours=True, in_work_hours=False)
        # base chance 10 → adjusted to 30, roll of 5 < 30 → True
        assert _should_contemplate("deep", False, 10, schedule) is True

    @patch("random.randint", return_value=5)
    def test_schedule_work_hours_zeroes_chance(self, mock_rand):
        """During work hours, contemplative chance is zero."""
        from app.schedule_manager import ScheduleState
        schedule = ScheduleState(in_deep_hours=False, in_work_hours=True)
        # chance becomes 0, roll of 5 >= 0 → False
        assert _should_contemplate("deep", False, 10, schedule) is False

    @patch("random.randint", return_value=5)
    def test_schedule_none_unchanged(self, mock_rand):
        """When schedule_state is None, chance is unchanged."""
        # base chance 10, roll of 5 < 10 → True
        assert _should_contemplate("deep", False, 10, None) is True


# === Tests: _check_focus ===


class TestCheckFocus:

    def test_returns_none_when_module_missing(self):
        """When focus_manager isn't available, returns None gracefully."""
        # _check_focus has try/except — if focus_manager doesn't exist, returns None
        with patch.dict("sys.modules", {"app.focus_manager": None}):
            assert _check_focus("/koan-root") is None

    def test_returns_none_when_not_active(self):
        """When focus_manager's check_focus returns None, so does _check_focus."""
        mock_module = MagicMock()
        mock_module.check_focus.return_value = None
        with patch.dict("sys.modules", {"app.focus_manager": mock_module}):
            assert _check_focus("/koan-root") is None

    def test_returns_state_when_active(self):
        """When focus_manager's check_focus returns a state, _check_focus returns it."""
        mock_state = MagicMock()
        mock_module = MagicMock()
        mock_module.check_focus.return_value = mock_state
        with patch.dict("sys.modules", {"app.focus_manager": mock_module}):
            assert _check_focus("/koan-root") is mock_state


# === Tests: _check_schedule ===


class TestCheckSchedule:

    def test_returns_state_when_configured(self):
        """Returns a ScheduleState when schedule is configured."""
        from app.schedule_manager import ScheduleState
        mock_state = ScheduleState(in_deep_hours=True, in_work_hours=False)
        with patch("app.schedule_manager.get_current_schedule", return_value=mock_state):
            result = _check_schedule()
            assert result is not None
            assert result.mode == "deep"

    def test_returns_normal_state_when_unconfigured(self):
        """Returns state (normal) when schedule has no windows configured."""
        from app.schedule_manager import ScheduleState
        mock_state = ScheduleState(in_deep_hours=False, in_work_hours=False)
        with patch("app.schedule_manager.get_current_schedule", return_value=mock_state):
            result = _check_schedule()
            assert result is not None
            assert result.mode == "normal"

    def test_returns_none_on_import_error(self):
        """Returns None gracefully when module is unavailable."""
        with patch("app.schedule_manager.get_current_schedule", side_effect=ImportError):
            result = _check_schedule()
            assert result is None


# === Tests: plan_iteration (integration) ===


class TestPlanIteration:

    @patch("app.pick_mission.pick_mission", return_value="koan:Fix auth bug")
    @patch("app.usage_estimator.cmd_refresh")
    def test_mission_mode(self, mock_refresh, mock_pick, instance_dir, koan_root, usage_state):
        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects_str=PROJECTS_STR,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "mission"
        assert result["project_name"] == "koan"
        assert result["project_path"] == "/path/to/koan"
        assert result["mission_title"] == "Fix auth bug"
        assert result["error"] is None

    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("random.randint", return_value=99)  # No contemplation
    def test_autonomous_mode(self, mock_rand, mock_focus, mock_refresh, mock_pick,
                             instance_dir, koan_root, usage_state):
        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects_str=PROJECTS_STR,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "autonomous"
        assert result["mission_title"] == ""
        assert result["autonomous_mode"] == "deep"
        assert result["error"] is None

    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("random.randint", return_value=3)  # Contemplation triggers (< 10%)
    def test_contemplative_mode(self, mock_rand, mock_focus, mock_refresh, mock_pick,
                                instance_dir, koan_root, usage_state):
        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects_str=PROJECTS_STR,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "contemplative"

    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._check_focus")
    def test_focus_wait_mode(self, mock_focus, mock_refresh, mock_pick,
                             instance_dir, koan_root, usage_state):
        mock_state = MagicMock()
        mock_state.remaining_display.return_value = "2h remaining"
        mock_focus.return_value = mock_state

        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects_str=PROJECTS_STR,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "focus_wait"
        assert result["focus_remaining"] == "2h remaining"

    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("app.iteration_manager._check_schedule")
    @patch("random.randint", return_value=99)  # No contemplation
    def test_schedule_wait_mode(self, mock_rand, mock_schedule, mock_focus,
                                mock_refresh, mock_pick,
                                instance_dir, koan_root, usage_state):
        """When work_hours are active and no mission, returns schedule_wait."""
        from app.schedule_manager import ScheduleState
        mock_schedule.return_value = ScheduleState(in_deep_hours=False, in_work_hours=True)

        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects_str=PROJECTS_STR,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "schedule_wait"
        assert result["schedule_mode"] == "work"

    @patch("app.pick_mission.pick_mission", return_value="koan:Fix auth bug")
    @patch("app.usage_estimator.cmd_refresh")
    def test_schedule_does_not_block_missions(self, mock_refresh, mock_pick,
                                              instance_dir, koan_root, usage_state):
        """Work hours schedule doesn't block queued missions."""
        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        # Even though work hours would suppress exploration, missions still run
        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects_str=PROJECTS_STR,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "mission"
        assert result["mission_title"] == "Fix auth bug"

    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._check_focus", return_value=None)
    def test_wait_pause_mode(self, mock_focus, mock_refresh, mock_pick,
                             instance_dir, koan_root, usage_state):
        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 97% (reset in 1h)\nWeekly (7 day) : 50% (Resets in 3d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=5,
            count=4,
            projects_str=PROJECTS_STR,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "wait_pause"
        assert result["autonomous_mode"] == "wait"

    @patch("app.pick_mission.pick_mission", return_value="unknown_project:Fix thing")
    @patch("app.usage_estimator.cmd_refresh")
    def test_unknown_project_error(self, mock_refresh, mock_pick,
                                   instance_dir, koan_root, usage_state):
        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects_str=PROJECTS_STR,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["action"] == "error"
        assert "unknown_project" in result["error"]
        assert "backend" in result["error"]
        assert "koan" in result["error"]

    @patch("app.pick_mission.pick_mission", return_value="koan:Fix it")
    @patch("app.usage_estimator.cmd_refresh")
    def test_first_run_skips_usage_refresh(self, mock_refresh, mock_pick,
                                           instance_dir, koan_root, usage_state):
        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=1,
            count=0,
            projects_str=PROJECTS_STR,
            last_project="",
            usage_state_path=str(usage_state),
        )

        mock_refresh.assert_not_called()

    @patch("app.pick_mission.pick_mission", return_value="koan:Fix it")
    @patch("app.usage_estimator.cmd_refresh")
    def test_recurring_injection_runs(self, mock_refresh, mock_pick,
                                      instance_dir, koan_root, usage_state):
        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        # Create a recurring.json to trigger injection
        (instance_dir / "recurring.json").write_text("{}")

        with patch("app.recurring.check_and_inject", return_value=["daily: health check"]) as mock_inject:
            result = plan_iteration(
                instance_dir=str(instance_dir),
                koan_root=str(koan_root),
                run_num=2,
                count=1,
                projects_str=PROJECTS_STR,
                last_project="koan",
                usage_state_path=str(usage_state),
            )

        assert result["recurring_injected"] == ["daily: health check"]
        mock_inject.assert_called_once()

    @patch("app.pick_mission.pick_mission", return_value="koan:Task with: colon in title")
    @patch("app.usage_estimator.cmd_refresh")
    def test_mission_title_with_colon(self, mock_refresh, mock_pick,
                                      instance_dir, koan_root, usage_state):
        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        result = plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects_str=PROJECTS_STR,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        assert result["mission_title"] == "Task with: colon in title"

    @patch("app.pick_mission.pick_mission", return_value="")
    @patch("app.usage_estimator.cmd_refresh")
    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("random.randint", return_value=99)
    def test_focus_checked_once_for_autonomous(self, mock_rand, mock_focus,
                                               mock_refresh, mock_pick,
                                               instance_dir, koan_root, usage_state):
        """Focus is checked exactly once (not twice for contemplate + focus_wait)."""
        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        plan_iteration(
            instance_dir=str(instance_dir),
            koan_root=str(koan_root),
            run_num=2,
            count=1,
            projects_str=PROJECTS_STR,
            last_project="koan",
            usage_state_path=str(usage_state),
        )

        mock_focus.assert_called_once()


# === Tests: CLI interface ===


class TestCLI:

    def test_cli_outputs_valid_json(self, instance_dir, koan_root, usage_state):
        """CLI produces valid JSON output (autonomous mode when no missions)."""
        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        result = subprocess.run(
            [
                sys.executable, "-m", "app.iteration_manager",
                "plan-iteration",
                "--instance", str(instance_dir),
                "--koan-root", str(koan_root),
                "--run-num", "2",
                "--count", "1",
                "--projects", PROJECTS_STR,
                "--last-project", "koan",
                "--usage-state", str(usage_state),
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "KOAN_ROOT": str(koan_root), "PYTHONPATH": str(Path(__file__).parent.parent)},
        )

        assert result.returncode == 0
        data = json.loads(result.stdout)
        # With no missions.md, should be autonomous
        assert data["action"] in ("autonomous", "contemplative")
        assert data["autonomous_mode"] in ("wait", "review", "implement", "deep")
        assert isinstance(data["available_pct"], int)
        assert isinstance(data["display_lines"], list)
        assert data["error"] is None

    def test_cli_with_mission(self, instance_dir, koan_root, usage_state):
        """CLI picks up a mission from missions.md (fallback picker, no Claude)."""
        usage_md = instance_dir / "usage.md"
        usage_md.write_text("Session (5hr) : 30% (reset in 3h)\nWeekly (7 day) : 20% (Resets in 5d)\n")

        # Create missions.md with a pending mission
        missions_md = instance_dir / "missions.md"
        missions_md.write_text(
            "# Missions\n\n## En attente\n\n"
            "- [project:koan] Fix the test CLI\n\n"
            "## En cours\n\n## Terminées\n"
        )

        result = subprocess.run(
            [
                sys.executable, "-m", "app.iteration_manager",
                "plan-iteration",
                "--instance", str(instance_dir),
                "--koan-root", str(koan_root),
                "--run-num", "1",
                "--count", "0",
                "--projects", PROJECTS_STR,
                "--last-project", "",
                "--usage-state", str(usage_state),
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "KOAN_ROOT": str(koan_root), "PYTHONPATH": str(Path(__file__).parent.parent)},
        )

        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["action"] == "mission"
        assert data["project_name"] == "koan"
        assert "Fix the test CLI" in data["mission_title"]
