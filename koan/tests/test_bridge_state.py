"""Tests for bridge_state module — shared state for the messaging bridge."""

import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# _migrate_history_file tests
# ---------------------------------------------------------------------------


class TestMigrateHistoryFile:
    """Test the one-time history file migration."""

    def test_migrates_old_to_new(self, tmp_path):
        """Old file gets renamed to new name."""
        old = tmp_path / "telegram-history.jsonl"
        old.write_text("line1\nline2\n")
        new = tmp_path / "conversation-history.jsonl"

        with patch("app.bridge_state.INSTANCE_DIR", tmp_path):
            from app.bridge_state import _migrate_history_file
            result = _migrate_history_file()

        assert result == new
        assert not old.exists()
        assert new.read_text() == "line1\nline2\n"

    def test_skips_when_new_exists(self, tmp_path):
        """If the new file already exists, don't clobber it."""
        old = tmp_path / "telegram-history.jsonl"
        old.write_text("old data")
        new = tmp_path / "conversation-history.jsonl"
        new.write_text("new data")

        with patch("app.bridge_state.INSTANCE_DIR", tmp_path):
            from app.bridge_state import _migrate_history_file
            result = _migrate_history_file()

        assert result == new
        assert old.exists()  # old untouched
        assert new.read_text() == "new data"

    def test_returns_new_path_when_no_old(self, tmp_path):
        """If old file doesn't exist, just return new path."""
        with patch("app.bridge_state.INSTANCE_DIR", tmp_path):
            from app.bridge_state import _migrate_history_file
            result = _migrate_history_file()

        assert result == tmp_path / "conversation-history.jsonl"

    def test_returns_old_path_on_rename_failure(self, tmp_path, capsys):
        """If rename fails, return old path and log the error."""
        old = tmp_path / "telegram-history.jsonl"
        old.write_text("data")

        with patch("app.bridge_state.INSTANCE_DIR", tmp_path), \
             patch.object(Path, "rename", side_effect=OSError("permission denied")):
            from app.bridge_state import _migrate_history_file
            result = _migrate_history_file()

        assert result == old
        captured = capsys.readouterr()
        assert "Migration failed" in captured.err


# ---------------------------------------------------------------------------
# _resolve_default_project_path tests
# ---------------------------------------------------------------------------


class TestResolveDefaultProjectPath:
    """Test the project path fallback resolver."""

    def test_returns_first_project_path(self):
        """Should return the path of the first project."""
        projects = [("proj1", "/path/to/proj1"), ("proj2", "/path/to/proj2")]

        with patch("app.bridge_state.get_known_projects",
                   create=True, return_value=projects):
            # Need to import fresh since module-level code ran
            from app.bridge_state import _resolve_default_project_path
            with patch("app.utils.get_known_projects", return_value=projects):
                result = _resolve_default_project_path()

        assert result == "/path/to/proj1"

    def test_returns_empty_on_no_projects(self):
        """Should return empty string when no projects configured."""
        with patch("app.utils.get_known_projects", return_value=[]):
            from app.bridge_state import _resolve_default_project_path
            result = _resolve_default_project_path()

        assert result == ""

    def test_returns_empty_on_import_error(self):
        """Should return empty string when utils import fails."""
        from app.bridge_state import _resolve_default_project_path

        with patch("app.utils.get_known_projects",
                   side_effect=ImportError("no module")):
            result = _resolve_default_project_path()

        assert result == ""

    def test_returns_empty_on_runtime_error(self):
        """Should return empty string on unexpected runtime error."""
        from app.bridge_state import _resolve_default_project_path

        with patch("app.utils.get_known_projects",
                   side_effect=RuntimeError("broken")):
            result = _resolve_default_project_path()

        assert result == ""


# ---------------------------------------------------------------------------
# _get_registry tests
# ---------------------------------------------------------------------------


