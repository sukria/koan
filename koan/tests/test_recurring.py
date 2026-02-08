"""Tests for recurring.py — recurring missions storage and scheduler."""

import json
import pytest
from datetime import datetime, timedelta
from pathlib import Path

from app.recurring import (
    load_recurring,
    save_recurring,
    add_recurring,
    remove_recurring,
    list_recurring,
    format_recurring_list,
    is_due,
    check_and_inject,
    FREQUENCIES,
)


# --- load_recurring / save_recurring ---


class TestLoadSave:
    def test_load_nonexistent(self, tmp_path):
        assert load_recurring(tmp_path / "nope.json") == []

    def test_load_empty_file(self, tmp_path):
        f = tmp_path / "recurring.json"
        f.write_text("")
        assert load_recurring(f) == []

    def test_load_invalid_json(self, tmp_path):
        f = tmp_path / "recurring.json"
        f.write_text("{not json")
        assert load_recurring(f) == []

    def test_load_non_list(self, tmp_path):
        f = tmp_path / "recurring.json"
        f.write_text('{"key": "value"}')
        assert load_recurring(f) == []

    def test_roundtrip(self, tmp_path):
        f = tmp_path / "recurring.json"
        data = [{"id": "rec_1", "frequency": "daily", "text": "test"}]
        save_recurring(f, data)
        loaded = load_recurring(f)
        assert loaded == data

    def test_save_creates_file(self, tmp_path):
        f = tmp_path / "recurring.json"
        save_recurring(f, [])
        assert f.exists()
        assert json.loads(f.read_text()) == []


# --- add_recurring ---


class TestAddRecurring:
    def test_add_daily(self, tmp_path):
        f = tmp_path / "recurring.json"
        m = add_recurring(f, "daily", "check emails")
        assert m["frequency"] == "daily"
        assert m["text"] == "check emails"
        assert m["project"] is None
        assert m["last_run"] is None
        assert m["enabled"] is True
        assert m["id"].startswith("rec_")

    def test_add_with_project(self, tmp_path):
        f = tmp_path / "recurring.json"
        m = add_recurring(f, "weekly", "audit security", project="koan")
        assert m["project"] == "koan"

    def test_add_invalid_frequency(self, tmp_path):
        f = tmp_path / "recurring.json"
        with pytest.raises(ValueError, match="Invalid frequency"):
            add_recurring(f, "monthly", "something")

    def test_add_multiple(self, tmp_path):
        f = tmp_path / "recurring.json"
        add_recurring(f, "daily", "task 1")
        add_recurring(f, "hourly", "task 2")
        missions = load_recurring(f)
        assert len(missions) == 2

    def test_add_strips_whitespace(self, tmp_path):
        f = tmp_path / "recurring.json"
        m = add_recurring(f, "daily", "  check emails  ")
        assert m["text"] == "check emails"


# --- remove_recurring ---


class TestRemoveRecurring:
    def _setup(self, tmp_path):
        f = tmp_path / "recurring.json"
        add_recurring(f, "daily", "check emails")
        add_recurring(f, "weekly", "security audit")
        add_recurring(f, "hourly", "check PRs")
        return f

    def test_remove_by_number(self, tmp_path):
        f = self._setup(tmp_path)
        removed = remove_recurring(f, "1")
        assert "check emails" in removed
        assert len(load_recurring(f)) == 2

    def test_remove_by_keyword(self, tmp_path):
        f = self._setup(tmp_path)
        removed = remove_recurring(f, "security")
        assert "security audit" in removed
        assert len(load_recurring(f)) == 2

    def test_remove_invalid_number(self, tmp_path):
        f = self._setup(tmp_path)
        with pytest.raises(ValueError, match="Invalid number"):
            remove_recurring(f, "10")

    def test_remove_no_match(self, tmp_path):
        f = self._setup(tmp_path)
        with pytest.raises(ValueError, match="No recurring mission matching"):
            remove_recurring(f, "nonexistent")

    def test_remove_empty_list(self, tmp_path):
        f = tmp_path / "recurring.json"
        save_recurring(f, [])
        with pytest.raises(ValueError, match="No recurring missions"):
            remove_recurring(f, "1")

    def test_remove_ambiguous(self, tmp_path):
        f = tmp_path / "recurring.json"
        add_recurring(f, "daily", "check emails morning")
        add_recurring(f, "daily", "check emails evening")
        with pytest.raises(ValueError, match="Multiple matches"):
            remove_recurring(f, "check")


