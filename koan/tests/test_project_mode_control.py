"""Tests for per-project autonomous mode control (issue #263).

Covers:
- Mode field validation in projects_config
- get_project_mode_config() accessor
- resolve_mode_with_overrides() in usage_tracker
- Integration with plan_iteration() in iteration_manager
"""

import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Validation tests (projects_config._validate_mode_fields)
# ---------------------------------------------------------------------------

class TestModeFieldValidation:
    """Test mode/max_mode/min_mode validation in projects.yaml schema."""

    def test_valid_mode_values(self):
        from app.projects_config import _validate_mode_fields
        for mode in ("review", "implement", "deep"):
            _validate_mode_fields({"mode": mode}, "test")

    def test_valid_max_mode(self):
        from app.projects_config import _validate_mode_fields
        _validate_mode_fields({"max_mode": "implement"}, "test")

    def test_valid_min_mode(self):
        from app.projects_config import _validate_mode_fields
        _validate_mode_fields({"min_mode": "review"}, "test")

    def test_case_insensitive_validation(self):
        from app.projects_config import _validate_mode_fields
        _validate_mode_fields({"mode": "REVIEW"}, "test")
        _validate_mode_fields({"mode": "Deep"}, "test")

    def test_invalid_mode_rejected(self):
        from app.projects_config import _validate_mode_fields
        with pytest.raises(ValueError, match="invalid mode"):
            _validate_mode_fields({"mode": "wait"}, "test")

    def test_invalid_mode_unknown_value(self):
        from app.projects_config import _validate_mode_fields
        with pytest.raises(ValueError, match="invalid mode"):
            _validate_mode_fields({"mode": "turbo"}, "test")

    def test_invalid_max_mode(self):
        from app.projects_config import _validate_mode_fields
        with pytest.raises(ValueError, match="invalid max_mode"):
            _validate_mode_fields({"max_mode": "wait"}, "test")

    def test_invalid_min_mode(self):
        from app.projects_config import _validate_mode_fields
        with pytest.raises(ValueError, match="invalid min_mode"):
            _validate_mode_fields({"min_mode": "nope"}, "test")

    def test_min_higher_than_max_rejected(self):
        from app.projects_config import _validate_mode_fields
        with pytest.raises(ValueError, match="min_mode.*higher than max_mode"):
            _validate_mode_fields({"min_mode": "deep", "max_mode": "review"}, "test")

    def test_min_equals_max_allowed(self):
        from app.projects_config import _validate_mode_fields
        _validate_mode_fields({"min_mode": "implement", "max_mode": "implement"}, "test")

    def test_min_lower_than_max_allowed(self):
        from app.projects_config import _validate_mode_fields
        _validate_mode_fields({"min_mode": "review", "max_mode": "deep"}, "test")

    def test_none_values_accepted(self):
        from app.projects_config import _validate_mode_fields
        _validate_mode_fields({"mode": None, "max_mode": None, "min_mode": None}, "test")

    def test_empty_section_passes(self):
        from app.projects_config import _validate_mode_fields
        _validate_mode_fields({}, "test")

    def test_integer_mode_rejected(self):
        from app.projects_config import _validate_mode_fields
        with pytest.raises(ValueError, match="invalid mode"):
            _validate_mode_fields({"mode": 42}, "test")


class TestLoadProjectsConfigModeValidation:
    """Integration: mode fields are validated during config load."""

    def test_invalid_mode_in_defaults_raises(self, tmp_path):
        import yaml
        config = {
            "defaults": {"mode": "wait"},
            "projects": {"myapp": {"path": str(tmp_path)}},
        }
        config_file = tmp_path / "projects.yaml"
        config_file.write_text(yaml.dump(config))

        from app.projects_config import load_projects_config
        with pytest.raises(ValueError, match="invalid mode"):
            load_projects_config(str(tmp_path))

    def test_invalid_mode_in_project_raises(self, tmp_path):
        import yaml
        config = {
            "defaults": {},
            "projects": {"myapp": {"path": str(tmp_path), "mode": "turbo"}},
        }
        config_file = tmp_path / "projects.yaml"
        config_file.write_text(yaml.dump(config))

        from app.projects_config import load_projects_config
        with pytest.raises(ValueError, match="invalid mode"):
            load_projects_config(str(tmp_path))

    def test_valid_mode_in_config_loads(self, tmp_path):
        import yaml
        config = {
            "defaults": {"max_mode": "implement"},
            "projects": {"myapp": {"path": str(tmp_path), "mode": "review"}},
        }
        config_file = tmp_path / "projects.yaml"
        config_file.write_text(yaml.dump(config))

        from app.projects_config import load_projects_config
        result = load_projects_config(str(tmp_path))
        assert result is not None

    def test_min_greater_than_max_in_project_raises(self, tmp_path):
        import yaml
        config = {
            "defaults": {},
            "projects": {"myapp": {"path": str(tmp_path),
                                   "min_mode": "deep", "max_mode": "review"}},
        }
        config_file = tmp_path / "projects.yaml"
        config_file.write_text(yaml.dump(config))

        from app.projects_config import load_projects_config
        with pytest.raises(ValueError, match="min_mode.*higher"):
            load_projects_config(str(tmp_path))


