"""Tests for app.config — configuration loading and access."""

import os
from contextlib import contextmanager
from unittest.mock import patch

import pytest


@contextmanager
def _mock_config(data: dict):
    """Mock load_config to return a specific config dict."""
    with patch("app.config._load_config", return_value=data):
        yield


# --- get_chat_tools ---


class TestGetChatTools:
    def test_default(self):
        from app.config import get_chat_tools

        with _mock_config({}):
            assert get_chat_tools() == "Read,Glob,Grep"

    def test_custom(self):
        from app.config import get_chat_tools

        with _mock_config({"tools": {"chat": ["Read", "Write"]}}):
            assert get_chat_tools() == "Read,Write"


# --- get_mission_tools ---


class TestGetMissionTools:
    def test_default(self):
        from app.config import get_mission_tools

        with _mock_config({}):
            assert get_mission_tools() == "Read,Glob,Grep,Edit,Write,Bash"

    def test_custom(self):
        from app.config import get_mission_tools

        with _mock_config({"tools": {"mission": ["Read", "Bash"]}}):
            assert get_mission_tools() == "Read,Bash"


# --- get_allowed_tools (backward compat) ---


class TestGetAllowedTools:
    def test_delegates_to_mission_tools(self):
        from app.config import get_allowed_tools

        with _mock_config({}):
            assert get_allowed_tools() == "Read,Glob,Grep,Edit,Write,Bash"


# --- get_tools_description ---


class TestGetToolsDescription:
    def test_default_empty(self):
        from app.config import get_tools_description

        with _mock_config({}):
            assert get_tools_description() == ""

    def test_custom(self):
        from app.config import get_tools_description

        with _mock_config({"tools": {"description": "Tools info"}}):
            assert get_tools_description() == "Tools info"


# --- get_model_config ---


class TestGetModelConfig:
    def test_defaults(self):
        from app.config import get_model_config

        with _mock_config({}):
            result = get_model_config()
        assert result["mission"] == ""
        assert result["chat"] == ""
        assert result["lightweight"] == "haiku"
        assert result["fallback"] == "sonnet"
        assert result["review_mode"] == ""

    def test_custom_models(self):
        from app.config import get_model_config

        with _mock_config({"models": {"mission": "opus", "chat": "sonnet"}}):
            result = get_model_config()
        assert result["mission"] == "opus"
        assert result["chat"] == "sonnet"
        assert result["lightweight"] == "haiku"  # not overridden


# --- get_start_on_pause ---


class TestGetStartOnPause:
    def test_default_false(self):
        from app.config import get_start_on_pause

        with _mock_config({}):
            assert get_start_on_pause() is False

    def test_enabled(self):
        from app.config import get_start_on_pause

        with _mock_config({"start_on_pause": True}):
            assert get_start_on_pause() is True


# --- get_max_runs ---


class TestGetMaxRuns:
    def test_default(self):
        from app.config import get_max_runs

        with _mock_config({}):
            assert get_max_runs() == 20

    def test_custom(self):
        from app.config import get_max_runs

        with _mock_config({"max_runs_per_day": 50}):
            assert get_max_runs() == 50

    def test_string_value_coerced(self):
        from app.config import get_max_runs

        with _mock_config({"max_runs_per_day": "30"}):
            assert get_max_runs() == 30


# --- get_interval_seconds ---


class TestGetIntervalSeconds:
    def test_default(self):
        from app.config import get_interval_seconds

        with _mock_config({}):
            assert get_interval_seconds() == 300

    def test_custom(self):
        from app.config import get_interval_seconds

        with _mock_config({"interval_seconds": 120}):
            assert get_interval_seconds() == 120


# --- get_fast_reply_model ---