# --- list_recurring ---


class TestListRecurring:
    def test_empty(self, tmp_path):
        f = tmp_path / "recurring.json"
        assert list_recurring(f) == []

    def test_sorts_by_frequency(self, tmp_path):
        f = tmp_path / "recurring.json"
        add_recurring(f, "weekly", "task W")
        add_recurring(f, "hourly", "task H")
        add_recurring(f, "daily", "task D")
        result = list_recurring(f)
        assert [m["frequency"] for m in result] == ["hourly", "daily", "weekly"]

    def test_excludes_disabled(self, tmp_path):
        f = tmp_path / "recurring.json"
        missions = [
            {"id": "1", "frequency": "daily", "text": "active", "enabled": True},
            {"id": "2", "frequency": "daily", "text": "disabled", "enabled": False},
        ]
        save_recurring(f, missions)
        result = list_recurring(f)
        assert len(result) == 1
        assert result[0]["text"] == "active"


# --- format_recurring_list ---


class TestFormatRecurringList:
    def test_empty(self):
        assert "No recurring" in format_recurring_list([])

    def test_basic_format(self):
        missions = [
            {"frequency": "daily", "text": "check emails", "project": None, "last_run": None},
        ]
        result = format_recurring_list(missions)
        assert "[daily]" in result
        assert "check emails" in result
        assert "never run" in result

    def test_with_project(self):
        missions = [
            {"frequency": "weekly", "text": "audit", "project": "koan", "last_run": None},
        ]
        result = format_recurring_list(missions)
        assert "project: koan" in result

    def test_with_last_run(self):
        recent = (datetime.now() - timedelta(minutes=30)).isoformat()
        missions = [
            {"frequency": "hourly", "text": "check PRs", "project": None, "last_run": recent},
        ]
        result = format_recurring_list(missions)
        assert "30min ago" in result


# --- is_due ---


class TestIsDue:
    def test_never_run_is_due(self):
        m = {"frequency": "daily", "last_run": None, "enabled": True}
        assert is_due(m) is True

    def test_disabled_not_due(self):
        m = {"frequency": "daily", "last_run": None, "enabled": False}
        assert is_due(m) is False

    def test_hourly_within_hour(self):
        now = datetime(2026, 2, 3, 14, 0)
        m = {"frequency": "hourly", "last_run": (now - timedelta(minutes=30)).isoformat(), "enabled": True}
        assert is_due(m, now) is False

    def test_hourly_past_hour(self):
        now = datetime(2026, 2, 3, 14, 0)
        m = {"frequency": "hourly", "last_run": (now - timedelta(hours=1, minutes=1)).isoformat(), "enabled": True}
        assert is_due(m, now) is True

    def test_daily_same_day(self):
        now = datetime(2026, 2, 3, 14, 0)
        m = {"frequency": "daily", "last_run": datetime(2026, 2, 3, 8, 0).isoformat(), "enabled": True}
        assert is_due(m, now) is False

    def test_daily_next_day(self):
        now = datetime(2026, 2, 4, 8, 0)
        m = {"frequency": "daily", "last_run": datetime(2026, 2, 3, 22, 0).isoformat(), "enabled": True}
        assert is_due(m, now) is True

    def test_weekly_within_week(self):
        now = datetime(2026, 2, 3, 14, 0)
        m = {"frequency": "weekly", "last_run": (now - timedelta(days=3)).isoformat(), "enabled": True}
        assert is_due(m, now) is False

    def test_weekly_past_week(self):
        now = datetime(2026, 2, 10, 14, 0)
        m = {"frequency": "weekly", "last_run": datetime(2026, 2, 3, 8, 0).isoformat(), "enabled": True}
        assert is_due(m, now) is True

    def test_invalid_last_run_is_due(self):
        m = {"frequency": "daily", "last_run": "not-a-date", "enabled": True}
        assert is_due(m) is True


