"""Tests for github_command_handler.py — notification-to-mission bridge."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.github_command_handler import (
    _error_replies,
    build_mission_from_command,
    extract_issue_number_from_notification,
    format_help_message,
    get_github_enabled_commands,
    get_github_enabled_commands_with_descriptions,
    post_error_reply,
    process_single_notification,
    resolve_project_from_notification,
    validate_command,
)
from app.skills import Skill, SkillCommand, SkillRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_skill():
    """A github-enabled skill."""
    return Skill(
        name="rebase",
        scope="core",
        description="Rebase PR",
        github_enabled=True,
        github_context_aware=False,
        commands=[SkillCommand(name="rebase", aliases=["rb"])],
    )


@pytest.fixture
def mock_context_skill():
    """A github-enabled, context-aware skill."""
    return Skill(
        name="implement",
        scope="core",
        description="Implement issue",
        github_enabled=True,
        github_context_aware=True,
        commands=[SkillCommand(name="implement", aliases=["impl"])],
    )


@pytest.fixture
def registry(mock_skill, mock_context_skill):
    """Registry with test skills."""
    reg = SkillRegistry()
    reg._register(mock_skill)
    reg._register(mock_context_skill)
    return reg


@pytest.fixture
def sample_notification():
    return {
        "id": "12345",
        "reason": "mention",
        "updated_at": "2026-02-11T20:00:00Z",
        "repository": {
            "full_name": "sukria/koan",
        },
        "subject": {
            "type": "PullRequest",
            "url": "https://api.github.com/repos/sukria/koan/pulls/42",
            "latest_comment_url": "https://api.github.com/repos/sukria/koan/issues/comments/99999",
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestValidateCommand:
    def test_valid_github_enabled(self, registry):
        skill = validate_command("rebase", registry)
        assert skill is not None
        assert skill.name == "rebase"

    def test_valid_via_alias(self, registry):
        skill = validate_command("rb", registry)
        assert skill is not None
        assert skill.name == "rebase"

    def test_unknown_command(self, registry):
        assert validate_command("nonexistent", registry) is None

    def test_not_github_enabled(self):
        skill = Skill(
            name="status",
            scope="core",
            github_enabled=False,
            commands=[SkillCommand(name="status")],
        )
        reg = SkillRegistry()
        reg._register(skill)
        assert validate_command("status", reg) is None


class TestGetGithubEnabledCommands:
    def test_lists_enabled_commands(self, registry):
        commands = get_github_enabled_commands(registry)
        assert "rebase" in commands
        assert "implement" in commands

    def test_excludes_disabled(self):
        skill = Skill(
            name="status", scope="core", github_enabled=False,
            commands=[SkillCommand(name="status")],
        )
        reg = SkillRegistry()
        reg._register(skill)
        assert get_github_enabled_commands(reg) == []

    def test_sorted(self, registry):
        commands = get_github_enabled_commands(registry)
        assert commands == sorted(commands)


class TestGetGithubEnabledCommandsWithDescriptions:
    def test_returns_name_and_description(self, registry):
        result = get_github_enabled_commands_with_descriptions(registry)
        names = [name for name, _ in result]
        assert "rebase" in names
        assert "implement" in names

    def test_uses_command_description(self):
        skill = Skill(
            name="review", scope="core", github_enabled=True,
            description="Skill description",
            commands=[SkillCommand(name="review", description="Review a PR")],
        )
        reg = SkillRegistry()
        reg._register(skill)
        result = get_github_enabled_commands_with_descriptions(reg)
        assert result == [("review", "Review a PR")]

    def test_falls_back_to_skill_description(self):
        skill = Skill(
            name="review", scope="core", github_enabled=True,
            description="Skill-level description",
            commands=[SkillCommand(name="review", description="")],
        )
        reg = SkillRegistry()
        reg._register(skill)
        result = get_github_enabled_commands_with_descriptions(reg)
        assert result == [("review", "Skill-level description")]

    def test_excludes_disabled(self):
        skill = Skill(
            name="status", scope="core", github_enabled=False,
            commands=[SkillCommand(name="status", description="Status check")],
        )
        reg = SkillRegistry()
        reg._register(skill)
        assert get_github_enabled_commands_with_descriptions(reg) == []

    def test_sorted(self, registry):
        result = get_github_enabled_commands_with_descriptions(registry)
        names = [name for name, _ in result]
        assert names == sorted(names)

    def test_deduplicates_command_names(self):
        """Two skills with same command name — first one wins."""
        skill1 = Skill(
            name="review", scope="core", github_enabled=True,
            commands=[SkillCommand(name="review", description="First")],
        )
        skill2 = Skill(
            name="review2", scope="custom", github_enabled=True,
            commands=[SkillCommand(name="review", description="Second")],
        )
        reg = SkillRegistry()
        reg._register(skill1)
        reg._register(skill2)
        result = get_github_enabled_commands_with_descriptions(reg)
        assert len(result) == 1
        assert result[0] == ("review", "First")


class TestFormatHelpMessage:
    def test_contains_invalid_command(self, registry):
        msg = format_help_message("badcmd", registry, "koanbot")
        assert "`badcmd`" in msg

    def test_lists_available_commands(self, registry):
        msg = format_help_message("badcmd", registry, "koanbot")
        assert "`@koanbot rebase`" in msg
        assert "`@koanbot implement`" in msg

    def test_includes_usage_line(self, registry):
        msg = format_help_message("badcmd", registry, "koanbot")
        assert "Usage:" in msg
        assert "`@koanbot <command>`" in msg

    def test_includes_descriptions(self):
        skill = Skill(
            name="rebase", scope="core", github_enabled=True,
            commands=[SkillCommand(name="rebase", description="Rebase a PR")],
        )
        reg = SkillRegistry()
        reg._register(skill)
        msg = format_help_message("badcmd", reg, "koanbot")
        assert "Rebase a PR" in msg

    def test_empty_registry(self):
        reg = SkillRegistry()
        msg = format_help_message("badcmd", reg, "koanbot")
        assert "`badcmd`" in msg
        assert "Usage:" in msg


class TestBuildMissionFromCommand:
    def test_simple_command(self, mock_skill, sample_notification):
        mission = build_mission_from_command(
            mock_skill, "rebase", "", sample_notification, "koan"
        )
        assert mission == "- [project:koan] /rebase https://github.com/sukria/koan/pull/42"

    def test_context_aware_with_context(self, mock_context_skill, sample_notification):
        mission = build_mission_from_command(
            mock_context_skill, "implement", "phase 1 only",
            sample_notification, "koan",
        )
        assert "phase 1 only" in mission
        assert "/implement" in mission

    def test_context_ignored_when_not_aware(self, mock_skill, sample_notification):
        mission = build_mission_from_command(
            mock_skill, "rebase", "extra context", sample_notification, "koan"
        )
        assert "extra context" not in mission

    def test_url_in_context_overrides(self, mock_context_skill, sample_notification):
        context = "https://github.com/other/repo/issues/99 phase 2"
        mission = build_mission_from_command(
            mock_context_skill, "implement", context, sample_notification, "koan"
        )
        assert "https://github.com/other/repo/issues/99" in mission

    def test_no_subject_url(self, mock_skill):
        notif = {"subject": {}}
        mission = build_mission_from_command(
            mock_skill, "rebase", "", notif, "myproject"
        )
        assert mission == "- [project:myproject] /rebase"


class TestResolveProjectFromNotification:
    @patch("app.utils.resolve_project_path", return_value="/path/to/koan")
    @patch("app.utils.project_name_for_path", return_value="koan")
    def test_known_repo(self, mock_name, mock_resolve, sample_notification):
        result = resolve_project_from_notification(sample_notification)
        assert result == ("koan", "sukria", "koan")

    @patch("app.utils.resolve_project_path", return_value=None)
    def test_unknown_repo(self, mock_resolve, sample_notification):
        assert resolve_project_from_notification(sample_notification) is None

    def test_missing_repository(self):
        assert resolve_project_from_notification({}) is None


class TestExtractIssueNumber:
    def test_issue_url(self):
        notif = {"subject": {"url": "https://api.github.com/repos/o/r/issues/42"}}
        assert extract_issue_number_from_notification(notif) == "42"

    def test_pr_url(self):
        notif = {"subject": {"url": "https://api.github.com/repos/o/r/pulls/99"}}
        assert extract_issue_number_from_notification(notif) == "99"

    def test_missing_url(self):
        assert extract_issue_number_from_notification({}) is None


class TestPostErrorReply:
    def setup_method(self):
        _error_replies.clear()

    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github.api")
    def test_posts_error(self, mock_api, mock_react):
        mock_api.return_value = ""
        result = post_error_reply("owner", "repo", "42", "123", "Test error")
        assert result is True
        mock_api.assert_called_once()

    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github.api")
    def test_deduplication(self, mock_api, mock_react):
        mock_api.return_value = ""
        post_error_reply("owner", "repo", "42", "123", "Test error")
        result = post_error_reply("owner", "repo", "42", "123", "Test error")
        assert result is False  # Duplicate
        assert mock_api.call_count == 1

    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github.api")
    def test_different_errors_not_deduplicated(self, mock_api, mock_react):
        mock_api.return_value = ""
        post_error_reply("owner", "repo", "42", "123", "Error A")
        post_error_reply("owner", "repo", "42", "123", "Error B")
        assert mock_api.call_count == 2


class TestProcessSingleNotification:
    """Integration-style tests for the full notification processing pipeline."""

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.add_reaction", return_value=True)
    @patch("app.github_command_handler.check_user_permission", return_value=True)
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler.is_self_mention", return_value=False)
    @patch("app.github_command_handler.is_notification_stale", return_value=False)
    @patch("app.github_command_handler.get_comment_from_notification")
    @patch("app.github_command_handler.resolve_project_from_notification")
    @patch("app.utils.insert_pending_mission")
    def test_happy_path(
        self, mock_insert, mock_resolve, mock_get_comment,
        mock_stale, mock_self, mock_processed, mock_perm,
        mock_react, mock_read, registry, sample_notification, tmp_path,
    ):
        mock_resolve.return_value = ("koan", "sukria", "koan")
        mock_get_comment.return_value = {
            "id": 99999,
            "body": "@testbot rebase",
            "user": {"login": "alice"},
        }

        config = {"github": {"nickname": "testbot", "authorized_users": ["*"]}}

        with patch.dict("os.environ", {"KOAN_ROOT": str(tmp_path)}):
            success, error = process_single_notification(
                sample_notification, registry, config, None, "testbot",
            )

        assert success is True
        assert error is None
        mock_insert.assert_called_once()
        mock_react.assert_called_once()

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.is_notification_stale", return_value=True)
    def test_stale_notification_skipped(self, mock_stale, mock_read, registry, sample_notification):
        success, error = process_single_notification(
            sample_notification, registry, {}, None, "bot",
        )
        assert success is False
        assert error is None

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.is_notification_stale", return_value=False)
    @patch("app.github_command_handler.get_comment_from_notification", return_value=None)
    def test_no_comment_skipped(self, mock_comment, mock_stale, mock_read, registry, sample_notification):
        success, error = process_single_notification(
            sample_notification, registry, {}, None, "bot",
        )
        assert success is False

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler.is_self_mention", return_value=False)
    @patch("app.github_command_handler.is_notification_stale", return_value=False)
    @patch("app.github_command_handler.get_comment_from_notification")
    @patch("app.github_command_handler.resolve_project_from_notification", return_value=None)
    def test_unknown_repo_error(
        self, mock_resolve, mock_comment, mock_stale, mock_self,
        mock_processed, mock_read, registry, sample_notification,
    ):
        mock_comment.return_value = {
            "id": 99999, "body": "@bot rebase", "user": {"login": "alice"},
        }
        config = {"github": {"nickname": "bot"}}

        success, error = process_single_notification(
            sample_notification, registry, config, None, "bot",
        )
        assert success is False
        assert "Unknown repository" in error

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler.is_self_mention", return_value=False)
    @patch("app.github_command_handler.is_notification_stale", return_value=False)
    @patch("app.github_command_handler.get_comment_from_notification")
    @patch("app.github_command_handler.resolve_project_from_notification")
    def test_invalid_command_returns_help(
        self, mock_resolve, mock_comment, mock_stale, mock_self,
        mock_processed, mock_read, registry, sample_notification,
    ):
        mock_resolve.return_value = ("koan", "sukria", "koan")
        mock_comment.return_value = {
            "id": 99999, "body": "@testbot badcmd",
            "user": {"login": "alice"},
        }
        config = {"github": {"nickname": "testbot"}}

        success, error = process_single_notification(
            sample_notification, registry, config, None, "testbot",
        )
        assert success is False
        assert "`badcmd`" in error
        assert "`@testbot rebase`" in error
        assert "`@testbot implement`" in error
        assert "Usage:" in error

    @patch("app.github_command_handler.mark_notification_read")
    @patch("app.github_command_handler.check_already_processed", return_value=False)
    @patch("app.github_command_handler.is_self_mention", return_value=False)
    @patch("app.github_command_handler.is_notification_stale", return_value=False)
    @patch("app.github_command_handler.get_comment_from_notification")
    @patch("app.github_command_handler.resolve_project_from_notification")
    def test_invalid_command_marks_notification_read(
        self, mock_resolve, mock_comment, mock_stale, mock_self,
        mock_processed, mock_read, registry, sample_notification,
    ):
        mock_resolve.return_value = ("koan", "sukria", "koan")
        mock_comment.return_value = {
            "id": 99999, "body": "@testbot badcmd",
            "user": {"login": "alice"},
        }
        config = {"github": {"nickname": "testbot"}}

        process_single_notification(
            sample_notification, registry, config, None, "testbot",
        )
        # Notification should be marked as read for invalid commands
        mock_read.assert_called_with("12345")
