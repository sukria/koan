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


class TestLanguageShortcuts:
    """Tests for /french, /english, and alias shortcuts."""

    def _make_shortcut_ctx(self, command_name, args=""):
        ctx = MagicMock(spec=SkillContext)
        ctx.command_name = command_name
        ctx.args = args
        return ctx

    def test_french_command(self):
        from skills.core.language.handler import handle

        with patch("app.language_preference.set_language") as mock_set:
            ctx = self._make_shortcut_ctx("french")
            result = handle(ctx)

        mock_set.assert_called_once_with("french")
        assert "french" in result.lower()
        assert "🌐" in result

    def test_english_command(self):
        from skills.core.language.handler import handle

        with patch("app.language_preference.set_language") as mock_set:
            ctx = self._make_shortcut_ctx("english")
            result = handle(ctx)

        mock_set.assert_called_once_with("english")
        assert "english" in result.lower()
        assert "🌐" in result

    def test_fr_alias(self):
        from skills.core.language.handler import handle

        with patch("app.language_preference.set_language") as mock_set:
            ctx = self._make_shortcut_ctx("fr")
            result = handle(ctx)

        mock_set.assert_called_once_with("french")
        assert "french" in result.lower()

    def test_en_alias(self):
        from skills.core.language.handler import handle

        with patch("app.language_preference.set_language") as mock_set:
            ctx = self._make_shortcut_ctx("en")
            result = handle(ctx)

        mock_set.assert_called_once_with("english")
        assert "english" in result.lower()

    def test_francais_alias(self):
        from skills.core.language.handler import handle

        with patch("app.language_preference.set_language") as mock_set:
            ctx = self._make_shortcut_ctx("francais")
            result = handle(ctx)

        mock_set.assert_called_once_with("french")

    def test_anglais_alias(self):
        from skills.core.language.handler import handle

        with patch("app.language_preference.set_language") as mock_set:
            ctx = self._make_shortcut_ctx("anglais")
            result = handle(ctx)

        mock_set.assert_called_once_with("english")

    def test_shortcut_ignores_args(self):
        """Shortcut commands set language regardless of args."""
        from skills.core.language.handler import handle

        with patch("app.language_preference.set_language") as mock_set:
            ctx = self._make_shortcut_ctx("french", args="something")
            result = handle(ctx)

        mock_set.assert_called_once_with("french")
        assert "french" in result.lower()

    def test_language_command_not_a_shortcut(self):
        """The base /language command should not be treated as a shortcut."""
        from skills.core.language.handler import handle

        with patch("app.language_preference.get_language", return_value=None):
            ctx = self._make_shortcut_ctx("language")
            result = handle(ctx)

        assert "Usage:" in result

    def test_no_args_usage_mentions_shortcuts(self):
        """Usage text should mention /french and /english shortcuts."""
        from skills.core.language.handler import handle

        with patch("app.language_preference.get_language", return_value=None):
            ctx = _make_ctx("")
            result = handle(ctx)

        assert "/french" in result
        assert "/english" in result


class TestLanguageSkillRegistration:
    """Tests that /french and /english are properly registered as commands."""

    def test_skill_md_has_french_command(self):
        from app.skills import parse_skill_md
        from pathlib import Path

        skill_md = Path(__file__).parent.parent / "skills" / "core" / "language" / "SKILL.md"
        skill = parse_skill_md(skill_md)

        command_names = [c.name for c in skill.commands]
        assert "french" in command_names

    def test_skill_md_has_english_command(self):
        from app.skills import parse_skill_md
        from pathlib import Path

        skill_md = Path(__file__).parent.parent / "skills" / "core" / "language" / "SKILL.md"
        skill = parse_skill_md(skill_md)

        command_names = [c.name for c in skill.commands]
        assert "english" in command_names

    def test_french_aliases_registered(self):
        from app.skills import parse_skill_md
        from pathlib import Path

        skill_md = Path(__file__).parent.parent / "skills" / "core" / "language" / "SKILL.md"
        skill = parse_skill_md(skill_md)

        french_cmd = [c for c in skill.commands if c.name == "french"][0]
        assert "fr" in french_cmd.aliases
        assert "francais" in french_cmd.aliases

    def test_english_aliases_registered(self):
        from app.skills import parse_skill_md
        from pathlib import Path

        skill_md = Path(__file__).parent.parent / "skills" / "core" / "language" / "SKILL.md"
        skill = parse_skill_md(skill_md)

        english_cmd = [c for c in skill.commands if c.name == "english"][0]
        assert "en" in english_cmd.aliases
        assert "anglais" in english_cmd.aliases

    def test_registry_finds_french_command(self):
        from app.skills import build_registry

        registry = build_registry()
        skill = registry.find_by_command("french")
        assert skill is not None
        assert skill.name == "language"

    def test_registry_finds_english_command(self):
        from app.skills import build_registry

        registry = build_registry()
        skill = registry.find_by_command("english")
        assert skill is not None
        assert skill.name == "language"

    def test_registry_finds_fr_alias(self):
        from app.skills import build_registry

        registry = build_registry()
        skill = registry.find_by_command("fr")
        assert skill is not None
        assert skill.name == "language"

    def test_registry_finds_en_alias(self):
        from app.skills import build_registry

        registry = build_registry()
        skill = registry.find_by_command("en")
        assert skill is not None
        assert skill.name == "language"