# ---------------------------------------------------------------------------
# Accessor tests (projects_config.get_project_mode_config)
# ---------------------------------------------------------------------------

class TestGetProjectModeConfig:
    """Test get_project_mode_config() accessor."""

    def test_no_mode_config(self):
        from app.projects_config import get_project_mode_config
        config = {"defaults": {}, "projects": {"myapp": {"path": "/tmp"}}}
        result = get_project_mode_config(config, "myapp")
        assert result == {"mode": None, "max_mode": None, "min_mode": None}

    def test_mode_from_project(self):
        from app.projects_config import get_project_mode_config
        config = {
            "defaults": {},
            "projects": {"vendor": {"path": "/tmp", "mode": "review"}},
        }
        result = get_project_mode_config(config, "vendor")
        assert result["mode"] == "review"
        assert result["max_mode"] is None
        assert result["min_mode"] is None

    def test_max_mode_from_defaults(self):
        from app.projects_config import get_project_mode_config
        config = {
            "defaults": {"max_mode": "implement"},
            "projects": {"myapp": {"path": "/tmp"}},
        }
        result = get_project_mode_config(config, "myapp")
        assert result["max_mode"] == "implement"

    def test_project_overrides_defaults(self):
        from app.projects_config import get_project_mode_config
        config = {
            "defaults": {"mode": "review"},
            "projects": {"myapp": {"path": "/tmp", "mode": "deep"}},
        }
        result = get_project_mode_config(config, "myapp")
        assert result["mode"] == "deep"

    def test_mixed_defaults_and_project(self):
        from app.projects_config import get_project_mode_config
        config = {
            "defaults": {"min_mode": "review", "max_mode": "implement"},
            "projects": {"myapp": {"path": "/tmp", "max_mode": "deep"}},
        }
        result = get_project_mode_config(config, "myapp")
        assert result["min_mode"] == "review"  # from defaults
        assert result["max_mode"] == "deep"    # project override

    def test_case_normalized(self):
        from app.projects_config import get_project_mode_config
        config = {
            "defaults": {},
            "projects": {"myapp": {"path": "/tmp", "mode": "DEEP"}},
        }
        result = get_project_mode_config(config, "myapp")
        assert result["mode"] == "deep"

    def test_unknown_project_uses_defaults(self):
        from app.projects_config import get_project_mode_config
        config = {
            "defaults": {"mode": "review"},
            "projects": {"myapp": {"path": "/tmp"}},
        }
        result = get_project_mode_config(config, "unknown")
        assert result["mode"] == "review"

    def test_invalid_mode_value_returns_none(self):
        from app.projects_config import get_project_mode_config
        config = {
            "defaults": {},
            "projects": {"myapp": {"path": "/tmp", "mode": "wait"}},
        }
        result = get_project_mode_config(config, "myapp")
        assert result["mode"] is None  # "wait" is not a valid mode for override


# ---------------------------------------------------------------------------
# Resolution logic tests (usage_tracker.resolve_mode_with_overrides)
# ---------------------------------------------------------------------------

