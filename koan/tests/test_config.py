"""Tests for koan/app/config.py — configuration loading and access."""
from unittest.mock import patch


def _mock_config(config_dict):
    """Helper to mock _load_config with a specific config dict."""
    return patch("app.config._load_config", return_value=config_dict)


# --- get_chat_tools ---


class TestGetChatTools:
    def test_default_tools(self):
        from app.config import get_chat_tools

        with _mock_config({}):
            result = get_chat_tools()
        assert result == "Read,Glob,Grep"

    def test_custom_tools(self):
        from app.config import get_chat_tools

        with _mock_config({"tools": {"chat": ["Read", "Grep"]}}):
            result = get_chat_tools()
        assert result == "Read,Grep"

    def test_empty_tools_list(self):
        from app.config import get_chat_tools

        with _mock_config({"tools": {"chat": []}}):
            result = get_chat_tools()
        assert result == ""

    def test_tools_section_without_chat_key(self):
        from app.config import get_chat_tools

        with _mock_config({"tools": {"mission": ["Bash"]}}):
            result = get_chat_tools()
        assert result == "Read,Glob,Grep"


# --- get_mission_tools ---


class TestGetMissionTools:
    def test_default_tools(self):
        from app.config import get_mission_tools

        with _mock_config({}):
            result = get_mission_tools()
        assert result == "Read,Glob,Grep,Edit,Write,Bash"

    def test_custom_tools(self):
        from app.config import get_mission_tools

        with _mock_config({"tools": {"mission": ["Read", "Edit", "Bash"]}}):
            result = get_mission_tools()
        assert result == "Read,Edit,Bash"


# --- get_allowed_tools (backward compat) ---


class TestGetAllowedTools:
    def test_returns_mission_tools(self):
        from app.config import get_allowed_tools

        with _mock_config({"tools": {"mission": ["Read", "Bash"]}}):
            result = get_allowed_tools()
        assert result == "Read,Bash"


# --- get_tools_description ---


class TestGetToolsDescription:
    def test_returns_description(self):
        from app.config import get_tools_description

        with _mock_config({"tools": {"description": "Custom tool usage rules"}}):
            result = get_tools_description()
        assert result == "Custom tool usage rules"

    def test_empty_when_no_description(self):
        from app.config import get_tools_description

        with _mock_config({}):
            result = get_tools_description()
        assert result == ""

    def test_empty_when_tools_section_exists_without_description(self):
        from app.config import get_tools_description

        with _mock_config({"tools": {"chat": ["Read"]}}):
            result = get_tools_description()
        assert result == ""


# --- get_model_config ---


class TestGetModelConfig:
    def test_defaults(self):
        from app.config import get_model_config

        with _mock_config({}):
            result = get_model_config()
        assert result == {
            "mission": "",
            "chat": "",
            "lightweight": "haiku",
            "fallback": "sonnet",
            "review_mode": "",
        }

    def test_custom_models(self):
        from app.config import get_model_config

        cfg = {"models": {"mission": "opus", "chat": "sonnet", "lightweight": "flash"}}
        with _mock_config(cfg):
            result = get_model_config()
        assert result["mission"] == "opus"
        assert result["chat"] == "sonnet"
        assert result["lightweight"] == "flash"
        assert result["fallback"] == "sonnet"  # not overridden → default
        assert result["review_mode"] == ""  # not overridden → default

    def test_partial_override(self):
        from app.config import get_model_config

        with _mock_config({"models": {"review_mode": "haiku"}}):
            result = get_model_config()
        assert result["review_mode"] == "haiku"
        assert result["mission"] == ""


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

    def test_falsy_value(self):
        from app.config import get_start_on_pause

        with _mock_config({"start_on_pause": 0}):
            assert get_start_on_pause() is False


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

    def test_string_value_coerced(self):
        from app.config import get_interval_seconds

        with _mock_config({"interval_seconds": "600"}):
            assert get_interval_seconds() == 600


# --- get_fast_reply_model ---


class TestGetFastReplyModel:
    def test_disabled_by_default(self):
        from app.config import get_fast_reply_model

        with _mock_config({}):
            assert get_fast_reply_model() == ""

    def test_enabled_returns_lightweight(self):
        from app.config import get_fast_reply_model

        with _mock_config({"fast_reply": True, "models": {"lightweight": "flash"}}):
            result = get_fast_reply_model()
        assert result == "flash"

    def test_enabled_uses_default_lightweight(self):
        from app.config import get_fast_reply_model

        with _mock_config({"fast_reply": True}):
            result = get_fast_reply_model()
        assert result == "haiku"

    def test_explicitly_disabled(self):
        from app.config import get_fast_reply_model

        with _mock_config({"fast_reply": False}):
            assert get_fast_reply_model() == ""


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
    def test_no_args(self):
        from app.config import build_claude_flags

        assert build_claude_flags() == []

    def test_model_only(self):
        from app.config import build_claude_flags

        result = build_claude_flags(model="opus")
        assert result == ["--model", "opus"]

    def test_fallback_only(self):
        from app.config import build_claude_flags

        result = build_claude_flags(fallback="sonnet")
        assert result == ["--fallback-model", "sonnet"]

    def test_disallowed_tools(self):
        from app.config import build_claude_flags

        result = build_claude_flags(disallowed_tools=["Bash", "Edit"])
        assert result == ["--disallowedTools", "Bash", "Edit"]

    def test_all_flags(self):
        from app.config import build_claude_flags

        result = build_claude_flags(
            model="opus", fallback="sonnet", disallowed_tools=["Bash"]
        )
        assert result == [
            "--model", "opus",
            "--fallback-model", "sonnet",
            "--disallowedTools", "Bash",
        ]

    def test_empty_model_ignored(self):
        from app.config import build_claude_flags

        result = build_claude_flags(model="")
        assert result == []

    def test_none_disallowed_tools(self):
        from app.config import build_claude_flags

        result = build_claude_flags(disallowed_tools=None)
        assert result == []


