"""Tests for mission_history.py — execution tracker for dedup protection."""

import json
import time
from pathlib import Path

import pytest

from app.mission_history import (
    _normalize_key,
    cleanup_old_entries,
    get_execution_count,
    record_execution,
    should_skip_mission,
)


@pytest.fixture
def instance_dir(tmp_path):
    return str(tmp_path)


# ---------------------------------------------------------------------------
# _normalize_key
# ---------------------------------------------------------------------------

class TestNormalizeKey:
    def test_strips_leading_dash(self):
        assert _normalize_key("- Fix the bug") == "Fix the bug"

    def test_strips_whitespace(self):
        assert _normalize_key("  Fix the bug  ") == "Fix the bug"

    def test_first_line_only(self):
        assert _normalize_key("- Fix bug\n  with details") == "Fix bug"

    def test_empty_string(self):
        assert _normalize_key("") == ""

    def test_dash_with_extra_spaces(self):
        assert _normalize_key("-  Fix the bug") == "Fix the bug"

    def test_strips_project_tag(self):
        assert _normalize_key("- [project:koan] /plan Add dark mode") == "/plan Add dark mode"

    def test_strips_projet_tag_french(self):
        assert _normalize_key("- [projet:koan] Fix auth") == "Fix auth"

    def test_strips_project_tag_with_hyphens(self):
        assert _normalize_key("- [project:my-app] Fix bug") == "Fix bug"

    def test_strips_project_tag_with_underscores(self):
        assert _normalize_key("- [project:my_app] Fix bug") == "Fix bug"

    def test_same_key_with_and_without_tag(self):
        """Mission with and without project tag should normalize to same key."""
        assert _normalize_key("- [project:koan] Fix auth") == _normalize_key("- Fix auth")


# ---------------------------------------------------------------------------
# record_execution / get_execution_count
# ---------------------------------------------------------------------------

class TestRecordExecution:
    def test_first_execution(self, instance_dir):
        record_execution(instance_dir, "- Fix the bug", "koan", 0)
        assert get_execution_count(instance_dir, "- Fix the bug") == 1

    def test_subsequent_executions_increment(self, instance_dir):
        record_execution(instance_dir, "- Fix the bug", "koan", 0)
        record_execution(instance_dir, "- Fix the bug", "koan", 1)
        record_execution(instance_dir, "- Fix the bug", "koan", 0)
        assert get_execution_count(instance_dir, "- Fix the bug") == 3

    def test_different_missions_tracked_separately(self, instance_dir):
        record_execution(instance_dir, "- Fix auth", "koan", 0)
        record_execution(instance_dir, "- Fix auth", "koan", 0)
        record_execution(instance_dir, "- Add tests", "koan", 0)
        assert get_execution_count(instance_dir, "- Fix auth") == 2
        assert get_execution_count(instance_dir, "- Add tests") == 1

    def test_unknown_mission_returns_zero(self, instance_dir):
        assert get_execution_count(instance_dir, "- Never seen") == 0

    def test_empty_instance_dir_returns_zero(self, instance_dir):
        assert get_execution_count(instance_dir, "- Something") == 0

    def test_records_project_and_exit_code(self, instance_dir):
        record_execution(instance_dir, "- Fix bug", "koan", 1)
        history_path = Path(instance_dir, "mission_history.json")
        data = json.loads(history_path.read_text())
        key = "Fix bug"
        assert data[key]["project"] == "koan"
        assert data[key]["last_exit_code"] == 1

    def test_records_timestamp(self, instance_dir):
        before = time.time()
        record_execution(instance_dir, "- Fix bug", "koan", 0)
        after = time.time()
        history_path = Path(instance_dir, "mission_history.json")
        data = json.loads(history_path.read_text())
        key = "Fix bug"
        assert before <= data[key]["last_run"] <= after

    def test_empty_mission_text_ignored(self, instance_dir):
        record_execution(instance_dir, "", "koan", 0)
        assert get_execution_count(instance_dir, "") == 0


# ---------------------------------------------------------------------------
# should_skip_mission
# ---------------------------------------------------------------------------

