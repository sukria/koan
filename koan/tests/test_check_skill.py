"""Tests for the /check core skill — PR and issue status checking."""

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from app.skills import SkillContext


# ---------------------------------------------------------------------------
# Import handler
# ---------------------------------------------------------------------------

HANDLER_PATH = Path(__file__).parent.parent / "skills" / "core" / "check" / "handler.py"


def _load_handler():
    spec = importlib.util.spec_from_file_location("check_handler", str(HANDLER_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def handler():
    return _load_handler()


@pytest.fixture
def ctx(tmp_path):
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    missions_md = instance_dir / "missions.md"
    missions_md.write_text("## En attente\n\n## En cours\n\n## Terminées\n")
    return SkillContext(
        koan_root=tmp_path,
        instance_dir=instance_dir,
        command_name="check",
        args="",
        send_message=MagicMock(),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pr_json(**overrides):
    """Build a realistic PR metadata dict."""
    base = {
        "state": "OPEN",
        "mergeable": "MERGEABLE",
        "reviewDecision": "",
        "updatedAt": "2026-02-07T10:00:00Z",
        "headRefName": "koan/fix-xyz",
        "baseRefName": "main",
        "title": "Fix XYZ",
        "isDraft": False,
        "author": {"login": "koan-bot"},
        "url": "https://github.com/sukria/koan/pull/99",
    }
    base.update(overrides)
    return base


def _issue_json(**overrides):
    """Build a realistic issue metadata dict."""
    base = {
        "state": "open",
        "updatedAt": "2026-02-07T10:00:00Z",
        "title": "Improve performance",
        "url": "https://github.com/sukria/koan/issues/42",
        "comments": 3,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# handle() — usage / routing
# ---------------------------------------------------------------------------

class TestHandleRouting:
    def test_no_args_returns_usage(self, handler, ctx):
        result = handler.handle(ctx)
        assert "Usage:" in result
        assert "/check" in result

    def test_invalid_url_returns_error(self, handler, ctx):
        ctx.args = "not-a-url"
        result = handler.handle(ctx)
        assert "\u274c" in result
        assert "No valid" in result

    def test_random_github_url_returns_error(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan"
        result = handler.handle(ctx)
        assert "\u274c" in result

    def test_pr_url_detected(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/42"
        with patch.object(handler, "_handle_pr", return_value="pr result") as mock:
            result = handler.handle(ctx)
            assert result == "pr result"
            mock.assert_called_once()

    def test_issue_url_detected(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/issues/42"
        with patch.object(handler, "_handle_issue", return_value="issue result") as mock:
            result = handler.handle(ctx)
            assert result == "issue result"
            mock.assert_called_once()

    def test_pr_url_takes_priority_over_issue(self, handler, ctx):
        """A PR URL should route to _handle_pr even if issue pattern also exists."""
        ctx.args = "https://github.com/sukria/koan/pull/42"
        with patch.object(handler, "_handle_pr", return_value="pr") as pr_mock, \
             patch.object(handler, "_handle_issue") as issue_mock:
            handler.handle(ctx)
            pr_mock.assert_called_once()
            issue_mock.assert_not_called()

    def test_url_in_surrounding_text(self, handler, ctx):
        ctx.args = "please check https://github.com/sukria/koan/pull/99 thanks"
        with patch.object(handler, "_handle_pr", return_value="ok") as mock:
            handler.handle(ctx)
            mock.assert_called_once()


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

class TestCanonicalUrl:
    def test_pr_url(self, handler):
        url = handler._canonical_url("sukria", "koan", "pull", "42")
        assert url == "https://github.com/sukria/koan/pull/42"

    def test_issue_url(self, handler):
        url = handler._canonical_url("sukria", "koan", "issues", "7")
        assert url == "https://github.com/sukria/koan/issues/7"


# ---------------------------------------------------------------------------
# PR metadata helpers
# ---------------------------------------------------------------------------

class TestPrHelpers:
    def test_needs_rebase_conflicting(self, handler):
        assert handler._needs_rebase({"mergeable": "CONFLICTING"}) is True

    def test_needs_rebase_mergeable(self, handler):
        assert handler._needs_rebase({"mergeable": "MERGEABLE"}) is False

    def test_needs_rebase_unknown(self, handler):
        assert handler._needs_rebase({"mergeable": "UNKNOWN"}) is False

    def test_has_no_reviews_empty(self, handler):
        assert handler._has_no_reviews({"reviewDecision": ""}) is True

    def test_has_no_reviews_none(self, handler):
        assert handler._has_no_reviews({"reviewDecision": None}) is True

    def test_has_no_reviews_approved(self, handler):
        assert handler._has_no_reviews({"reviewDecision": "APPROVED"}) is False

    def test_has_no_reviews_changes_requested(self, handler):
        assert handler._has_no_reviews({"reviewDecision": "CHANGES_REQUESTED"}) is False


# ---------------------------------------------------------------------------
# _handle_pr — closed/merged PRs
# ---------------------------------------------------------------------------

class TestHandlePrClosed:
    def test_closed_pr_no_action(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/42"
        pr_data = _pr_json(state="CLOSED")
        with patch.object(handler, "_fetch_pr_metadata", return_value=pr_data), \
             patch("app.check_tracker.mark_checked") as mock_mark:
            result = handler._handle_pr(ctx, handler._PR_URL_RE.search(ctx.args))
            assert "closed" in result.lower()
            assert "No action" in result
            mock_mark.assert_called_once()

    def test_merged_pr_no_action(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/42"
        pr_data = _pr_json(state="MERGED")
        with patch.object(handler, "_fetch_pr_metadata", return_value=pr_data), \
             patch("app.check_tracker.mark_checked"):
            result = handler._handle_pr(ctx, handler._PR_URL_RE.search(ctx.args))
            assert "merged" in result.lower()


# ---------------------------------------------------------------------------
# _handle_pr — no changes since last check
# ---------------------------------------------------------------------------

class TestHandlePrNoChanges:
    def test_skip_when_no_updates(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/42"
        pr_data = _pr_json(updatedAt="2026-02-07T10:00:00Z")
        with patch.object(handler, "_fetch_pr_metadata", return_value=pr_data), \
             patch("app.check_tracker.has_changed", return_value=False):
            result = handler._handle_pr(ctx, handler._PR_URL_RE.search(ctx.args))
            assert "no updates" in result.lower()
            assert "Skipping" in result


# ---------------------------------------------------------------------------
# _handle_pr — rebase needed
# ---------------------------------------------------------------------------

class TestHandlePrRebase:
    def test_conflicting_pr_queues_rebase(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/42"
        pr_data = _pr_json(mergeable="CONFLICTING")
        with patch.object(handler, "_fetch_pr_metadata", return_value=pr_data), \
             patch("app.check_tracker.has_changed", return_value=True), \
             patch("app.check_tracker.mark_checked"), \
             patch("app.utils.insert_pending_mission") as mock_insert, \
             patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            result = handler._handle_pr(ctx, handler._PR_URL_RE.search(ctx.args))
            assert "Rebase queued" in result
            mock_insert.assert_called_once()
            entry = mock_insert.call_args[0][1]
            assert "Rebase PR #42" in entry

    def test_rebase_mission_has_correct_command(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/42"
        pr_data = _pr_json(mergeable="CONFLICTING")
        with patch.object(handler, "_fetch_pr_metadata", return_value=pr_data), \
             patch("app.check_tracker.has_changed", return_value=True), \
             patch("app.check_tracker.mark_checked"), \
             patch("app.utils.insert_pending_mission") as mock_insert, \
             patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            handler._handle_pr(ctx, handler._PR_URL_RE.search(ctx.args))
            entry = mock_insert.call_args[0][1]
            assert "app.rebase_pr" in entry
            assert "--project-path /home/koan" in entry


# ---------------------------------------------------------------------------
# _handle_pr — review needed
# ---------------------------------------------------------------------------

class TestHandlePrReview:
    def test_no_review_queues_pr_review(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/42"
        pr_data = _pr_json(reviewDecision="", isDraft=False, mergeable="MERGEABLE")
        with patch.object(handler, "_fetch_pr_metadata", return_value=pr_data), \
             patch("app.check_tracker.has_changed", return_value=True), \
             patch("app.check_tracker.mark_checked"), \
             patch("app.utils.insert_pending_mission") as mock_insert, \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            result = handler._handle_pr(ctx, handler._PR_URL_RE.search(ctx.args))
            assert "review queued" in result.lower()
            mock_insert.assert_called_once()
            entry = mock_insert.call_args[0][1]
            assert "/pr" in entry
            assert "Review PR #42" in entry

    def test_draft_pr_no_review(self, handler, ctx):
        """Draft PRs should NOT trigger review even without reviews."""
        ctx.args = "https://github.com/sukria/koan/pull/42"
        pr_data = _pr_json(reviewDecision="", isDraft=True, mergeable="MERGEABLE")
        with patch.object(handler, "_fetch_pr_metadata", return_value=pr_data), \
             patch("app.check_tracker.has_changed", return_value=True), \
             patch("app.check_tracker.mark_checked"), \
             patch("app.utils.insert_pending_mission") as mock_insert, \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            result = handler._handle_pr(ctx, handler._PR_URL_RE.search(ctx.args))
            mock_insert.assert_not_called()
            assert "No action needed" in result

    def test_approved_pr_no_review_queued(self, handler, ctx):
        """PRs with reviews should NOT trigger review."""
        ctx.args = "https://github.com/sukria/koan/pull/42"
        pr_data = _pr_json(reviewDecision="APPROVED", isDraft=False, mergeable="MERGEABLE")
        with patch.object(handler, "_fetch_pr_metadata", return_value=pr_data), \
             patch("app.check_tracker.has_changed", return_value=True), \
             patch("app.check_tracker.mark_checked"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            result = handler._handle_pr(ctx, handler._PR_URL_RE.search(ctx.args))
            assert "No action needed" in result

    def test_conflicting_pr_no_review_queued(self, handler, ctx):
        """When rebase is needed, skip review (rebase first)."""
        ctx.args = "https://github.com/sukria/koan/pull/42"
        pr_data = _pr_json(
            reviewDecision="", isDraft=False, mergeable="CONFLICTING"
        )
        with patch.object(handler, "_fetch_pr_metadata", return_value=pr_data), \
             patch("app.check_tracker.has_changed", return_value=True), \
             patch("app.check_tracker.mark_checked"), \
             patch("app.utils.insert_pending_mission") as mock_insert, \
             patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            result = handler._handle_pr(ctx, handler._PR_URL_RE.search(ctx.args))
            # Only rebase should be queued, not review
            assert mock_insert.call_count == 1
            entry = mock_insert.call_args[0][1]
            assert "Rebase" in entry


# ---------------------------------------------------------------------------
# _handle_pr — clean status report
# ---------------------------------------------------------------------------

class TestHandlePrCleanStatus:
    def test_clean_pr_reports_status(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/42"
        pr_data = _pr_json(
            reviewDecision="APPROVED",
            mergeable="MERGEABLE",
            headRefName="koan/fix-xyz",
            baseRefName="main",
        )
        with patch.object(handler, "_fetch_pr_metadata", return_value=pr_data), \
             patch("app.check_tracker.has_changed", return_value=True), \
             patch("app.check_tracker.mark_checked"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            result = handler._handle_pr(ctx, handler._PR_URL_RE.search(ctx.args))
            assert "\u2705" in result
            assert "koan/fix-xyz" in result
            assert "main" in result
            assert "No action needed" in result


# ---------------------------------------------------------------------------
# _handle_pr — fetch error
# ---------------------------------------------------------------------------

class TestHandlePrFetchError:
    def test_fetch_error_returns_message(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/42"
        with patch.object(handler, "_fetch_pr_metadata",
                          side_effect=RuntimeError("timeout")):
            result = handler._handle_pr(ctx, handler._PR_URL_RE.search(ctx.args))
            assert "\u274c" in result
            assert "timeout" in result


# ---------------------------------------------------------------------------
# _handle_pr — send_message notifications
# ---------------------------------------------------------------------------

class TestHandlePrNotifications:
    def test_sends_checking_notification(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/42"
        pr_data = _pr_json()
        with patch.object(handler, "_fetch_pr_metadata", return_value=pr_data), \
             patch("app.check_tracker.has_changed", return_value=True), \
             patch("app.check_tracker.mark_checked"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            handler._handle_pr(ctx, handler._PR_URL_RE.search(ctx.args))
            ctx.send_message.assert_called_once()
            msg = ctx.send_message.call_args[0][0]
            assert "Checking PR #42" in msg


# ---------------------------------------------------------------------------
# _handle_issue — closed issue
# ---------------------------------------------------------------------------

class TestHandleIssueClosed:
    def test_closed_issue_no_action(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/issues/42"
        issue_data = _issue_json(state="closed")
        with patch.object(handler, "_fetch_issue_metadata", return_value=issue_data), \
             patch("app.check_tracker.mark_checked") as mock_mark:
            result = handler._handle_issue(ctx, handler._ISSUE_URL_RE.search(ctx.args))
            assert "closed" in result.lower()
            mock_mark.assert_called_once()


# ---------------------------------------------------------------------------
# _handle_issue — no changes
# ---------------------------------------------------------------------------

class TestHandleIssueNoChanges:
    def test_skip_when_no_updates(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/issues/42"
        issue_data = _issue_json()
        with patch.object(handler, "_fetch_issue_metadata", return_value=issue_data), \
             patch("app.check_tracker.has_changed", return_value=False):
            result = handler._handle_issue(ctx, handler._ISSUE_URL_RE.search(ctx.args))
            assert "no updates" in result.lower()


# ---------------------------------------------------------------------------
# _handle_issue — plan queued
# ---------------------------------------------------------------------------

class TestHandleIssuePlan:
    def test_updated_issue_queues_plan(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/issues/42"
        issue_data = _issue_json()
        with patch.object(handler, "_fetch_issue_metadata", return_value=issue_data), \
             patch("app.check_tracker.has_changed", return_value=True), \
             patch("app.check_tracker.mark_checked"), \
             patch("app.utils.insert_pending_mission") as mock_insert, \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            result = handler._handle_issue(ctx, handler._ISSUE_URL_RE.search(ctx.args))
            assert "/plan queued" in result.lower()
            mock_insert.assert_called_once()
            entry = mock_insert.call_args[0][1]
            assert "/plan" in entry
            assert "github.com/sukria/koan/issues/42" in entry

    def test_plan_mission_has_project_tag(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/issues/42"
        issue_data = _issue_json()
        with patch.object(handler, "_fetch_issue_metadata", return_value=issue_data), \
             patch("app.check_tracker.has_changed", return_value=True), \
             patch("app.check_tracker.mark_checked"), \
             patch("app.utils.insert_pending_mission") as mock_insert, \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            handler._handle_issue(ctx, handler._ISSUE_URL_RE.search(ctx.args))
            entry = mock_insert.call_args[0][1]
            assert "[project:koan]" in entry


# ---------------------------------------------------------------------------
# _handle_issue — fetch error
# ---------------------------------------------------------------------------

class TestHandleIssueFetchError:
    def test_fetch_error_returns_message(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/issues/42"
        with patch.object(handler, "_fetch_issue_metadata",
                          side_effect=RuntimeError("not found")):
            result = handler._handle_issue(ctx, handler._ISSUE_URL_RE.search(ctx.args))
            assert "\u274c" in result
            assert "not found" in result


# ---------------------------------------------------------------------------
# _resolve_project_name
# ---------------------------------------------------------------------------

class TestResolveProjectName:
    def test_exact_match(self, handler):
        with patch("app.utils.get_known_projects",
                    return_value=[("koan", "/home/koan")]):
            assert handler._resolve_project_name("koan") == "koan"

    def test_case_insensitive(self, handler):
        with patch("app.utils.get_known_projects",
                    return_value=[("Koan", "/home/koan")]):
            assert handler._resolve_project_name("koan") == "Koan"

    def test_unknown_repo_returns_repo_name(self, handler):
        with patch("app.utils.get_known_projects",
                    return_value=[("other", "/other")]):
            assert handler._resolve_project_name("myrepo") == "myrepo"


# ---------------------------------------------------------------------------
# SKILL.md — structure validation
# ---------------------------------------------------------------------------

class TestSkillMd:
    def test_skill_md_parses(self):
        from app.skills import parse_skill_md
        skill_path = Path(__file__).parent.parent / "skills" / "core" / "check" / "SKILL.md"
        skill = parse_skill_md(skill_path)
        assert skill is not None
        assert skill.name == "check"
        assert skill.scope == "core"
        assert skill.worker is True
        assert len(skill.commands) == 1
        assert skill.commands[0].name == "check"

    def test_skill_has_alias(self):
        from app.skills import parse_skill_md
        skill_path = Path(__file__).parent.parent / "skills" / "core" / "check" / "SKILL.md"
        skill = parse_skill_md(skill_path)
        assert "inspect" in skill.commands[0].aliases

    def test_skill_registered_in_registry(self):
        from app.skills import build_registry
        registry = build_registry()
        skill = registry.find_by_command("check")
        assert skill is not None
        assert skill.name == "check"

    def test_alias_registered_in_registry(self):
        from app.skills import build_registry
        registry = build_registry()
        skill = registry.find_by_command("inspect")
        assert skill is not None
        assert skill.name == "check"

    def test_skill_handler_exists(self):
        assert HANDLER_PATH.exists()


# ---------------------------------------------------------------------------
# _handle_issue — send_message notification
# ---------------------------------------------------------------------------

class TestHandleIssueNotifications:
    def test_sends_checking_notification(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/issues/42"
        issue_data = _issue_json()
        with patch.object(handler, "_fetch_issue_metadata", return_value=issue_data), \
             patch("app.check_tracker.has_changed", return_value=True), \
             patch("app.check_tracker.mark_checked"), \
             patch("app.utils.insert_pending_mission"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            handler._handle_issue(ctx, handler._ISSUE_URL_RE.search(ctx.args))
            ctx.send_message.assert_called_once()
            msg = ctx.send_message.call_args[0][0]
            assert "Checking issue #42" in msg

    def test_no_notification_when_send_message_is_none(self, handler, ctx):
        ctx.send_message = None
        ctx.args = "https://github.com/sukria/koan/issues/42"
        issue_data = _issue_json(state="closed")
        with patch.object(handler, "_fetch_issue_metadata", return_value=issue_data), \
             patch("app.check_tracker.mark_checked"):
            # Should not raise even though send_message is None
            result = handler._handle_issue(ctx, handler._ISSUE_URL_RE.search(ctx.args))
            assert "closed" in result.lower()


# ---------------------------------------------------------------------------
# _queue_rebase — edge cases
# ---------------------------------------------------------------------------

class TestQueueRebaseEdgeCases:
    def test_rebase_without_project_path(self, handler, ctx):
        """When resolve_project_path returns None, command omits --project-path."""
        ctx.args = "https://github.com/unknown/repo/pull/10"
        pr_data = _pr_json(mergeable="CONFLICTING")
        with patch.object(handler, "_fetch_pr_metadata", return_value=pr_data), \
             patch("app.check_tracker.has_changed", return_value=True), \
             patch("app.check_tracker.mark_checked"), \
             patch("app.utils.insert_pending_mission") as mock_insert, \
             patch("app.utils.resolve_project_path", return_value=None), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            handler._handle_pr(ctx, handler._PR_URL_RE.search(ctx.args))
            entry = mock_insert.call_args[0][1]
            assert "--project-path" not in entry


# ---------------------------------------------------------------------------
# URL edge cases
# ---------------------------------------------------------------------------

class TestUrlEdgeCases:
    def test_pr_url_with_fragment(self, handler, ctx):
        """URL with #issuecomment-xxx should still be detected."""
        ctx.args = "https://github.com/sukria/koan/pull/42#issuecomment-123"
        with patch.object(handler, "_handle_pr", return_value="ok") as mock:
            handler.handle(ctx)
            mock.assert_called_once()

    def test_issue_url_with_query(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/issues/7?foo=bar"
        with patch.object(handler, "_handle_issue", return_value="ok") as mock:
            handler.handle(ctx)
            mock.assert_called_once()

    def test_http_url_accepted(self, handler, ctx):
        """http:// (not https) should still work."""
        ctx.args = "http://github.com/sukria/koan/pull/5"
        with patch.object(handler, "_handle_pr", return_value="ok") as mock:
            handler.handle(ctx)
            mock.assert_called_once()


# ---------------------------------------------------------------------------
# _handle_pr — no send_message callback
# ---------------------------------------------------------------------------

class TestHandlePrNoSendMessage:
    def test_pr_works_without_send_message(self, handler, ctx):
        ctx.send_message = None
        ctx.args = "https://github.com/sukria/koan/pull/42"
        pr_data = _pr_json(state="MERGED")
        with patch.object(handler, "_fetch_pr_metadata", return_value=pr_data), \
             patch("app.check_tracker.mark_checked"):
            result = handler._handle_pr(ctx, handler._PR_URL_RE.search(ctx.args))
            assert "merged" in result.lower()