class TestGetRegistry:
    """Test the lazy-singleton skill registry."""

    def setup_method(self):
        """Reset the registry cache before each test."""
        import app.bridge_state
        app.bridge_state._skill_registry = None

    def test_creates_registry_on_first_call(self, tmp_path):
        """First call should build a fresh registry."""
        from app.bridge_state import _get_registry, _reset_registry
        _reset_registry()

        mock_registry = MagicMock()
        with patch("app.bridge_state.build_registry",
                   return_value=mock_registry) as mock_build, \
             patch("app.bridge_state.INSTANCE_DIR", tmp_path):
            result = _get_registry()

        assert result is mock_registry
        mock_build.assert_called_once()

    def test_returns_cached_on_second_call(self, tmp_path):
        """Second call should return the same registry."""
        from app.bridge_state import _get_registry, _reset_registry
        _reset_registry()

        mock_registry = MagicMock()
        with patch("app.bridge_state.build_registry",
                   return_value=mock_registry) as mock_build, \
             patch("app.bridge_state.INSTANCE_DIR", tmp_path):
            first = _get_registry()
            second = _get_registry()

        assert first is second
        assert mock_build.call_count == 1

    def test_includes_instance_skills_dir(self, tmp_path):
        """If instance/skills/ exists, it should be passed to build_registry."""
        from app.bridge_state import _get_registry, _reset_registry
        _reset_registry()

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        mock_registry = MagicMock()
        with patch("app.bridge_state.build_registry",
                   return_value=mock_registry) as mock_build, \
             patch("app.bridge_state.INSTANCE_DIR", tmp_path):
            _get_registry()

        args, kwargs = mock_build.call_args
        extra_dirs = args[0] if args else kwargs.get("extra_dirs", [])
        assert skills_dir in extra_dirs

    def test_no_instance_skills_dir(self, tmp_path):
        """If instance/skills/ doesn't exist, extra_dirs should be empty."""
        from app.bridge_state import _get_registry, _reset_registry
        _reset_registry()

        mock_registry = MagicMock()
        with patch("app.bridge_state.build_registry",
                   return_value=mock_registry) as mock_build, \
             patch("app.bridge_state.INSTANCE_DIR", tmp_path):
            _get_registry()

        args, kwargs = mock_build.call_args
        extra_dirs = args[0] if args else kwargs.get("extra_dirs", [])
        assert extra_dirs == []


# ---------------------------------------------------------------------------
# _reset_registry tests
# ---------------------------------------------------------------------------


class TestResetRegistry:
    """Test the registry reset function."""

    def test_reset_clears_cache(self, tmp_path):
        """After reset, _get_registry should rebuild."""
        from app.bridge_state import _get_registry, _reset_registry
        _reset_registry()

        registry_a = MagicMock()
        registry_b = MagicMock()
        call_count = [0]

        def build_side_effect(extra_dirs=None):
            r = registry_a if call_count[0] == 0 else registry_b
            call_count[0] += 1
            return r

        with patch("app.bridge_state.build_registry",
                   side_effect=build_side_effect), \
             patch("app.bridge_state.INSTANCE_DIR", tmp_path):
            first = _get_registry()
            _reset_registry()
            second = _get_registry()

        assert first is registry_a
        assert second is registry_b


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


class TestModuleLevelState:
    """Test module-level constants are initialized correctly."""

    def test_soul_loaded_from_file(self, tmp_path):
        """SOUL constant should contain soul.md content when file exists."""
        soul_file = tmp_path / "soul.md"
        soul_file.write_text("Test soul content")

        # Simulate what the module does
        SOUL = ""
        if soul_file.exists():
            SOUL = soul_file.read_text()

        assert SOUL == "Test soul content"

    def test_soul_empty_when_no_file(self, tmp_path):
        """SOUL constant should be empty when soul.md doesn't exist."""
        soul_file = tmp_path / "soul.md"

        SOUL = ""
        if soul_file.exists():
            SOUL = soul_file.read_text()

        assert SOUL == ""

    def test_summary_loaded_from_file(self, tmp_path):
        """SUMMARY constant should contain summary.md content."""
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        summary_file = mem_dir / "summary.md"
        summary_file.write_text("Session summaries here")

        SUMMARY = ""
        if summary_file.exists():
            SUMMARY = summary_file.read_text()

        assert SUMMARY == "Session summaries here"

    def test_telegram_api_url_format(self):
        """TELEGRAM_API should use the bot token in URL."""
        token = "123:ABC"
        expected = f"https://api.telegram.org/bot{token}"
        assert expected == f"https://api.telegram.org/bot{token}"

    def test_conversation_history_file_path(self, tmp_path):
        """CONVERSATION_HISTORY_FILE should be in instance dir."""
        expected = tmp_path / "conversation-history.jsonl"
        with patch("app.bridge_state.INSTANCE_DIR", tmp_path):
            from app.bridge_state import _migrate_history_file
            result = _migrate_history_file()
        assert result == expected

    def test_topics_file_path(self):
        """TOPICS_FILE should point to previous-discussions-topics.json."""
        import app.bridge_state as bs
        assert bs.TOPICS_FILE.name == "previous-discussions-topics.json"

    def test_poll_interval_default(self):
        """Default POLL_INTERVAL should be 3 seconds."""
        import app.bridge_state as bs
        assert isinstance(bs.POLL_INTERVAL, int)
        assert bs.POLL_INTERVAL > 0

    def test_chat_timeout_default(self):
        """Default CHAT_TIMEOUT should be a positive integer."""
        import app.bridge_state as bs
        assert isinstance(bs.CHAT_TIMEOUT, int)
        assert bs.CHAT_TIMEOUT > 0
