"""Tests for the /verbose and /silent skill handlers."""

from unittest.mock import MagicMock

from app.skills import SkillContext


def _make_ctx(command_name, koan_root):
    """Create a minimal SkillContext for testing."""
    ctx = MagicMock(spec=SkillContext)
    ctx.command_name = command_name
    ctx.koan_root = koan_root
    ctx.args = ""
    return ctx


class TestVerboseCommand:
    """Tests for /verbose command."""

    def test_verbose_creates_marker_file(self, tmp_path):
        from skills.core.verbose.handler import handle

        ctx = _make_ctx("verbose", tmp_path)
        result = handle(ctx)

        assert "ON" in result or "🔔" in result
        marker = tmp_path / ".koan-verbose"
        assert marker.exists()

    def test_verbose_overwrites_existing(self, tmp_path):
        from skills.core.verbose.handler import handle

        marker = tmp_path / ".koan-verbose"
        marker.write_text("OLD_CONTENT")

        ctx = _make_ctx("verbose", tmp_path)
        result = handle(ctx)

        assert "ON" in result
        assert marker.read_text() == "VERBOSE"

    def test_verbose_response_mentions_updates(self, tmp_path):
        from skills.core.verbose.handler import handle

        ctx = _make_ctx("verbose", tmp_path)
        result = handle(ctx)

        assert "progress" in result.lower() or "update" in result.lower()


class TestSilentCommand:
    """Tests for /silent command."""

    def test_silent_removes_marker(self, tmp_path):
        from skills.core.verbose.handler import handle

        marker = tmp_path / ".koan-verbose"
        marker.write_text("VERBOSE")

        ctx = _make_ctx("silent", tmp_path)
        result = handle(ctx)

        assert "OFF" in result or "🔕" in result
        assert not marker.exists()

    def test_silent_when_already_silent(self, tmp_path):
        from skills.core.verbose.handler import handle

        # No marker file exists
        ctx = _make_ctx("silent", tmp_path)
        result = handle(ctx)

        assert "Already" in result or "silent" in result.lower()

    def test_silent_response_mentions_conclusion(self, tmp_path):
        from skills.core.verbose.handler import handle

        marker = tmp_path / ".koan-verbose"
        marker.write_text("VERBOSE")

        ctx = _make_ctx("silent", tmp_path)
        result = handle(ctx)

        assert "silent" in result.lower() or "conclusion" in result.lower()


class TestVerboseSilentToggle:
    """Test toggling between verbose and silent modes."""

    def test_toggle_verbose_then_silent(self, tmp_path):
        from skills.core.verbose.handler import handle

        marker = tmp_path / ".koan-verbose"

        # Enable verbose
        ctx_verbose = _make_ctx("verbose", tmp_path)
        handle(ctx_verbose)
        assert marker.exists()

        # Disable with silent
        ctx_silent = _make_ctx("silent", tmp_path)
        handle(ctx_silent)
        assert not marker.exists()

    def test_toggle_silent_then_verbose(self, tmp_path):
        from skills.core.verbose.handler import handle

        marker = tmp_path / ".koan-verbose"

        # Ensure silent (no file)
        ctx_silent = _make_ctx("silent", tmp_path)
        handle(ctx_silent)
        assert not marker.exists()

        # Enable verbose
        ctx_verbose = _make_ctx("verbose", tmp_path)
        handle(ctx_verbose)
        assert marker.exists()
