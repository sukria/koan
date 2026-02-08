"""Tests for plan_runner.py — the plan execution pipeline."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

from app.plan_runner import (
    run_plan,
    _generate_plan,
    _get_repo_info,
    _fetch_issue_context,
    _format_comments,
    _extract_title,
    _extract_idea_from_issue,
    _run_new_plan,
    _run_issue_plan,
    main,
)


# ---------------------------------------------------------------------------
# run_plan — top-level routing
# ---------------------------------------------------------------------------

class TestRunPlan:
    def test_no_idea_no_url_returns_error(self):
        ok, msg = run_plan("/project")
        assert not ok
        assert "No idea" in msg

    def test_routes_to_new_plan(self):
        with patch("app.plan_runner._run_new_plan", return_value=(True, "done")) as mock:
            ok, msg = run_plan("/project", idea="Add feature", notify_fn=MagicMock())
            assert ok
            mock.assert_called_once()

    def test_routes_to_issue_plan(self):
        url = "https://github.com/o/r/issues/1"
        with patch("app.plan_runner._run_issue_plan", return_value=(True, "done")) as mock:
            ok, msg = run_plan("/project", issue_url=url, notify_fn=MagicMock())
            assert ok
            mock.assert_called_once()

    def test_defaults_notify_fn(self):
        with patch("app.plan_runner._run_new_plan", return_value=(True, "ok")) as mock, \
             patch("app.notify.send_telegram"):
            run_plan("/project", idea="test")
            # Should not crash — notify_fn defaults to send_telegram


# ---------------------------------------------------------------------------
# _run_new_plan
# ---------------------------------------------------------------------------

class TestRunNewPlan:
    def test_successful_plan_with_issue(self):
        notify = MagicMock()
        with patch("app.plan_runner._generate_plan", return_value="## Plan\nStep 1"), \
             patch("app.plan_runner._get_repo_info", return_value=("sukria", "koan")), \
             patch("app.github.subprocess.run", return_value=MagicMock(
                 returncode=0, stdout="https://github.com/sukria/koan/issues/99\n"
             )):
            ok, msg = _run_new_plan("/project", "Add feature", notify, None)
            assert ok
            assert "issues/99" in msg
            notify.assert_called()

    def test_no_github_repo_sends_inline(self):
        notify = MagicMock()
        with patch("app.plan_runner._generate_plan", return_value="## Plan\nStep 1"), \
             patch("app.plan_runner._get_repo_info", return_value=(None, None)):
            ok, msg = _run_new_plan("/project", "Add feature", notify, None)
            assert ok
            assert "inline" in msg
            # Plan was sent via notify_fn
            calls = [str(c) for c in notify.call_args_list]
            assert any("Plan" in c for c in calls)

    def test_generate_plan_failure(self):
        notify = MagicMock()
        with patch("app.plan_runner._generate_plan", side_effect=RuntimeError("timeout")):
            ok, msg = _run_new_plan("/project", "idea", notify, None)
            assert not ok
            assert "failed" in msg.lower()

    def test_empty_plan(self):
        notify = MagicMock()
        with patch("app.plan_runner._generate_plan", return_value=""):
            ok, msg = _run_new_plan("/project", "idea", notify, None)
            assert not ok
            assert "empty" in msg.lower()

    def test_issue_creation_failure_sends_inline(self):
        notify = MagicMock()
        with patch("app.plan_runner._generate_plan", return_value="## Plan"), \
             patch("app.plan_runner._get_repo_info", return_value=("o", "r")), \
             patch("app.github.subprocess.run", return_value=MagicMock(
                 returncode=1, stderr="no perms"
             )):
            ok, msg = _run_new_plan("/project", "idea", notify, None)
            assert ok
            assert "failed" in msg.lower()

    def test_sends_planning_notification(self):
        notify = MagicMock()
        with patch("app.plan_runner._generate_plan", return_value="## Plan"), \
             patch("app.plan_runner._get_repo_info", return_value=(None, None)):
            _run_new_plan("/project", "Add dark mode to dashboard", notify, None)
            first_msg = notify.call_args_list[0][0][0]
            assert "Planning" in first_msg
            assert "dark mode" in first_msg

    def test_long_idea_truncated_in_notification(self):
        notify = MagicMock()
        long_idea = "A" * 200
        with patch("app.plan_runner._generate_plan", return_value="## Plan"), \
             patch("app.plan_runner._get_repo_info", return_value=(None, None)):
            _run_new_plan("/project", long_idea, notify, None)
            first_msg = notify.call_args_list[0][0][0]
            assert "..." in first_msg


# ---------------------------------------------------------------------------
# _run_issue_plan
# ---------------------------------------------------------------------------

class TestRunIssuePlan:
    def test_successful_iteration(self):
        notify = MagicMock()
        url = "https://github.com/sukria/koan/issues/64"
        with patch("app.plan_runner._fetch_issue_context",
                    return_value=("Issue Title", "body", "comments")), \
             patch("app.plan_runner._generate_plan", return_value="## Updated Plan"), \
             patch("app.plan_runner._comment_on_issue") as mock_comment:
            ok, msg = _run_issue_plan("/project", url, notify, None)
            assert ok
            assert "#64" in msg
            mock_comment.assert_called_once()

    def test_invalid_url(self):
        notify = MagicMock()
        ok, msg = _run_issue_plan("/project", "not-a-url", notify, None)
        assert not ok
        assert "Invalid" in msg

    def test_fetch_failure(self):
        notify = MagicMock()
        url = "https://github.com/o/r/issues/1"
        with patch("app.plan_runner._fetch_issue_context",
                    side_effect=RuntimeError("not found")):
            ok, msg = _run_issue_plan("/project", url, notify, None)
            assert not ok
            assert "Failed to fetch" in msg

    def test_plan_generation_failure(self):
        notify = MagicMock()
        url = "https://github.com/o/r/issues/1"
        with patch("app.plan_runner._fetch_issue_context",
                    return_value=("Title", "body", "")), \
             patch("app.plan_runner._generate_plan",
                    side_effect=RuntimeError("error")):
            ok, msg = _run_issue_plan("/project", url, notify, None)
            assert not ok
            assert "failed" in msg.lower()

    def test_empty_plan(self):
        notify = MagicMock()
        url = "https://github.com/o/r/issues/1"
        with patch("app.plan_runner._fetch_issue_context",
                    return_value=("Title", "body", "")), \
             patch("app.plan_runner._generate_plan", return_value=""):
            ok, msg = _run_issue_plan("/project", url, notify, None)
            assert not ok
            assert "empty" in msg.lower()

    def test_comment_failure_sends_inline(self):
        notify = MagicMock()
        url = "https://github.com/o/r/issues/1"
        with patch("app.plan_runner._fetch_issue_context",
                    return_value=("Title", "body", "")), \
             patch("app.plan_runner._generate_plan", return_value="## Plan"), \
             patch("app.plan_runner._comment_on_issue",
                    side_effect=RuntimeError("no perms")):
            ok, msg = _run_issue_plan("/project", url, notify, None)
            assert ok
            assert "failed" in msg.lower()

    def test_sends_reading_notification(self):
        notify = MagicMock()
        url = "https://github.com/sukria/koan/issues/64"
        with patch("app.plan_runner._fetch_issue_context",
                    return_value=("Title", "body", "")), \
             patch("app.plan_runner._generate_plan", return_value="## Plan"), \
             patch("app.plan_runner._comment_on_issue"):
            _run_issue_plan("/project", url, notify, None)
            first_msg = notify.call_args_list[0][0][0]
            assert "#64" in first_msg

    def test_success_includes_title(self):
        notify = MagicMock()
        url = "https://github.com/sukria/koan/issues/64"
        with patch("app.plan_runner._fetch_issue_context",
                    return_value=("Add dark mode", "body", "")), \
             patch("app.plan_runner._generate_plan", return_value="## Plan"), \
             patch("app.plan_runner._comment_on_issue"):
            ok, msg = _run_issue_plan("/project", url, notify, None)
            assert ok
            assert "Add dark mode" in msg


# ---------------------------------------------------------------------------
# _generate_plan
# ---------------------------------------------------------------------------

class TestGeneratePlan:
    @patch("subprocess.run")
    def test_returns_claude_output(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="## Plan\n\nStep 1", stderr=""
        )
        with patch("app.prompts.load_skill_prompt", return_value="prompt"), \
             patch("app.config.get_model_config",
                    return_value={"chat": "sonnet", "fallback": "haiku"}), \
             patch("app.cli_provider.build_full_command",
                    return_value=["claude", "-p", "test"]):
            skill_dir = Path("/fake/skills/core/plan")
            result = _generate_plan("/project", "Add feature", skill_dir=skill_dir)
            assert "Step 1" in result

    @patch("subprocess.run")
    def test_includes_context(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="plan", stderr="")
        with patch("app.prompts.load_skill_prompt") as mock_load, \
             patch("app.config.get_model_config",
                    return_value={"chat": "", "fallback": ""}), \
             patch("app.cli_provider.build_full_command",
                    return_value=["claude", "-p", "test"]):
            skill_dir = Path("/fake")
            _generate_plan("/project", "idea", context="prev", skill_dir=skill_dir)
            _, kwargs = mock_load.call_args
            assert kwargs["CONTEXT"] == "prev"

    @patch("subprocess.run")
    def test_raises_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr="rate limited")
        with patch("app.prompts.load_skill_prompt", return_value="prompt"), \
             patch("app.config.get_model_config",
                    return_value={"chat": "", "fallback": ""}), \
             patch("app.cli_provider.build_full_command",
                    return_value=["claude"]):
            with pytest.raises(RuntimeError, match="plan generation failed"):
                _generate_plan("/project", "idea", skill_dir=Path("/fake"))

    @patch("subprocess.run")
    def test_uses_read_only_tools(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="plan", stderr="")
        with patch("app.prompts.load_skill_prompt", return_value="prompt"), \
             patch("app.config.get_model_config",
                    return_value={"chat": "", "fallback": ""}):
            _generate_plan("/project", "idea", skill_dir=Path("/fake"))
            cmd = mock_run.call_args[0][0]
            tools_idx = cmd.index("--allowedTools")
            tools = cmd[tools_idx + 1]
            assert "Read" in tools
            assert "Write" not in tools
            assert "Bash" not in tools

    @patch("subprocess.run")
    def test_no_skill_dir_uses_load_prompt(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="plan", stderr="")
        with patch("app.prompts.load_prompt", return_value="prompt") as mock_load, \
             patch("app.config.get_model_config",
                    return_value={"chat": "", "fallback": ""}), \
             patch("app.cli_provider.build_full_command",
                    return_value=["claude"]):
            _generate_plan("/project", "idea")
            mock_load.assert_called_once()


# ---------------------------------------------------------------------------
# _get_repo_info
# ---------------------------------------------------------------------------

class TestGetRepoInfo:
    def test_successful_gh_call(self):
        gh_output = json.dumps({"owner": {"login": "sukria"}, "name": "koan"})
        with patch("app.github.subprocess.run",
                    return_value=MagicMock(returncode=0, stdout=gh_output)):
            owner, repo = _get_repo_info("/path")
            assert owner == "sukria"
            assert repo == "koan"

    def test_gh_failure_returns_none(self):
        with patch("app.github.subprocess.run",
                    return_value=MagicMock(returncode=1, stderr="err")):
            owner, repo = _get_repo_info("/path")
            assert owner is None
            assert repo is None

    def test_timeout_returns_none(self):
        with patch("app.github.subprocess.run",
                    side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=15)):
            owner, repo = _get_repo_info("/path")
            assert owner is None
            assert repo is None


# ---------------------------------------------------------------------------
# _fetch_issue_context
# ---------------------------------------------------------------------------

class TestFetchIssueContext:
    @patch("app.github.subprocess.run")
    def test_returns_title_body_and_comments(self, mock_run):
        comments_data = json.dumps([
            {"author": "alice", "date": "2026-02-01T10:00:00Z", "body": "Looks good"},
        ])
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps(
                {"title": "My Issue", "body": "Body"}
            )),
            MagicMock(returncode=0, stdout=comments_data),
        ]
        title, body, comments = _fetch_issue_context("sukria", "koan", "64")
        assert title == "My Issue"
        assert body == "Body"
        assert "alice" in comments

    @patch("app.github.subprocess.run")
    def test_handles_non_json(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="plain text"),
            MagicMock(returncode=0, stdout=""),
        ]
        title, body, _ = _fetch_issue_context("o", "r", "1")
        assert title == ""
        assert body == "plain text"


# ---------------------------------------------------------------------------
# _format_comments
# ---------------------------------------------------------------------------

class TestFormatComments:
    def test_formats_with_author_and_date(self):
        data = json.dumps([
            {"author": "alice", "date": "2026-02-01T10:00:00Z", "body": "Good"},
        ])
        result = _format_comments(data)
        assert "alice" in result
        assert "2026-02-01" in result

    def test_empty_list(self):
        assert _format_comments("[]") == ""

    def test_invalid_json(self):
        assert _format_comments("not json") == "not json"

    def test_empty_string(self):
        assert _format_comments("") == ""

    def test_skips_empty_body(self):
        data = json.dumps([
            {"author": "a", "date": "2026-01-01T00:00:00Z", "body": ""},
            {"author": "b", "date": "2026-01-02T00:00:00Z", "body": "useful"},
        ])
        result = _format_comments(data)
        assert "useful" in result
        assert result.count("**") == 2


# ---------------------------------------------------------------------------
# _extract_title
# ---------------------------------------------------------------------------

class TestExtractTitle:
    def test_from_heading(self):
        assert _extract_title("## Dark mode\n\nDetails") == "Dark mode"

    def test_first_non_empty_line(self):
        assert _extract_title("\n\nThis is the plan") == "This is the plan"

    def test_truncates(self):
        assert len(_extract_title("# " + "A" * 200)) <= 120

    def test_fallback(self):
        assert _extract_title("") == "Implementation Plan"

    def test_strips_prefix(self):
        assert _extract_title("### Summary") == "Summary"


# ---------------------------------------------------------------------------
# _extract_idea_from_issue
# ---------------------------------------------------------------------------

class TestExtractIdeaFromIssue:
    def test_first_paragraph(self):
        assert "Add dark mode" in _extract_idea_from_issue(
            "## Plan: Add dark mode\n\nDetails"
        )

    def test_skips_metadata(self):
        assert "real idea" in _extract_idea_from_issue(
            "---\n*Generated by Kōan*\n\nThe real idea"
        )

    def test_empty_body(self):
        assert "Review" in _extract_idea_from_issue("")
        assert "Review" in _extract_idea_from_issue(None)

    def test_strips_plan_prefix(self):
        idea = _extract_idea_from_issue("Plan: Implement X\n\nDetails")
        assert idea.startswith("Implement X")

    def test_truncates(self):
        assert len(_extract_idea_from_issue("A" * 600)) <= 500


# ---------------------------------------------------------------------------
# CLI entry point — main()
# ---------------------------------------------------------------------------

class TestCLI:
    def test_idea_mode(self):
        with patch("app.plan_runner.run_plan",
                    return_value=(True, "Plan created")) as mock:
            code = main(["--project-path", "/proj", "--idea", "Add auth"])
            assert code == 0
            mock.assert_called_once()
            assert mock.call_args.kwargs["idea"] == "Add auth"
            assert mock.call_args.kwargs["project_path"] == "/proj"

    def test_issue_url_mode(self):
        url = "https://github.com/o/r/issues/1"
        with patch("app.plan_runner.run_plan",
                    return_value=(True, "Posted")) as mock:
            code = main(["--project-path", "/proj", "--issue-url", url])
            assert code == 0
            assert mock.call_args.kwargs["issue_url"] == url

    def test_failure_returns_1(self):
        with patch("app.plan_runner.run_plan",
                    return_value=(False, "error")):
            code = main(["--project-path", "/proj", "--idea", "bad"])
            assert code == 1

    def test_missing_args_exits(self):
        with pytest.raises(SystemExit):
            main([])

    def test_both_idea_and_url_exits(self):
        with pytest.raises(SystemExit):
            main(["--project-path", "/p", "--idea", "x",
                   "--issue-url", "https://github.com/o/r/issues/1"])

    def test_skill_dir_resolved(self):
        with patch("app.plan_runner.run_plan",
                    return_value=(True, "ok")) as mock:
            main(["--project-path", "/proj", "--idea", "test"])
            skill_dir = mock.call_args.kwargs["skill_dir"]
            assert skill_dir.name == "plan"
            assert "skills/core/plan" in str(skill_dir)