class TestGetFastReplyModel:
    def test_disabled_by_default(self):
        from app.config import get_fast_reply_model

        with _mock_config({}):
            assert get_fast_reply_model() == ""

    def test_enabled_returns_lightweight(self):
        from app.config import get_fast_reply_model

        with _mock_config({"fast_reply": True, "models": {"lightweight": "flash"}}):
            assert get_fast_reply_model() == "flash"

    def test_enabled_uses_default_lightweight(self):
        from app.config import get_fast_reply_model

        with _mock_config({"fast_reply": True}):
            assert get_fast_reply_model() == "haiku"


# --- get_branch_prefix ---


class TestGetBranchPrefix:
    def test_default(self):
        from app.config import get_branch_prefix

        with _mock_config({}):
            assert get_branch_prefix() == "koan/"

    def test_custom(self):
        from app.config import get_branch_prefix

        with _mock_config({"branch_prefix": "mybot"}):
            assert get_branch_prefix() == "mybot/"

    def test_strips_trailing_slash(self):
        from app.config import get_branch_prefix

        with _mock_config({"branch_prefix": "agent/"}):
            assert get_branch_prefix() == "agent/"

    def test_empty_string_defaults_to_koan(self):
        from app.config import get_branch_prefix

        with _mock_config({"branch_prefix": ""}):
            assert get_branch_prefix() == "koan/"


# --- get_contemplative_chance ---


class TestGetContemplativeChance:
    def test_default(self):
        from app.config import get_contemplative_chance

        with _mock_config({}):
            assert get_contemplative_chance() == 10

    def test_custom(self):
        from app.config import get_contemplative_chance

        with _mock_config({"contemplative_chance": 25}):
            assert get_contemplative_chance() == 25

    def test_zero(self):
        from app.config import get_contemplative_chance

        with _mock_config({"contemplative_chance": 0}):
            assert get_contemplative_chance() == 0


# --- build_claude_flags ---


class TestBuildClaudeFlags:
    def test_empty_returns_empty(self):
        from app.config import build_claude_flags

        with patch("app.cli_provider.build_cli_flags", return_value=[]):
            result = build_claude_flags()
        assert result == []

    def test_with_model(self):
        from app.config import build_claude_flags

        with patch("app.cli_provider.build_cli_flags", return_value=["--model", "opus"]) as mock:
            result = build_claude_flags(model="opus")
        mock.assert_called_once_with(model="opus", fallback="", disallowed_tools=None)
        assert result == ["--model", "opus"]


# --- get_auto_merge_config ---


class TestGetAutoMergeConfig:
    def test_defaults(self):
        from app.config import get_auto_merge_config

        config = {}
        result = get_auto_merge_config(config, "myproject")
        assert result["enabled"] is True
        assert result["base_branch"] == "main"
        assert result["strategy"] == "squash"
        assert result["rules"] == []

    def test_global_config(self):
        from app.config import get_auto_merge_config

        config = {"git_auto_merge": {"enabled": False, "strategy": "rebase"}}
        result = get_auto_merge_config(config, "myproject")
        assert result["enabled"] is False
        assert result["strategy"] == "rebase"

    def test_config_yaml_projects_section_ignored(self):
        """config.yaml projects: section is no longer used for per-project overrides.

        Per-project auto-merge config is now exclusively in projects.yaml.
        """
        from app.config import get_auto_merge_config

        config = {
            "git_auto_merge": {"enabled": True, "strategy": "squash"},
            "projects": {"myproject": {"git_auto_merge": {"strategy": "merge"}}},
        }
        result = get_auto_merge_config(config, "myproject")
        assert result["enabled"] is True
        # Should use global config, not the projects section override
        assert result["strategy"] == "squash"


# --- backward compatibility ---


class TestBackwardCompat:
    """Verify that importing from app.utils still works."""

    def test_config_functions_accessible_from_utils(self):
        from app.utils import get_chat_tools, get_model_config, get_branch_prefix
        # Just verify they're importable (not None)
        assert callable(get_chat_tools)
        assert callable(get_model_config)
        assert callable(get_branch_prefix)