# --- check_and_inject ---


class TestCheckAndInject:
    def _setup_missions(self, tmp_path):
        missions_path = tmp_path / "missions.md"
        missions_path.write_text(
            "# Missions\n\n"
            "## Pending\n\n"
            "## In Progress\n\n"
            "## Done\n\n"
        )
        return missions_path

    def test_no_recurring_file(self, tmp_path):
        missions_path = self._setup_missions(tmp_path)
        recurring_path = tmp_path / "recurring.json"
        result = check_and_inject(recurring_path, missions_path)
        assert result == []

    def test_injects_due_mission(self, tmp_path):
        missions_path = self._setup_missions(tmp_path)
        recurring_path = tmp_path / "recurring.json"
        add_recurring(recurring_path, "daily", "check emails")

        now = datetime(2026, 2, 3, 8, 0)
        result = check_and_inject(recurring_path, missions_path, now)

        assert len(result) == 1
        assert "check emails" in result[0]

        # Verify mission was inserted into missions.md
        content = missions_path.read_text()
        assert "[daily] check emails" in content

        # Verify last_run was updated
        missions = load_recurring(recurring_path)
        assert missions[0]["last_run"] is not None

    def test_skips_not_due(self, tmp_path):
        missions_path = self._setup_missions(tmp_path)
        recurring_path = tmp_path / "recurring.json"

        # Add and mark as recently run
        now = datetime(2026, 2, 3, 14, 0)
        missions_data = [{
            "id": "rec_1",
            "frequency": "daily",
            "text": "check emails",
            "project": None,
            "created": "2026-02-03T08:00:00",
            "last_run": now.isoformat(),
            "enabled": True,
        }]
        save_recurring(recurring_path, missions_data)

        result = check_and_inject(recurring_path, missions_path, now)
        assert result == []

    def test_injects_with_project_tag(self, tmp_path):
        missions_path = self._setup_missions(tmp_path)
        recurring_path = tmp_path / "recurring.json"
        add_recurring(recurring_path, "weekly", "audit security", project="koan")

        now = datetime(2026, 2, 3, 8, 0)
        check_and_inject(recurring_path, missions_path, now)

        content = missions_path.read_text()
        assert "[project:koan]" in content
        assert "[weekly] audit security" in content

    def test_multiple_due(self, tmp_path):
        missions_path = self._setup_missions(tmp_path)
        recurring_path = tmp_path / "recurring.json"
        add_recurring(recurring_path, "daily", "task 1")
        add_recurring(recurring_path, "hourly", "task 2")

        now = datetime(2026, 2, 3, 8, 0)
        result = check_and_inject(recurring_path, missions_path, now)

        assert len(result) == 2

    def test_mixed_due_and_not_due(self, tmp_path):
        missions_path = self._setup_missions(tmp_path)
        recurring_path = tmp_path / "recurring.json"

        now = datetime(2026, 2, 3, 14, 0)
        missions_data = [
            {
                "id": "rec_1", "frequency": "daily", "text": "due task",
                "project": None, "created": "2026-02-01", "last_run": None, "enabled": True,
            },
            {
                "id": "rec_2", "frequency": "daily", "text": "not due",
                "project": None, "created": "2026-02-01",
                "last_run": now.isoformat(), "enabled": True,
            },
        ]
        save_recurring(recurring_path, missions_data)

        result = check_and_inject(recurring_path, missions_path, now)
        assert len(result) == 1
        assert "due task" in result[0]


# --- CLI recurring_scheduler.py ---


class TestRecurringSchedulerCLI:
    def test_no_args(self):
        import subprocess
        result = subprocess.run(
            ["python3", "-m", "app.recurring_scheduler"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 1

    def test_no_recurring_file(self, tmp_path):
        import subprocess
        result = subprocess.run(
            ["python3", "-m", "app.recurring_scheduler", str(tmp_path)],
            capture_output=True, text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 0
