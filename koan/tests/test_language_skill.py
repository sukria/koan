"""Tests for the /language skill handler."""

from unittest.mock import MagicMock, patch

from app.skills import SkillContext


def _make_ctx(args=""):
    """Create a minimal SkillContext for testing."""
    ctx = MagicMock(spec=SkillContext)
    ctx.command_name = "language"
    ctx.args = args
    return ctx


class TestLanguageNoArgs:
    """Tests for /language with no arguments."""

    def test_no_args_shows_usage(self):
        from skills.core.language.handler import handle

        with patch("app.language_preference.get_language", return_value=None):
            ctx = _make_ctx("")
            result = handle(ctx)

        assert "Usage:" in result
        assert "/language" in result

    def test_no_args_shows_current_language(self):
        from skills.core.language.handler import handle

        with patch("app.language_preference.get_language", return_value="french"):
            ctx = _make_ctx("")
            result = handle(ctx)

        assert "french" in result.lower()
        assert "Current" in result or "current" in result.lower()

    def test_no_args_when_no_override_set(self):
        from skills.core.language.handler import handle

        with patch("app.language_preference.get_language", return_value=None):
            ctx = _make_ctx("")
            result = handle(ctx)

        assert "No" in result or "no" in result
        assert "input" in result.lower() or "language" in result.lower()


class TestLanguageSet:
    """Tests for /language <lang>."""

    def test_set_french(self):
        from skills.core.language.handler import handle

        with patch("app.language_preference.set_language") as mock_set:
            ctx = _make_ctx("french")
            result = handle(ctx)

        mock_set.assert_called_once_with("french")
        assert "french" in result.lower()

    def test_set_english(self):
        from skills.core.language.handler import handle

        with patch("app.language_preference.set_language") as mock_set:
            ctx = _make_ctx("English")
            result = handle(ctx)

        mock_set.assert_called_once_with("English")
        assert "english" in result.lower()

    def test_set_with_whitespace(self):
        from skills.core.language.handler import handle

        with patch("app.language_preference.set_language") as mock_set:
            ctx = _make_ctx("  spanish  ")
            result = handle(ctx)

        mock_set.assert_called_once_with("spanish")

    def test_response_confirms_language(self):
        from skills.core.language.handler import handle

        with patch("app.language_preference.set_language"):
            ctx = _make_ctx("german")
            result = handle(ctx)

        assert "german" in result.lower()
        assert "🌐" in result


class TestLanguageReset:
    """Tests for /language reset."""

    def test_reset_calls_reset_language(self):
        from skills.core.language.handler import handle

        with patch("app.language_preference.reset_language") as mock_reset:
            ctx = _make_ctx("reset")
            result = handle(ctx)

        mock_reset.assert_called_once()

    def test_reset_case_insensitive(self):
        from skills.core.language.handler import handle

        with patch("app.language_preference.reset_language") as mock_reset:
            ctx = _make_ctx("RESET")
            result = handle(ctx)

        mock_reset.assert_called_once()

    def test_reset_response_confirms(self):
        from skills.core.language.handler import handle

        with patch("app.language_preference.reset_language"):
            ctx = _make_ctx("reset")
            result = handle(ctx)

        assert "reset" in result.lower() or "input" in result.lower()
        assert "🌐" in result
