"""Tests for the /recurring core skill — manage recurring missions."""

import json
from pathlib import Path

import pytest

from app.skills import SkillContext


def _load_handler():
    """Import the recurring skill handler."""
    import importlib.util
    handler_path = str(
        Path(__file__).parent.parent / "skills" / "core" / "recurring" / "handler.py"
    )
    spec = importlib.util.spec_from_file_location("recurring_handler", handler_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ctx(tmp_path, command_name, args=""):
    """Create a SkillContext for testing."""
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir(exist_ok=True)
    return SkillContext(
        koan_root=tmp_path,
        instance_dir=instance_dir,
        command_name=command_name,
        args=args,
    )


# ---------------------------------------------------------------------------
# /daily, /hourly, /weekly — add recurring missions
# ---------------------------------------------------------------------------


class TestAddCommands:
    def test_daily_adds_mission(self, tmp_path):
        mod = _load_handler()
        ctx = _ctx(tmp_path, "daily", "check emails")
        result = mod.handle(ctx)
        assert "Recurring mission added (daily)" in result
        assert "check emails" in result

        recurring_path = tmp_path / "instance" / "recurring.json"
        assert recurring_path.exists()
        data = json.loads(recurring_path.read_text())
        assert len(data) == 1
        assert data[0]["frequency"] == "daily"

    def test_hourly_adds_mission(self, tmp_path):
        mod = _load_handler()
        ctx = _ctx(tmp_path, "hourly", "check PRs")
        result = mod.handle(ctx)
        assert "Recurring mission added (hourly)" in result

    def test_weekly_adds_mission(self, tmp_path):
        mod = _load_handler()
        ctx = _ctx(tmp_path, "weekly", "security audit")
        result = mod.handle(ctx)
        assert "Recurring mission added (weekly)" in result

    def test_add_with_project_tag(self, tmp_path):
        mod = _load_handler()
        ctx = _ctx(tmp_path, "daily", "[project:koan] check tests")
        result = mod.handle(ctx)
        assert "project:koan" in result

        recurring_path = tmp_path / "instance" / "recurring.json"
        data = json.loads(recurring_path.read_text())
        assert data[0]["project"] == "koan"

    def test_add_empty_shows_usage(self, tmp_path):
        mod = _load_handler()
        ctx = _ctx(tmp_path, "daily", "")
        result = mod.handle(ctx)
        assert "Usage:" in result
        assert "/daily" in result

    def test_add_whitespace_only_shows_usage(self, tmp_path):
        mod = _load_handler()
        ctx = _ctx(tmp_path, "hourly", "   ")
        result = mod.handle(ctx)
        assert "Usage:" in result


# ---------------------------------------------------------------------------
# /recurring — list recurring missions
# ---------------------------------------------------------------------------


class TestListCommand:
    def test_list_empty(self, tmp_path):
        mod = _load_handler()
        ctx = _ctx(tmp_path, "recurring")
        result = mod.handle(ctx)
        assert "No recurring" in result

    def test_list_shows_missions(self, tmp_path):
        mod = _load_handler()
        # Add some missions first
        from app.recurring import add_recurring
        recurring_path = tmp_path / "instance" / "recurring.json"
        (tmp_path / "instance").mkdir(exist_ok=True)
        add_recurring(recurring_path, "daily", "check emails")
        add_recurring(recurring_path, "weekly", "audit security")

        ctx = _ctx(tmp_path, "recurring")
        result = mod.handle(ctx)
        assert "check emails" in result
        assert "audit security" in result
        assert "[daily]" in result
        assert "[weekly]" in result


# ---------------------------------------------------------------------------
# /cancel-recurring — cancel recurring missions
# ---------------------------------------------------------------------------


class TestCancelRecurringCommand:
    def _setup_recurring(self, tmp_path):
        from app.recurring import add_recurring
        (tmp_path / "instance").mkdir(exist_ok=True)
        recurring_path = tmp_path / "instance" / "recurring.json"
        add_recurring(recurring_path, "daily", "check emails")
        add_recurring(recurring_path, "weekly", "security audit")
        return recurring_path

    def test_cancel_by_number(self, tmp_path):
        mod = _load_handler()
        self._setup_recurring(tmp_path)
        ctx = _ctx(tmp_path, "cancel-recurring", "1")
        result = mod.handle(ctx)
        assert "Recurring mission removed" in result
        assert "check emails" in result

    def test_cancel_by_keyword(self, tmp_path):
        mod = _load_handler()
        self._setup_recurring(tmp_path)
        ctx = _ctx(tmp_path, "cancel-recurring", "security")
        result = mod.handle(ctx)
        assert "Recurring mission removed" in result
        assert "security audit" in result

    def test_cancel_invalid_number(self, tmp_path):
        mod = _load_handler()
        self._setup_recurring(tmp_path)
        ctx = _ctx(tmp_path, "cancel-recurring", "99")
        result = mod.handle(ctx)
        assert "Invalid number" in result

    def test_cancel_no_match(self, tmp_path):
        mod = _load_handler()
        self._setup_recurring(tmp_path)
        ctx = _ctx(tmp_path, "cancel-recurring", "nonexistent")
        result = mod.handle(ctx)
        assert "No recurring mission matching" in result

    def test_cancel_empty_shows_list(self, tmp_path):
        mod = _load_handler()
        self._setup_recurring(tmp_path)
        ctx = _ctx(tmp_path, "cancel-recurring", "")
        result = mod.handle(ctx)
        assert "check emails" in result
        assert "/cancel-recurring" in result

    def test_cancel_empty_no_missions(self, tmp_path):
        mod = _load_handler()
        ctx = _ctx(tmp_path, "cancel-recurring", "")
        result = mod.handle(ctx)
        assert "No recurring missions to cancel" in result


# ---------------------------------------------------------------------------
# Skill registration (SKILL.md)
# ---------------------------------------------------------------------------


class TestSkillRegistration:
    def test_skill_md_exists(self):
        skill_md = Path(__file__).parent.parent / "skills" / "core" / "recurring" / "SKILL.md"
        assert skill_md.exists()

    def test_skill_md_has_required_commands(self):
        skill_md = Path(__file__).parent.parent / "skills" / "core" / "recurring" / "SKILL.md"
        content = skill_md.read_text()
        for cmd in ["daily", "hourly", "weekly", "recurring", "cancel-recurring"]:
            assert cmd in content, f"Missing command '{cmd}' in SKILL.md"

    def test_handler_exists(self):
        handler = Path(__file__).parent.parent / "skills" / "core" / "recurring" / "handler.py"
        assert handler.exists()

    def test_registry_discovers_recurring(self):
        from app.skills import build_registry
        registry = build_registry()
        for cmd in ["daily", "hourly", "weekly", "recurring", "cancel-recurring"]:
            skill = registry.find_by_command(cmd)
            assert skill is not None, f"Command '/{cmd}' not found in registry"
            assert skill.name == "recurring"