class TestResolveModeWithOverrides:
    """Test resolve_mode_with_overrides() in usage_tracker."""

    def _no_overrides(self):
        return {"mode": None, "max_mode": None, "min_mode": None}

    # --- No overrides ---

    def test_no_overrides_passes_through(self):
        from app.usage_tracker import resolve_mode_with_overrides
        for mode in ("wait", "review", "implement", "deep"):
            resolved, reason = resolve_mode_with_overrides(mode, self._no_overrides())
            assert resolved == mode
            assert reason == ""

    # --- Absolute lock ---

    def test_lock_overrides_quota(self):
        from app.usage_tracker import resolve_mode_with_overrides
        r, reason = resolve_mode_with_overrides("deep", {"mode": "review", "max_mode": None, "min_mode": None})
        assert r == "review"
        assert "project-locked" in reason

    def test_lock_overrides_wait(self):
        from app.usage_tracker import resolve_mode_with_overrides
        r, reason = resolve_mode_with_overrides("wait", {"mode": "review", "max_mode": None, "min_mode": None})
        assert r == "review"
        assert "project-locked" in reason

    def test_lock_same_as_quota_no_reason(self):
        from app.usage_tracker import resolve_mode_with_overrides
        r, reason = resolve_mode_with_overrides("review", {"mode": "review", "max_mode": None, "min_mode": None})
        assert r == "review"
        assert reason == ""

    # --- Max mode (ceiling) ---

    def test_ceiling_caps_deep(self):
        from app.usage_tracker import resolve_mode_with_overrides
        r, reason = resolve_mode_with_overrides("deep", {"mode": None, "max_mode": "implement", "min_mode": None})
        assert r == "implement"
        assert "capped" in reason

    def test_ceiling_caps_implement(self):
        from app.usage_tracker import resolve_mode_with_overrides
        r, reason = resolve_mode_with_overrides("implement", {"mode": None, "max_mode": "review", "min_mode": None})
        assert r == "review"
        assert "capped" in reason

    def test_ceiling_allows_lower(self):
        from app.usage_tracker import resolve_mode_with_overrides
        r, reason = resolve_mode_with_overrides("review", {"mode": None, "max_mode": "implement", "min_mode": None})
        assert r == "review"
        assert reason == ""

    def test_ceiling_allows_equal(self):
        from app.usage_tracker import resolve_mode_with_overrides
        r, reason = resolve_mode_with_overrides("implement", {"mode": None, "max_mode": "implement", "min_mode": None})
        assert r == "implement"
        assert reason == ""

    # --- Min mode (floor) ---

    def test_floor_raises_review(self):
        from app.usage_tracker import resolve_mode_with_overrides
        r, reason = resolve_mode_with_overrides("review", {"mode": None, "max_mode": None, "min_mode": "implement"})
        assert r == "implement"
        assert "raised" in reason

    def test_floor_allows_higher(self):
        from app.usage_tracker import resolve_mode_with_overrides
        r, reason = resolve_mode_with_overrides("deep", {"mode": None, "max_mode": None, "min_mode": "implement"})
        assert r == "deep"
        assert reason == ""

    def test_floor_allows_equal(self):
        from app.usage_tracker import resolve_mode_with_overrides
        r, reason = resolve_mode_with_overrides("implement", {"mode": None, "max_mode": None, "min_mode": "implement"})
        assert r == "implement"
        assert reason == ""

    def test_floor_does_not_override_wait(self):
        from app.usage_tracker import resolve_mode_with_overrides
        r, reason = resolve_mode_with_overrides("wait", {"mode": None, "max_mode": None, "min_mode": "implement"})
        assert r == "wait"
        assert reason == ""

    # --- Combined ceiling + floor ---

    def test_ceiling_and_floor_clamps(self):
        from app.usage_tracker import resolve_mode_with_overrides
        config = {"mode": None, "max_mode": "implement", "min_mode": "implement"}
        r, _ = resolve_mode_with_overrides("deep", config)
        assert r == "implement"
        r, _ = resolve_mode_with_overrides("review", config)
        assert r == "implement"
        r, _ = resolve_mode_with_overrides("implement", config)
        assert r == "implement"

    def test_ceiling_and_floor_range(self):
        from app.usage_tracker import resolve_mode_with_overrides
        config = {"mode": None, "max_mode": "deep", "min_mode": "review"}
        for mode in ("review", "implement", "deep"):
            r, _ = resolve_mode_with_overrides(mode, config)
            assert r == mode  # all within range


# ---------------------------------------------------------------------------
# Integration: _apply_project_mode_overrides in iteration_manager
# ---------------------------------------------------------------------------

