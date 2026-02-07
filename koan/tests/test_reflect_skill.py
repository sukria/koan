"""Tests for the /reflect skill handler."""

from unittest.mock import MagicMock

from app.skills import SkillContext


def _make_ctx(instance_dir, args=""):
    """Create a minimal SkillContext for testing."""
    ctx = MagicMock(spec=SkillContext)
    ctx.command_name = "reflect"
    ctx.instance_dir = instance_dir
    ctx.args = args
    return ctx


class TestReflectSkill:
    """Tests for /reflect command."""

    def test_no_args_returns_usage(self, tmp_path):
        from skills.core.reflect.handler import handle

        ctx = _make_ctx(tmp_path, args="")
        result = handle(ctx)
        assert "Usage:" in result
        assert "/reflect" in result

    def test_empty_whitespace_returns_usage(self, tmp_path):
        from skills.core.reflect.handler import handle

        ctx = _make_ctx(tmp_path, args="   ")
        result = handle(ctx)
        assert "Usage:" in result

    def test_writes_to_shared_journal(self, tmp_path):
        from skills.core.reflect.handler import handle

        ctx = _make_ctx(tmp_path, args="This is my deep thought")
        result = handle(ctx)

        assert "Noted" in result
        assert "journal" in result.lower()

        shared_journal = tmp_path / "shared-journal.md"
        assert shared_journal.exists()
        content = shared_journal.read_text()
        assert "Human" in content
        assert "This is my deep thought" in content

    def test_appends_multiple_entries(self, tmp_path):
        from skills.core.reflect.handler import handle

        # First entry
        ctx1 = _make_ctx(tmp_path, args="First thought")
        handle(ctx1)

        # Second entry
        ctx2 = _make_ctx(tmp_path, args="Second thought")
        handle(ctx2)

        shared_journal = tmp_path / "shared-journal.md"
        content = shared_journal.read_text()
        assert "First thought" in content
        assert "Second thought" in content
        # Should have two Human sections
        assert content.count("## Human") == 2

    def test_creates_parent_directory(self, tmp_path):
        from skills.core.reflect.handler import handle

        nested = tmp_path / "sub" / "instance"
        ctx = _make_ctx(nested, args="Thought in nested dir")
        result = handle(ctx)

        assert "Noted" in result
        shared_journal = nested / "shared-journal.md"
        assert shared_journal.exists()

    def test_entry_includes_timestamp(self, tmp_path):
        from skills.core.reflect.handler import handle
        from datetime import datetime

        ctx = _make_ctx(tmp_path, args="Timestamped thought")
        handle(ctx)

        shared_journal = tmp_path / "shared-journal.md"
        content = shared_journal.read_text()
        # Check for date format like "2026-02-07"
        today = datetime.now().strftime("%Y-%m-%d")
        assert today in content