class TestShouldSkipMission:
    def test_below_threshold_returns_false(self, instance_dir):
        record_execution(instance_dir, "- Fix bug", "koan", 1)
        record_execution(instance_dir, "- Fix bug", "koan", 1)
        assert should_skip_mission(instance_dir, "- Fix bug", max_executions=3) is False

    def test_at_threshold_returns_true(self, instance_dir):
        for _ in range(3):
            record_execution(instance_dir, "- Fix bug", "koan", 1)
        assert should_skip_mission(instance_dir, "- Fix bug", max_executions=3) is True

    def test_above_threshold_returns_true(self, instance_dir):
        for _ in range(5):
            record_execution(instance_dir, "- Fix bug", "koan", 1)
        assert should_skip_mission(instance_dir, "- Fix bug", max_executions=3) is True

    def test_custom_threshold(self, instance_dir):
        record_execution(instance_dir, "- Fix bug", "koan", 1)
        assert should_skip_mission(instance_dir, "- Fix bug", max_executions=1) is True

    def test_unknown_mission_returns_false(self, instance_dir):
        assert should_skip_mission(instance_dir, "- Unknown", max_executions=3) is False

    def test_default_threshold_is_three(self, instance_dir):
        for _ in range(3):
            record_execution(instance_dir, "- Fix bug", "koan", 1)
        assert should_skip_mission(instance_dir, "- Fix bug") is True
        assert should_skip_mission(instance_dir, "- Other") is False


# ---------------------------------------------------------------------------
# cleanup_old_entries
# ---------------------------------------------------------------------------

class TestCleanupOldEntries:
    def test_removes_old_entries(self, instance_dir):
        record_execution(instance_dir, "- Old task", "koan", 0)
        # Manually backdate the entry
        history_path = Path(instance_dir, "mission_history.json")
        data = json.loads(history_path.read_text())
        data["Old task"]["last_run"] = time.time() - 200_000  # ~55 hours ago
        history_path.write_text(json.dumps(data))

        record_execution(instance_dir, "- Recent task", "koan", 0)
        cleanup_old_entries(instance_dir, max_age_hours=48)
        assert get_execution_count(instance_dir, "- Old task") == 0
        assert get_execution_count(instance_dir, "- Recent task") == 1

    def test_preserves_recent_entries(self, instance_dir):
        record_execution(instance_dir, "- Fresh task", "koan", 0)
        cleanup_old_entries(instance_dir, max_age_hours=48)
        assert get_execution_count(instance_dir, "- Fresh task") == 1

    def test_caps_at_max_entries(self, instance_dir):
        # Create 110 entries
        for i in range(110):
            record_execution(instance_dir, f"- Task {i}", "koan", 0)
        cleanup_old_entries(instance_dir)
        history_path = Path(instance_dir, "mission_history.json")
        data = json.loads(history_path.read_text())
        assert len(data) <= 100

    def test_empty_history_no_error(self, instance_dir):
        cleanup_old_entries(instance_dir)  # no history file yet

    def test_corrupt_json_handled_gracefully(self, instance_dir):
        history_path = Path(instance_dir, "mission_history.json")
        history_path.write_text("not json{{{")
        cleanup_old_entries(instance_dir)
        # Should not raise — corrupt data treated as empty


# ---------------------------------------------------------------------------
# Integration: complete workflow
# ---------------------------------------------------------------------------

class TestMissionHistoryIntegration:
    def test_record_and_skip_workflow(self, instance_dir):
        """Full workflow: record 3 failures, then should_skip returns True."""
        mission = "- /plan Add dark mode"
        for _ in range(3):
            assert should_skip_mission(instance_dir, mission) is False
            record_execution(instance_dir, mission, "koan", 1)
        assert should_skip_mission(instance_dir, mission) is True

    def test_normalize_key_matches_across_formats(self, instance_dir):
        """Mission recorded with '- ' prefix matches query without it."""
        record_execution(instance_dir, "- Fix the bug", "koan", 0)
        assert get_execution_count(instance_dir, "Fix the bug") == 1
        assert get_execution_count(instance_dir, "- Fix the bug") == 1

    def test_multiline_mission_uses_first_line(self, instance_dir):
        """Multi-line missions are normalized to first line."""
        record_execution(instance_dir, "- Fix bug\n  with details\n  more", "koan", 0)
        assert get_execution_count(instance_dir, "- Fix bug") == 1

    def test_project_tagged_missions_share_counter(self, instance_dir):
        """Same mission with different project tags shares one dedup counter."""
        record_execution(instance_dir, "- [project:koan] Fix auth", "koan", 1)
        record_execution(instance_dir, "- Fix auth", "koan", 1)
        record_execution(instance_dir, "- [projet:koan] Fix auth", "koan", 1)
        assert get_execution_count(instance_dir, "- Fix auth") == 3
        assert should_skip_mission(instance_dir, "- [project:koan] Fix auth") is True
