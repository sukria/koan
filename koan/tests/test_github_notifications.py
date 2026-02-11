"""Tests for github_notifications.py — notification fetching, parsing, reactions."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.github_notifications import (
    _processed_comments,
    add_reaction,
    api_url_to_web_url,
    check_already_processed,
    check_user_permission,
    extract_comment_metadata,
    fetch_unread_notifications,
    is_notification_stale,
    is_self_mention,
    parse_mention_command,
)


class TestParseMentionCommand:
    def test_simple_command(self):
        result = parse_mention_command("@bot rebase", "bot")
        assert result == ("rebase", "")

    def test_command_with_context(self):
        result = parse_mention_command("@bot implement phase 1 only", "bot")
        assert result == ("implement", "phase 1 only")

    def test_command_case_insensitive(self):
        result = parse_mention_command("@Bot REBASE", "Bot")
        assert result == ("rebase", "")

    def test_command_with_url(self):
        result = parse_mention_command(
            "@koan rebase https://github.com/owner/repo/pull/42", "koan"
        )
        assert result == ("rebase", "https://github.com/owner/repo/pull/42")

    def test_no_mention(self):
        assert parse_mention_command("just a comment", "bot") is None

    def test_mention_in_code_block(self):
        body = "```\n@bot rebase\n```"
        assert parse_mention_command(body, "bot") is None

    def test_mention_in_inline_code(self):
        body = "use `@bot rebase` to trigger"
        assert parse_mention_command(body, "bot") is None

    def test_empty_command(self):
        # @bot with nothing after it — no word to capture
        assert parse_mention_command("@bot ", "bot") is None

    def test_empty_body(self):
        assert parse_mention_command("", "bot") is None

    def test_empty_nickname(self):
        assert parse_mention_command("@bot rebase", "") is None

    def test_mention_with_surrounding_text(self):
        body = "Hey can you please @bot rebase this PR? Thanks!"
        result = parse_mention_command(body, "bot")
        assert result == ("rebase", "this PR? Thanks!")

    def test_multiple_mentions_first_wins(self):
        body = "@bot rebase\n@bot review"
        result = parse_mention_command(body, "bot")
        assert result == ("rebase", "")


class TestApiUrlToWebUrl:
    def test_pr_url(self):
        result = api_url_to_web_url(
            "https://api.github.com/repos/sukria/koan/pulls/42"
        )
        assert result == "https://github.com/sukria/koan/pull/42"

    def test_issue_url(self):
        result = api_url_to_web_url(
            "https://api.github.com/repos/owner/repo/issues/123"
        )
        assert result == "https://github.com/owner/repo/issues/123"

    def test_already_web_url(self):
        url = "https://github.com/owner/repo/pull/1"
        assert api_url_to_web_url(url) == url


class TestFetchUnreadNotifications:
    @patch("app.github_notifications.api")
    def test_returns_mentions_only(self, mock_api):
        notifications = [
            {"reason": "mention", "repository": {"full_name": "owner/repo"}},
            {"reason": "review_requested", "repository": {"full_name": "owner/repo"}},
            {"reason": "mention", "repository": {"full_name": "other/repo"}},
        ]
        mock_api.return_value = json.dumps(notifications)

        result = fetch_unread_notifications()
        assert len(result) == 2
        assert all(n["reason"] == "mention" for n in result)

    @patch("app.github_notifications.api")
    def test_filters_by_known_repos(self, mock_api):
        notifications = [
            {"reason": "mention", "repository": {"full_name": "owner/repo"}},
            {"reason": "mention", "repository": {"full_name": "other/repo"}},
        ]
        mock_api.return_value = json.dumps(notifications)

        result = fetch_unread_notifications(known_repos={"owner/repo"})
        assert len(result) == 1
        assert result[0]["repository"]["full_name"] == "owner/repo"

    @patch("app.github_notifications.api")
    def test_handles_api_error(self, mock_api):
        mock_api.side_effect = RuntimeError("API error")
        assert fetch_unread_notifications() == []

    @patch("app.github_notifications.api")
    def test_handles_empty_response(self, mock_api):
        mock_api.return_value = ""
        assert fetch_unread_notifications() == []

    @patch("app.github_notifications.api")
    def test_handles_invalid_json(self, mock_api):
        mock_api.return_value = "not json"
        assert fetch_unread_notifications() == []


class TestCheckAlreadyProcessed:
    def setup_method(self):
        _processed_comments.clear()

    @patch("app.github_notifications.api")
    def test_in_memory_check(self, mock_api):
        _processed_comments.add("123")
        assert check_already_processed("123", "bot", "owner", "repo") is True
        mock_api.assert_not_called()

    @patch("app.github_notifications.api")
    def test_reaction_check_found(self, mock_api):
        reactions = [{"user": {"login": "bot"}, "content": "+1"}]
        mock_api.return_value = json.dumps(reactions)

        assert check_already_processed("456", "bot", "owner", "repo") is True
        assert "456" in _processed_comments

    @patch("app.github_notifications.api")
    def test_reaction_check_not_found(self, mock_api):
        reactions = [{"user": {"login": "other"}, "content": "+1"}]
        mock_api.return_value = json.dumps(reactions)

        assert check_already_processed("789", "bot", "owner", "repo") is False

    @patch("app.github_notifications.api")
    def test_api_error_returns_false(self, mock_api):
        mock_api.side_effect = RuntimeError("fail")
        assert check_already_processed("999", "bot", "owner", "repo") is False


class TestAddReaction:
    def setup_method(self):
        _processed_comments.clear()

    @patch("app.github_notifications.api")
    def test_success(self, mock_api):
        mock_api.return_value = ""
        assert add_reaction("owner", "repo", "123") is True
        assert "123" in _processed_comments

    @patch("app.github_notifications.api")
    def test_failure(self, mock_api):
        mock_api.side_effect = RuntimeError("fail")
        assert add_reaction("owner", "repo", "123") is False


class TestCheckUserPermission:
    @patch("app.github_notifications.api")
    def test_wildcard_with_write_access(self, mock_api):
        mock_api.return_value = json.dumps({"permission": "write"})
        assert check_user_permission("o", "r", "anyone", ["*"]) is True

    @patch("app.github_notifications.api")
    def test_wildcard_read_only_denied(self, mock_api):
        mock_api.return_value = json.dumps({"permission": "read"})
        assert check_user_permission("o", "r", "anyone", ["*"]) is False

    @patch("app.github_notifications.api")
    def test_not_in_allowlist(self, mock_api):
        assert check_user_permission("o", "r", "charlie", ["alice", "bob"]) is False
        mock_api.assert_not_called()

    @patch("app.github_notifications.api")
    def test_in_allowlist_with_write(self, mock_api):
        mock_api.return_value = json.dumps({"permission": "write"})
        assert check_user_permission("o", "r", "alice", ["alice"]) is True

    @patch("app.github_notifications.api")
    def test_in_allowlist_with_admin(self, mock_api):
        mock_api.return_value = json.dumps({"permission": "admin"})
        assert check_user_permission("o", "r", "alice", ["alice"]) is True

    @patch("app.github_notifications.api")
    def test_in_allowlist_read_only_denied(self, mock_api):
        mock_api.return_value = json.dumps({"permission": "read"})
        assert check_user_permission("o", "r", "alice", ["alice"]) is False


class TestIsNotificationStale:
    def test_fresh_notification(self):
        now = datetime.now(timezone.utc).isoformat()
        notif = {"updated_at": now}
        assert is_notification_stale(notif, max_age_hours=24) is False

    def test_stale_notification(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        notif = {"updated_at": old}
        assert is_notification_stale(notif, max_age_hours=24) is True

    def test_missing_timestamp(self):
        assert is_notification_stale({}) is True

    def test_invalid_timestamp(self):
        assert is_notification_stale({"updated_at": "not-a-date"}) is True


class TestIsSelfMention:
    def test_self_mention(self):
        comment = {"user": {"login": "koan-bot"}}
        assert is_self_mention(comment, "koan-bot") is True

    def test_not_self_mention(self):
        comment = {"user": {"login": "alice"}}
        assert is_self_mention(comment, "koan-bot") is False

    def test_missing_user(self):
        assert is_self_mention({}, "koan-bot") is False


class TestExtractCommentMetadata:
    def test_api_url(self):
        result = extract_comment_metadata(
            "https://api.github.com/repos/sukria/koan/issues/comments/123456"
        )
        assert result == ("sukria", "koan", "123456")

    def test_web_url_issue(self):
        result = extract_comment_metadata(
            "https://github.com/sukria/koan/issues/42#issuecomment-789"
        )
        assert result == ("sukria", "koan", "789")

    def test_web_url_pull(self):
        result = extract_comment_metadata(
            "https://github.com/owner/repo/pull/1#issuecomment-999"
        )
        assert result == ("owner", "repo", "999")

    def test_invalid_url(self):
        assert extract_comment_metadata("https://example.com/foo") is None

    def test_empty_string(self):
        assert extract_comment_metadata("") is None
