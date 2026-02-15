"""Tests for implement_runner.py — the implement execution pipeline."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.github import fetch_issue_with_comments
from skills.core.implement.implement_runner import (
    run_implement,
    _is_plan_content,
    _extract_latest_plan,
    _build_prompt,
    _execute_implementation,
    _PLAN_MARKER_RE,
    main,
)


# ---------------------------------------------------------------------------
# _is_plan_content
# ---------------------------------------------------------------------------

class TestIsPlanContent:
    def test_empty_text(self):
        assert not _is_plan_content("")
        assert not _is_plan_content(None)

    def test_no_markers(self):
        assert not _is_plan_content("Just a random comment about the issue.")

    def test_phase_marker(self):
        assert _is_plan_content("Some text\n#### Phase 1: Setup\nDo things")

    def test_implementation_phases_marker(self):
        assert _is_plan_content("## Implementation Phases\n#### Phase 1")

    def test_summary_marker(self):
        assert _is_plan_content("### Summary\nThis plan does X")

    def test_changes_iteration_marker(self):
        assert _is_plan_content("### Changes in this iteration\n- Updated phase 2")

    def test_case_insensitive(self):
        assert _is_plan_content("## implementation phases\n#### Phase 1")
        assert _is_plan_content("### SUMMARY\nText here")


# ---------------------------------------------------------------------------
# _extract_latest_plan
# ---------------------------------------------------------------------------

class TestExtractLatestPlan:
    def test_plan_in_body_only(self):
        body = "## Summary\nDo the thing\n### Implementation Phases\n#### Phase 1: Start"
        result = _extract_latest_plan(body, [])
        assert result == body

    def test_plan_in_latest_comment(self):
        body = "Original issue description"
        comments = [
            {"body": "Nice idea!", "author": "user1", "date": "2026-01-01"},
            {"body": "### Summary\nOld plan v1\n#### Phase 1: Old", "author": "bot", "date": "2026-01-02"},
            {"body": "### Summary\nNew plan v2\n#### Phase 1: New", "author": "bot", "date": "2026-01-03"},
        ]
        result = _extract_latest_plan(body, comments)
        assert "New plan v2" in result
        assert "Old plan v1" not in result

    def test_ignores_non_plan_comments(self):
        body = "### Summary\nThe original plan"
        comments = [
            {"body": "Looks good!", "author": "reviewer", "date": "2026-01-01"},
            {"body": "Ship it", "author": "reviewer2", "date": "2026-01-02"},
        ]
        result = _extract_latest_plan(body, comments)
        assert "The original plan" in result

    def test_fallback_to_long_body_without_markers(self):
        body = "A" * 200  # Long body without plan markers
        result = _extract_latest_plan(body, [])
        assert result == body

    def test_empty_body_no_comments(self):
        result = _extract_latest_plan("", [])
        assert result == ""

    def test_short_body_without_markers(self):
        """Short bodies without markers are now returned as fallback."""
        result = _extract_latest_plan("Short text", [])
        assert result == "Short text"

    def test_plan_in_middle_comment_not_last(self):
        """The latest plan comment wins, even if non-plan comments follow."""
        body = "Issue body"
        comments = [
            {"body": "### Summary\nPlan v1\n#### Phase 1: Do it", "author": "bot", "date": "2026-01-01"},
            {"body": "Thanks for the plan!", "author": "human", "date": "2026-01-02"},
        ]
        result = _extract_latest_plan(body, comments)
        assert "Plan v1" in result

    def test_empty_comments_list(self):
        body = "### Implementation Phases\n#### Phase 1: Go"
        result = _extract_latest_plan(body, [])
        assert "Phase 1" in result


# ---------------------------------------------------------------------------
# fetch_issue_with_comments (now in github.py)
# ---------------------------------------------------------------------------

class TestFetchIssueWithComments:
    def test_successful_fetch(self):
        issue_data = json.dumps({"title": "My Plan", "body": "The plan body"})
        comments_data = json.dumps([
            {"author": "user1", "date": "2026-01-01", "body": "Nice!"}
        ])
        with patch("app.github.api", side_effect=[issue_data, comments_data]):
            title, body, comments = fetch_issue_with_comments("owner", "repo", "42")
            assert title == "My Plan"
            assert body == "The plan body"
            assert len(comments) == 1
            assert comments[0]["author"] == "user1"

    def test_malformed_issue_json(self):
        with patch("app.github.api", side_effect=["not json", "[]"]):
            title, body, comments = fetch_issue_with_comments("o", "r", "1")
            assert title == ""
            assert body == "not json"
            assert comments == []

    def test_malformed_comments_json(self):
        issue_data = json.dumps({"title": "T", "body": "B"})
        with patch("app.github.api", side_effect=[issue_data, "bad"]):
            title, body, comments = fetch_issue_with_comments("o", "r", "1")
            assert title == "T"
            assert comments == []

    def test_empty_comments(self):
        issue_data = json.dumps({"title": "T", "body": "B"})
        with patch("app.github.api", side_effect=[issue_data, "[]"]):
            _, _, comments = fetch_issue_with_comments("o", "r", "1")
            assert comments == []


# ---------------------------------------------------------------------------
# _build_prompt + _execute_implementation
# ---------------------------------------------------------------------------

class TestBuildPrompt:
    def test_uses_skill_prompt_when_skill_dir_given(self):
        skill_dir = Path("/fake/skill/dir")
        with patch("app.prompts.load_skill_prompt", return_value="prompt") as mock_load:
            result = _build_prompt(
                "http://url", "Title", "Plan", "Context",
                skill_dir=skill_dir,
            )
            mock_load.assert_called_once_with(
                skill_dir, "implement",
                ISSUE_URL="http://url",
                ISSUE_TITLE="Title",
                PLAN="Plan",
                CONTEXT="Context",
            )
            assert result == "prompt"

    def test_uses_global_prompt_when_no_skill_dir(self):
        with patch("app.prompts.load_prompt", return_value="prompt") as mock_load:
            result = _build_prompt(
                "http://url", "Title", "Plan", "Context",
            )
            mock_load.assert_called_once_with(
                "implement",
                ISSUE_URL="http://url",
                ISSUE_TITLE="Title",
                PLAN="Plan",
                CONTEXT="Context",
            )
            assert result == "prompt"


class TestExecuteImplementation:
    def test_passes_correct_run_command_params(self):
        with patch("skills.core.implement.implement_runner._build_prompt", return_value="prompt"), \
             patch("app.cli_provider.run_command", return_value="ok") as mock_run:
            result = _execute_implementation(
                "/project", "url", "t", "p", "c",
                skill_dir=Path("/skill"),
            )
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args
            assert call_kwargs[0][0] == "prompt"
            assert call_kwargs[0][1] == "/project"
            assert call_kwargs[1]["max_turns"] == 50
            assert call_kwargs[1]["timeout"] == 900
            assert result == "ok"

    def test_passes_allowed_tools(self):
        """run_command must receive allowed_tools covering full CLAUDE_TOOLS set."""
        with patch("skills.core.implement.implement_runner._build_prompt", return_value="p"), \
             patch("app.cli_provider.run_command", return_value="ok") as mock_run:
            _execute_implementation("/project", "url", "t", "p", "c")
            call_args = mock_run.call_args
            tools = call_args[1].get("allowed_tools") or call_args[0][2]
            # Implementation needs the full toolset
            for tool in ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]:
                assert tool in tools, f"{tool} missing from allowed_tools"


# ---------------------------------------------------------------------------
# run_implement — top-level
# ---------------------------------------------------------------------------

class TestRunImplement:
    def test_invalid_url(self):
        ok, msg = run_implement("/project", "not-a-url", notify_fn=MagicMock())
        assert not ok
        assert "Invalid" in msg

    def test_no_plan_found(self):
        notify = MagicMock()
        with patch("skills.core.implement.implement_runner.fetch_issue_with_comments",
                    return_value=("Title", "", [])):
            ok, msg = run_implement(
                "/project",
                "https://github.com/o/r/issues/1",
                notify_fn=notify,
            )
            assert not ok
            assert "No plan found" in msg

    def test_successful_implementation(self):
        notify = MagicMock()
        body = "### Summary\nPlan\n#### Phase 1: Do it"
        with patch("skills.core.implement.implement_runner.fetch_issue_with_comments",
                    return_value=("Title", body, [])), \
             patch("skills.core.implement.implement_runner._execute_implementation",
                    return_value="Done"):
            ok, msg = run_implement(
                "/project",
                "https://github.com/o/r/issues/42",
                notify_fn=notify,
            )
            assert ok
            assert "#42" in msg

    def test_with_context(self):
        notify = MagicMock()
        body = "### Summary\nPlan\n#### Phase 1: Do it"
        with patch("skills.core.implement.implement_runner.fetch_issue_with_comments",
                    return_value=("Title", body, [])), \
             patch("skills.core.implement.implement_runner._execute_implementation",
                    return_value="Done") as mock_run:
            ok, msg = run_implement(
                "/project",
                "https://github.com/o/r/issues/42",
                context="Phase 1 to 3",
                notify_fn=notify,
            )
            assert ok
            assert "Phase 1 to 3" in msg
            # Verify context was passed to the runner
            _, kwargs = mock_run.call_args
            assert kwargs.get("context") == "Phase 1 to 3" or \
                   mock_run.call_args[0][3] == "Phase 1 to 3"

    def test_fetch_failure(self):
        notify = MagicMock()
        with patch("skills.core.implement.implement_runner.fetch_issue_with_comments",
                    side_effect=RuntimeError("API error")):
            ok, msg = run_implement(
                "/project",
                "https://github.com/o/r/issues/1",
                notify_fn=notify,
            )
            assert not ok
            assert "Failed to fetch" in msg

    def test_claude_failure(self):
        notify = MagicMock()
        body = "### Summary\nPlan\n#### Phase 1: Do it"
        with patch("skills.core.implement.implement_runner.fetch_issue_with_comments",
                    return_value=("Title", body, [])), \
             patch("skills.core.implement.implement_runner._execute_implementation",
                    side_effect=RuntimeError("Timeout")):
            ok, msg = run_implement(
                "/project",
                "https://github.com/o/r/issues/1",
                notify_fn=notify,
            )
            assert not ok
            assert "Implementation failed" in msg

    def test_empty_claude_output(self):
        notify = MagicMock()
        body = "### Summary\nPlan\n#### Phase 1: Do it"
        with patch("skills.core.implement.implement_runner.fetch_issue_with_comments",
                    return_value=("Title", body, [])), \
             patch("skills.core.implement.implement_runner._execute_implementation",
                    return_value=""):
            ok, msg = run_implement(
                "/project",
                "https://github.com/o/r/issues/1",
                notify_fn=notify,
            )
            assert not ok
            assert "empty output" in msg

    def test_default_context_when_none(self):
        """When no context is given, default to 'Implement the full plan.'"""
        notify = MagicMock()
        body = "### Summary\nPlan\n#### Phase 1: Do it"
        with patch("skills.core.implement.implement_runner.fetch_issue_with_comments",
                    return_value=("Title", body, [])), \
             patch("skills.core.implement.implement_runner._execute_implementation",
                    return_value="Done") as mock_run:
            run_implement(
                "/project",
                "https://github.com/o/r/issues/42",
                notify_fn=notify,
            )
            args = mock_run.call_args
            # context arg should be the default
            assert "Implement the full plan." in str(args)

    def test_notify_messages(self):
        """Verify notification messages are sent correctly."""
        notify = MagicMock()
        body = "### Summary\nPlan\n#### Phase 1: Do it"
        with patch("skills.core.implement.implement_runner.fetch_issue_with_comments",
                    return_value=("Title", body, [])), \
             patch("skills.core.implement.implement_runner._execute_implementation",
                    return_value="Done"):
            run_implement(
                "/project",
                "https://github.com/o/r/issues/42",
                context="Phase 1",
                notify_fn=notify,
            )
            # First call: starting notification
            first_msg = notify.call_args_list[0][0][0]
            assert "#42" in first_msg
            assert "Phase 1" in first_msg
            # Second call: completion notification
            second_msg = notify.call_args_list[1][0][0]
            assert "#42" in second_msg


# ---------------------------------------------------------------------------
# main — CLI entry point
# ---------------------------------------------------------------------------

class TestMain:
    def test_success_exit_code(self):
        with patch("skills.core.implement.implement_runner.run_implement",
                    return_value=(True, "ok")):
            code = main([
                "--project-path", "/project",
                "--issue-url", "https://github.com/o/r/issues/1",
            ])
            assert code == 0

    def test_failure_exit_code(self):
        with patch("skills.core.implement.implement_runner.run_implement",
                    return_value=(False, "failed")):
            code = main([
                "--project-path", "/project",
                "--issue-url", "https://github.com/o/r/issues/1",
            ])
            assert code == 1

    def test_context_arg_passed(self):
        with patch("skills.core.implement.implement_runner.run_implement",
                    return_value=(True, "ok")) as mock:
            main([
                "--project-path", "/project",
                "--issue-url", "https://github.com/o/r/issues/1",
                "--context", "Phase 1 to 3",
            ])
            _, kwargs = mock.call_args
            assert kwargs["context"] == "Phase 1 to 3"

    def test_context_defaults_to_none(self):
        with patch("skills.core.implement.implement_runner.run_implement",
                    return_value=(True, "ok")) as mock:
            main([
                "--project-path", "/project",
                "--issue-url", "https://github.com/o/r/issues/1",
            ])
            _, kwargs = mock.call_args
            assert kwargs["context"] is None