class TestApplyProjectModeOverrides:
    """Test iteration_manager._apply_project_mode_overrides."""

    @patch("app.projects_config.load_projects_config")
    @patch("app.projects_config.get_project_mode_config")
    @patch("app.usage_tracker.resolve_mode_with_overrides")
    def test_applies_override(self, mock_resolve, mock_get_mode, mock_load):
        from app.iteration_manager import _apply_project_mode_overrides
        mock_load.return_value = {"projects": {"koan": {}}}
        mock_get_mode.return_value = {"mode": "review", "max_mode": None, "min_mode": None}
        mock_resolve.return_value = ("review", "project-locked to review")

        mode, reason = _apply_project_mode_overrides("koan", "deep", "Ample budget", "/tmp")
        assert mode == "review"
        assert "project-locked" in reason

    @patch("app.projects_config.load_projects_config")
    @patch("app.projects_config.get_project_mode_config")
    @patch("app.usage_tracker.resolve_mode_with_overrides")
    def test_no_override_passthrough(self, mock_resolve, mock_get_mode, mock_load):
        from app.iteration_manager import _apply_project_mode_overrides
        mock_load.return_value = {"projects": {"koan": {}}}
        mock_get_mode.return_value = {"mode": None, "max_mode": None, "min_mode": None}
        mock_resolve.return_value = ("deep", "")

        mode, reason = _apply_project_mode_overrides("koan", "deep", "Ample budget", "/tmp")
        assert mode == "deep"
        assert reason == "Ample budget"

    @patch("app.projects_config.load_projects_config")
    def test_no_config_passthrough(self, mock_load):
        from app.iteration_manager import _apply_project_mode_overrides
        mock_load.return_value = None

        mode, reason = _apply_project_mode_overrides("koan", "deep", "Ample budget", "/tmp")
        assert mode == "deep"

    @patch("app.projects_config.load_projects_config")
    def test_error_fallback(self, mock_load):
        from app.iteration_manager import _apply_project_mode_overrides
        mock_load.side_effect = Exception("yaml broken")

        mode, reason = _apply_project_mode_overrides("koan", "deep", "Ample budget", "/tmp")
        assert mode == "deep"


# ---------------------------------------------------------------------------
# Integration: plan_iteration with mode overrides
# ---------------------------------------------------------------------------

class TestPlanIterationModeOverrides:
    """Verify plan_iteration applies project mode overrides."""

    def _make_config(self, tmp_path, mode_config=None):
        import yaml
        projects_dir = tmp_path / "workspace" / "myapp"
        projects_dir.mkdir(parents=True, exist_ok=True)

        project = {"path": str(projects_dir)}
        if mode_config:
            project.update(mode_config)

        config = {
            "defaults": {},
            "projects": {"myapp": project},
        }
        config_file = tmp_path / "projects.yaml"
        config_file.write_text(yaml.dump(config))
        return str(projects_dir)

    @patch("app.iteration_manager._refresh_usage")
    @patch("app.iteration_manager._get_usage_decision")
    @patch("app.iteration_manager._inject_recurring", return_value=[])
    @patch("app.iteration_manager._pick_mission", return_value=(None, None))
    @patch("app.iteration_manager._check_schedule", return_value=None)
    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("app.iteration_manager._should_contemplate", return_value=False)
    def test_mode_locked_project(self, mock_contemplate, mock_focus,
                                  mock_sched, mock_pick, mock_recurring,
                                  mock_usage, mock_refresh, tmp_path):
        from app.iteration_manager import plan_iteration

        instance = tmp_path / "instance"
        instance.mkdir()
        (instance / "missions.md").write_text("# Missions\n## Pending\n## In Progress\n## Done\n")

        project_path = self._make_config(tmp_path, {"mode": "review"})
        mock_usage.return_value = {
            "mode": "deep", "available_pct": 90,
            "reason": "Ample budget", "project_idx": 0, "display_lines": [],
        }

        result = plan_iteration(
            instance_dir=str(instance),
            koan_root=str(tmp_path),
            run_num=1, count=0,
            projects=[("myapp", project_path)],
        )

        assert result["autonomous_mode"] == "review"
        assert "project-locked" in result["decision_reason"]

    @patch("app.iteration_manager._refresh_usage")
    @patch("app.iteration_manager._get_usage_decision")
    @patch("app.iteration_manager._inject_recurring", return_value=[])
    @patch("app.iteration_manager._pick_mission", return_value=(None, None))
    @patch("app.iteration_manager._check_schedule", return_value=None)
    @patch("app.iteration_manager._check_focus", return_value=None)
    @patch("app.iteration_manager._should_contemplate", return_value=False)
    def test_mode_capped_project(self, mock_contemplate, mock_focus,
                                  mock_sched, mock_pick, mock_recurring,
                                  mock_usage, mock_refresh, tmp_path):
        from app.iteration_manager import plan_iteration

        instance = tmp_path / "instance"
        instance.mkdir()
        (instance / "missions.md").write_text("# Missions\n## Pending\n## In Progress\n## Done\n")

        project_path = self._make_config(tmp_path, {"max_mode": "implement"})
        mock_usage.return_value = {
            "mode": "deep", "available_pct": 90,
            "reason": "Ample budget", "project_idx": 0, "display_lines": [],
        }

        result = plan_iteration(
            instance_dir=str(instance),
            koan_root=str(tmp_path),
            run_num=1, count=0,
            projects=[("myapp", project_path)],
        )

        assert result["autonomous_mode"] == "implement"
        assert "capped" in result["decision_reason"]