# --- get_claude_flags_for_role ---


class TestGetClaudeFlagsForRole:
    def test_mission_default(self):
        from app.config import get_claude_flags_for_role

        with _mock_config({"models": {"fallback": "sonnet"}}):
            result = get_claude_flags_for_role("mission")
        assert "--fallback-model sonnet" in result

    def test_mission_with_model(self):
        from app.config import get_claude_flags_for_role

        with _mock_config({"models": {"mission": "opus", "fallback": ""}}):
            result = get_claude_flags_for_role("mission")
        assert result == "--model opus"

    def test_mission_review_mode_uses_review_model(self):
        from app.config import get_claude_flags_for_role

        cfg = {"models": {"mission": "opus", "review_mode": "haiku", "fallback": ""}}
        with _mock_config(cfg):
            result = get_claude_flags_for_role("mission", autonomous_mode="review")
        assert "--model haiku" in result

    def test_mission_review_mode_blocks_write_tools(self):
        from app.config import get_claude_flags_for_role

        with _mock_config({"models": {"fallback": ""}}):
            result = get_claude_flags_for_role("mission", autonomous_mode="review")
        assert "--disallowedTools" in result
        assert "Bash" in result
        assert "Edit" in result
        assert "Write" in result

    def test_contemplative_uses_lightweight(self):
        from app.config import get_claude_flags_for_role

        with _mock_config({"models": {"lightweight": "flash"}}):
            result = get_claude_flags_for_role("contemplative")
        assert result == "--model flash"

    def test_chat_with_model_and_fallback(self):
        from app.config import get_claude_flags_for_role

        cfg = {"models": {"chat": "sonnet", "fallback": "haiku"}}
        with _mock_config(cfg):
            result = get_claude_flags_for_role("chat")
        assert "--model sonnet" in result
        assert "--fallback-model haiku" in result

    def test_chat_no_model(self):
        from app.config import get_claude_flags_for_role

        with _mock_config({"models": {}}):
            result = get_claude_flags_for_role("chat")
        # No model flag, but fallback has default "sonnet"
        assert "--fallback-model sonnet" in result

    def test_unknown_role(self):
        from app.config import get_claude_flags_for_role

        with _mock_config({"models": {}}):
            result = get_claude_flags_for_role("unknown_role")
        assert result == ""

    def test_mission_no_review_model_keeps_default(self):
        from app.config import get_claude_flags_for_role

        cfg = {"models": {"mission": "opus", "review_mode": "", "fallback": ""}}
        with _mock_config(cfg):
            result = get_claude_flags_for_role("mission", autonomous_mode="review")
        # review_mode is empty, so should still use empty (not opus) — wait,
        # let's re-read the code: it checks autonomous_mode == "review" AND models["review_mode"],
        # if review_mode is empty/falsy, it falls through and uses the mission model
        assert "--model opus" in result


# --- get_auto_merge_config ---


class TestGetAutoMergeConfig:
    def test_defaults(self):
        from app.config import get_auto_merge_config

        result = get_auto_merge_config({}, "koan")
        assert result == {
            "enabled": True,
            "base_branch": "main",
            "strategy": "squash",
            "rules": [],
        }

    def test_global_config(self):
        from app.config import get_auto_merge_config

        cfg = {
            "git_auto_merge": {
                "enabled": False,
                "base_branch": "develop",
                "strategy": "merge",
                "rules": [{"pattern": "koan/*"}],
            }
        }
        result = get_auto_merge_config(cfg, "koan")
        assert result["enabled"] is False
        assert result["base_branch"] == "develop"
        assert result["strategy"] == "merge"
        assert len(result["rules"]) == 1

    def test_project_overrides_global(self):
        from app.config import get_auto_merge_config

        cfg = {
            "git_auto_merge": {
                "enabled": True,
                "base_branch": "main",
                "strategy": "squash",
            },
            "projects": {
                "backend": {
                    "git_auto_merge": {
                        "enabled": False,
                        "base_branch": "develop",
                    }
                }
            },
        }
        result = get_auto_merge_config(cfg, "backend")
        assert result["enabled"] is False
        assert result["base_branch"] == "develop"
        assert result["strategy"] == "squash"  # not overridden → global

    def test_unknown_project_uses_global(self):
        from app.config import get_auto_merge_config

        cfg = {
            "git_auto_merge": {"enabled": True, "strategy": "rebase"},
            "projects": {"backend": {"git_auto_merge": {"enabled": False}}},
        }
        result = get_auto_merge_config(cfg, "unknown-project")
        assert result["enabled"] is True
        assert result["strategy"] == "rebase"

    def test_project_rules_override_global_rules(self):
        from app.config import get_auto_merge_config

        cfg = {
            "git_auto_merge": {"rules": [{"pattern": "global/*"}]},
            "projects": {
                "koan": {
                    "git_auto_merge": {"rules": [{"pattern": "koan/*"}]}
                }
            },
        }
        result = get_auto_merge_config(cfg, "koan")
        assert result["rules"] == [{"pattern": "koan/*"}]
