"""Tests for the /focus and /unfocus skill handlers."""

import json
from unittest.mock import MagicMock

from app.skills import SkillContext


def _make_ctx(command_name, koan_root, args=""):
    """Create a minimal SkillContext for testing."""
    ctx = MagicMock(spec=SkillContext)
    ctx.command_name = command_name
    ctx.koan_root = koan_root
    ctx.args = args
    return ctx


class TestFocusCommand:
    """Tests for /focus command."""

    def test_focus_creates_marker_file(self, tmp_path):
        from skills.core.focus.handler import handle

        ctx = _make_ctx("focus", tmp_path)
        result = handle(ctx)

        assert "ON" in result or "🎯" in result
        marker = tmp_path / ".koan-focus"
        assert marker.exists()

    def test_focus_default_duration(self, tmp_path):
        from skills.core.focus.handler import handle

        ctx = _make_ctx("focus", tmp_path)
        result = handle(ctx)

        # Default is 5h
        assert "5h" in result or "5 h" in result
        marker = tmp_path / ".koan-focus"
        assert marker.exists()

    def test_focus_custom_duration(self, tmp_path):
        from skills.core.focus.handler import handle

        ctx = _make_ctx("focus", tmp_path, args="3h")
        result = handle(ctx)

        assert "3h" in result or "3 h" in result
        marker = tmp_path / ".koan-focus"
        assert marker.exists()

    def test_focus_duration_with_minutes(self, tmp_path):
        from skills.core.focus.handler import handle

        ctx = _make_ctx("focus", tmp_path, args="2h30m")
        result = handle(ctx)

        assert "ON" in result
        marker = tmp_path / ".koan-focus"
        assert marker.exists()

    def test_focus_invalid_duration(self, tmp_path):
        from skills.core.focus.handler import handle

        ctx = _make_ctx("focus", tmp_path, args="invalid")
        result = handle(ctx)

        assert "Invalid" in result or "❌" in result
        marker = tmp_path / ".koan-focus"
        assert not marker.exists()

    def test_focus_response_mentions_missions_only(self, tmp_path):
        from skills.core.focus.handler import handle

        ctx = _make_ctx("focus", tmp_path)
        result = handle(ctx)

        assert "mission" in result.lower()


class TestUnfocusCommand:
    """Tests for /unfocus command."""

    def test_unfocus_removes_marker(self, tmp_path):
        from skills.core.focus.handler import handle

        # First create focus state
        marker = tmp_path / ".koan-focus"
        marker.write_text(json.dumps({"end_time": 9999999999}))

        ctx = _make_ctx("unfocus", tmp_path)
        result = handle(ctx)

        assert "OFF" in result or "🎯" in result
        assert not marker.exists()

    def test_unfocus_when_not_focused(self, tmp_path):
        from skills.core.focus.handler import handle

        # No marker file exists
        ctx = _make_ctx("unfocus", tmp_path)
        result = handle(ctx)

        assert "Not" in result or "not" in result


class TestFocusUnfocusToggle:
    """Test toggling between focus and unfocus modes."""

    def test_toggle_focus_then_unfocus(self, tmp_path):
        from skills.core.focus.handler import handle

        marker = tmp_path / ".koan-focus"

        # Enable focus
        ctx_focus = _make_ctx("focus", tmp_path)
        handle(ctx_focus)
        assert marker.exists()

        # Disable with unfocus
        ctx_unfocus = _make_ctx("unfocus", tmp_path)
        handle(ctx_unfocus)
        assert not marker.exists()

    def test_focus_overwrites_existing(self, tmp_path):
        from skills.core.focus.handler import handle

        marker = tmp_path / ".koan-focus"

        # Enable focus with 2h
        ctx1 = _make_ctx("focus", tmp_path, args="2h")
        handle(ctx1)
        assert marker.exists()

        # Enable again with 4h - should overwrite
        ctx2 = _make_ctx("focus", tmp_path, args="4h")
        result = handle(ctx2)

        assert marker.exists()
        assert "4h" in result or "4 h" in result
