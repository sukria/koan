"""Tests for the /plan core skill — deep planning and GitHub issue creation."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.skills import SkillContext


# ---------------------------------------------------------------------------
# Import handler functions
# ---------------------------------------------------------------------------

# We import lazily inside tests to avoid module-level side effects,
# but the handler module itself is safe to import.
import importlib.util

HANDLER_PATH = Path(__file__).parent.parent / "skills" / "core" / "plan" / "handler.py"


def _load_handler():
    """Load the plan handler module."""
    spec = importlib.util.spec_from_file_location("plan_handler", str(HANDLER_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def handler():
    return _load_handler()


@pytest.fixture
def ctx(tmp_path):
    """Create a basic SkillContext for tests."""
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    return SkillContext(
        koan_root=tmp_path,
        instance_dir=instance_dir,
        command_name="plan",
        args="",
        send_message=MagicMock(),
    )


# ---------------------------------------------------------------------------
# handle() — usage / routing
# ---------------------------------------------------------------------------

class TestHandleRouting:
    def test_no_args_returns_usage(self, handler, ctx):
        result = handler.handle(ctx)
        assert "Usage:" in result
        assert "/plan <idea>" in result

    def test_routes_to_new_plan(self, handler, ctx):
        ctx.args = "Add dark mode"
        with patch.object(handler, "_handle_new_plan", return_value="ok") as mock:
            handler.handle(ctx)
            mock.assert_called_once()
            _, project, idea = mock.call_args[0]
            assert project is None
            assert idea == "Add dark mode"

    def test_routes_github_issue_url(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/issues/64"
        with patch.object(handler, "_handle_existing_issue", return_value="ok") as mock:
            handler.handle(ctx)
            mock.assert_called_once()

    def test_routes_project_prefixed_idea(self, handler, ctx):
        ctx.args = "koan Add dark mode"
        with patch.object(handler, "_handle_new_plan", return_value="ok") as mock, \
             patch("app.utils.get_known_projects", return_value=[("koan", "/path")]):
            handler.handle(ctx)
            mock.assert_called_once()
            _, project, idea = mock.call_args[0]
            assert project == "koan"
            assert idea == "Add dark mode"

    def test_routes_project_tag_idea(self, handler, ctx):
        ctx.args = "[project:koan] Add dark mode"
        with patch.object(handler, "_handle_new_plan", return_value="ok") as mock:
            handler.handle(ctx)
            mock.assert_called_once()
            _, project, idea = mock.call_args[0]
            assert project == "koan"
            assert idea == "Add dark mode"

    def test_github_url_with_fragment(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/issues/64#issuecomment-123"
        with patch.object(handler, "_handle_existing_issue", return_value="ok") as mock:
            handler.handle(ctx)
            mock.assert_called_once()


# ---------------------------------------------------------------------------
# _parse_project_arg
# ---------------------------------------------------------------------------

class TestParseProjectArg:
    def test_no_project_prefix(self, handler):
        with patch("app.utils.get_known_projects", return_value=[]):
            project, idea = handler._parse_project_arg("Add dark mode")
            assert project is None
            assert idea == "Add dark mode"

    def test_project_tag_format(self, handler):
        project, idea = handler._parse_project_arg("[project:koan] Fix the bug")
        assert project == "koan"
        assert idea == "Fix the bug"

    def test_project_name_prefix(self, handler):
        with patch("app.utils.get_known_projects", return_value=[("koan", "/path"), ("webapp", "/other")]):
            project, idea = handler._parse_project_arg("koan Fix the login")
            assert project == "koan"
            assert idea == "Fix the login"

    def test_unknown_project_name_treated_as_idea(self, handler):
        with patch("app.utils.get_known_projects", return_value=[("koan", "/path")]):
            project, idea = handler._parse_project_arg("webapp Fix the login")
            assert project is None
            assert idea == "webapp Fix the login"

    def test_single_word_no_project(self, handler):
        with patch("app.utils.get_known_projects", return_value=[("koan", "/path")]):
            project, idea = handler._parse_project_arg("refactor")
            assert project is None
            assert idea == "refactor"

    def test_case_insensitive_project_match(self, handler):
        with patch("app.utils.get_known_projects", return_value=[("Koan", "/path")]):
            project, idea = handler._parse_project_arg("koan Fix bug")
            assert project == "Koan"
            assert idea == "Fix bug"


# ---------------------------------------------------------------------------
# _resolve_project_path
# ---------------------------------------------------------------------------

class TestResolveProjectPath:
    def test_named_project(self, handler):
        with patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan"), ("web", "/home/web")]):
            assert handler._resolve_project_path("koan") == "/home/koan"

    def test_named_project_case_insensitive(self, handler):
        with patch("app.utils.get_known_projects", return_value=[("Koan", "/home/koan")]):
            assert handler._resolve_project_path("koan") == "/home/koan"

    def test_unknown_project_returns_none(self, handler):
        with patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            assert handler._resolve_project_path("unknown") is None

    def test_no_project_defaults_to_first(self, handler):
        with patch("app.utils.get_known_projects", return_value=[("koan", "/first"), ("web", "/second")]):
            assert handler._resolve_project_path(None) == "/first"

    def test_no_projects_falls_back_to_env(self, handler):
        with patch("app.utils.get_known_projects", return_value=[]), \
             patch.dict("os.environ", {"KOAN_PROJECT_PATH": "/from/env"}):
            assert handler._resolve_project_path(None) == "/from/env"

    def test_directory_basename_match(self, handler):
        with patch("app.utils.get_known_projects", return_value=[("myproject", "/home/koan")]):
            assert handler._resolve_project_path("koan") == "/home/koan"


# ---------------------------------------------------------------------------
# _get_repo_info
# ---------------------------------------------------------------------------

class TestGetRepoInfo:
    def test_successful_gh_call(self, handler):
        gh_output = json.dumps({"owner": {"login": "sukria"}, "name": "koan"})
        with patch("app.github.subprocess.run", return_value=MagicMock(returncode=0, stdout=gh_output)):
            owner, repo = handler._get_repo_info("/path/to/project")
            assert owner == "sukria"
            assert repo == "koan"

    def test_gh_failure_returns_none(self, handler):
        with patch("app.github.subprocess.run", return_value=MagicMock(returncode=1, stderr="err")):
            owner, repo = handler._get_repo_info("/path")
            assert owner is None
            assert repo is None

    def test_timeout_returns_none(self, handler):
        with patch("app.github.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=15)):
            owner, repo = handler._get_repo_info("/path")
            assert owner is None
            assert repo is None


# ---------------------------------------------------------------------------
# _extract_title
# ---------------------------------------------------------------------------

class TestExtractTitle:
    def test_extracts_from_markdown_heading(self, handler):
        plan = "## Add dark mode support\n\nSome details..."
        assert handler._extract_title(plan) == "Add dark mode support"

    def test_extracts_first_non_empty_line(self, handler):
        plan = "\n\nThis is the plan\n\nMore stuff"
        assert handler._extract_title(plan) == "This is the plan"

    def test_truncates_long_titles(self, handler):
        plan = "# " + "A" * 200
        title = handler._extract_title(plan)
        assert len(title) <= 120

    def test_fallback_for_empty_plan(self, handler):
        assert handler._extract_title("") == "Implementation Plan"
        assert handler._extract_title("\n\n\n") == "Implementation Plan"

    def test_strips_markdown_prefix(self, handler):
        assert handler._extract_title("### Summary") == "Summary"
        assert handler._extract_title("# Main Title") == "Main Title"


# ---------------------------------------------------------------------------
# _extract_idea_from_issue
# ---------------------------------------------------------------------------

class TestExtractIdeaFromIssue:
    def test_extracts_first_paragraph(self, handler):
        body = "## Plan: Add dark mode\n\nThe dashboard needs dark mode support."
        idea = handler._extract_idea_from_issue(body)
        assert "Add dark mode" in idea

    def test_skips_metadata_lines(self, handler):
        body = "---\n*Generated by Koan /plan*\n\nThe real idea is here."
        idea = handler._extract_idea_from_issue(body)
        assert "real idea" in idea

    def test_empty_body_returns_default(self, handler):
        assert "Review" in handler._extract_idea_from_issue("")
        assert "Review" in handler._extract_idea_from_issue(None)

    def test_strips_plan_prefix(self, handler):
        body = "Plan: Implement feature X\n\nDetails..."
        idea = handler._extract_idea_from_issue(body)
        assert idea.startswith("Implement feature X")

    def test_truncates_long_ideas(self, handler):
        body = "A" * 600
        idea = handler._extract_idea_from_issue(body)
        assert len(idea) <= 500

    def test_short_valid_idea(self, handler):
        body = "Add auth\n\nMore details here"
        idea = handler._extract_idea_from_issue(body)
        assert idea == "Add auth"


# ---------------------------------------------------------------------------
# _format_comments
# ---------------------------------------------------------------------------

class TestFormatComments:
    def test_formats_with_author_and_date(self, handler):
        data = json.dumps([
            {"author": "alice", "date": "2026-02-01T10:00:00Z", "body": "Looks good"},
        ])
        result = handler._format_comments(data)
        assert "alice" in result
        assert "2026-02-01" in result
        assert "Looks good" in result

    def test_multiple_comments_separated(self, handler):
        data = json.dumps([
            {"author": "a", "date": "2026-01-01T00:00:00Z", "body": "first"},
            {"author": "b", "date": "2026-01-02T00:00:00Z", "body": "second"},
        ])
        result = handler._format_comments(data)
        assert "first" in result
        assert "second" in result
        assert "---" in result  # separator between comments

    def test_empty_list(self, handler):
        assert handler._format_comments("[]") == ""

    def test_invalid_json_returns_raw(self, handler):
        assert handler._format_comments("not json") == "not json"

    def test_empty_string(self, handler):
        assert handler._format_comments("") == ""

    def test_skips_empty_body_comments(self, handler):
        data = json.dumps([
            {"author": "a", "date": "2026-01-01T00:00:00Z", "body": ""},
            {"author": "b", "date": "2026-01-02T00:00:00Z", "body": "useful"},
        ])
        result = handler._format_comments(data)
        assert "useful" in result
        # Empty body comment should not produce author header
        assert result.count("**") == 2  # only one **author** pair


# ---------------------------------------------------------------------------
# _handle_new_plan — integration-style tests
# ---------------------------------------------------------------------------

class TestHandleNewPlan:
    def test_unknown_project_returns_error(self, handler, ctx):
        with patch("app.utils.get_known_projects", return_value=[("koan", "/path")]):
            result = handler._handle_new_plan(ctx, "unknown", "some idea")
            assert "not found" in result

    def test_successful_plan_with_issue(self, handler, ctx):
        with patch.object(handler, "_resolve_project_path", return_value="/project"), \
             patch.object(handler, "_get_repo_info", return_value=("sukria", "koan")), \
             patch.object(handler, "_generate_plan", return_value="## Plan\n\nStep 1: Do X"), \
             patch("app.github.subprocess.run", return_value=MagicMock(returncode=0, stdout="https://github.com/sukria/koan/issues/99\n")):
            result = handler._handle_new_plan(ctx, "koan", "Add dark mode")
            assert result is None  # sent via send_message
            ctx.send_message.assert_called()
            # Check the last call contains the issue URL
            last_call = ctx.send_message.call_args_list[-1]
            assert "issues/99" in last_call[0][0]

    def test_no_github_repo_sends_inline(self, handler, ctx):
        with patch.object(handler, "_resolve_project_path", return_value="/project"), \
             patch.object(handler, "_get_repo_info", return_value=(None, None)), \
             patch.object(handler, "_generate_plan", return_value="## Plan\nStep 1"):
            result = handler._handle_new_plan(ctx, None, "Add feature")
            assert result is None
            ctx.send_message.assert_called()
            last_call = ctx.send_message.call_args_list[-1]
            assert "Plan" in last_call[0][0]

    def test_generate_plan_failure(self, handler, ctx):
        with patch.object(handler, "_resolve_project_path", return_value="/project"), \
             patch.object(handler, "_generate_plan", side_effect=RuntimeError("timeout")):
            result = handler._handle_new_plan(ctx, None, "idea")
            assert "failed" in result.lower()

    def test_empty_plan_from_claude(self, handler, ctx):
        with patch.object(handler, "_resolve_project_path", return_value="/project"), \
             patch.object(handler, "_generate_plan", return_value=""):
            result = handler._handle_new_plan(ctx, None, "idea")
            assert "empty" in result.lower()

    def test_issue_creation_failure_sends_inline(self, handler, ctx):
        with patch.object(handler, "_resolve_project_path", return_value="/project"), \
             patch.object(handler, "_get_repo_info", return_value=("owner", "repo")), \
             patch.object(handler, "_generate_plan", return_value="## Plan"), \
             patch("app.github.subprocess.run", return_value=MagicMock(returncode=1, stderr="no permissions")):
            result = handler._handle_new_plan(ctx, None, "idea")
            assert result is None
            # Should have sent the plan inline
            last_call = ctx.send_message.call_args_list[-1]
            assert "Plan" in last_call[0][0]

    def test_sends_planning_notification(self, handler, ctx):
        with patch.object(handler, "_resolve_project_path", return_value="/project"), \
             patch.object(handler, "_generate_plan", return_value="## Plan"), \
             patch.object(handler, "_get_repo_info", return_value=(None, None)):
            handler._handle_new_plan(ctx, None, "Add dark mode to dashboard")
            first_call = ctx.send_message.call_args_list[0]
            msg = first_call[0][0]
            assert "Planning" in msg
            assert "dark mode" in msg


# ---------------------------------------------------------------------------
# _handle_existing_issue — integration-style tests
# ---------------------------------------------------------------------------

class TestHandleExistingIssue:
    def _make_match(self, handler, url="https://github.com/sukria/koan/issues/64"):
        return handler._ISSUE_URL_RE.search(url)

    def test_successful_iteration(self, handler, ctx):
        match = self._make_match(handler)
        with patch.object(handler, "_resolve_project_path", return_value="/project"), \
             patch.object(handler, "_fetch_issue_context", return_value=("Issue Title", "Original plan", "Comment 1\nComment 2")), \
             patch.object(handler, "_generate_plan", return_value="## Updated Plan"), \
             patch.object(handler, "_comment_on_issue") as mock_comment:
            result = handler._handle_existing_issue(ctx, match)
            assert result is None
            mock_comment.assert_called_once()
            # Check comment was posted with updated plan
            body_arg = mock_comment.call_args[0][3]
            assert "Updated Plan" in body_arg

    def test_fetch_failure(self, handler, ctx):
        match = self._make_match(handler)
        with patch.object(handler, "_resolve_project_path", return_value="/project"), \
             patch.object(handler, "_fetch_issue_context", side_effect=RuntimeError("not found")):
            result = handler._handle_existing_issue(ctx, match)
            assert "Failed to fetch" in result

    def test_plan_generation_failure(self, handler, ctx):
        match = self._make_match(handler)
        with patch.object(handler, "_resolve_project_path", return_value="/project"), \
             patch.object(handler, "_fetch_issue_context", return_value=("Title", "body", "")), \
             patch.object(handler, "_generate_plan", side_effect=RuntimeError("claude error")):
            result = handler._handle_existing_issue(ctx, match)
            assert "failed" in result.lower()

    def test_comment_failure_sends_inline(self, handler, ctx):
        match = self._make_match(handler)
        with patch.object(handler, "_resolve_project_path", return_value="/project"), \
             patch.object(handler, "_fetch_issue_context", return_value=("Title", "body", "")), \
             patch.object(handler, "_generate_plan", return_value="## Updated Plan"), \
             patch.object(handler, "_comment_on_issue", side_effect=RuntimeError("no perms")):
            result = handler._handle_existing_issue(ctx, match)
            assert result is None
            last_call = ctx.send_message.call_args_list[-1]
            assert "Plan" in last_call[0][0]

    def test_sends_reading_notification(self, handler, ctx):
        match = self._make_match(handler)
        with patch.object(handler, "_resolve_project_path", return_value="/project"), \
             patch.object(handler, "_fetch_issue_context", return_value=("Title", "body", "")), \
             patch.object(handler, "_generate_plan", return_value="## Plan"), \
             patch.object(handler, "_comment_on_issue"):
            handler._handle_existing_issue(ctx, match)
            first_call = ctx.send_message.call_args_list[0]
            assert "issue #64" in first_call[0][0].lower()

    def test_success_notification_includes_title(self, handler, ctx):
        match = self._make_match(handler)
        with patch.object(handler, "_resolve_project_path", return_value="/project"), \
             patch.object(handler, "_fetch_issue_context", return_value=("Add dark mode", "body", "")), \
             patch.object(handler, "_generate_plan", return_value="## Plan"), \
             patch.object(handler, "_comment_on_issue"):
            handler._handle_existing_issue(ctx, match)
            last_call = ctx.send_message.call_args_list[-1]
            msg = last_call[0][0]
            assert "Add dark mode" in msg
            assert "#64" in msg


# ---------------------------------------------------------------------------
# _generate_plan — subprocess mocking
# ---------------------------------------------------------------------------

class TestGeneratePlan:
    @patch("subprocess.run")
    def test_returns_claude_output(self, mock_run, handler):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="## Plan\n\nStep 1: Do the thing",
            stderr="",
        )
        with patch("app.prompts.load_skill_prompt", return_value="Plan this: idea"), \
             patch("app.utils.get_model_config", return_value={"chat": "sonnet", "fallback": "haiku"}), \
             patch("app.cli_provider.build_full_command", return_value=["claude", "-p", "test"]):
            result = handler._generate_plan("/project", "Add feature")
            assert "Step 1" in result

    @patch("subprocess.run")
    def test_includes_context_in_prompt(self, mock_run, handler):
        mock_run.return_value = MagicMock(returncode=0, stdout="plan", stderr="")
        with patch("app.prompts.load_skill_prompt", return_value="prompt with previous discussion") as mock_load, \
             patch("app.utils.get_model_config", return_value={"chat": "", "fallback": ""}), \
             patch("app.cli_provider.build_full_command", return_value=["claude", "-p", "test"]):
            handler._generate_plan("/project", "idea", context="previous discussion")
            mock_load.assert_called_once()
            args, kwargs = mock_load.call_args
            assert args[1] == "plan"
            assert kwargs == {"IDEA": "idea", "CONTEXT": "previous discussion"}

    @patch("subprocess.run")
    def test_raises_on_claude_failure(self, mock_run, handler):
        mock_run.return_value = MagicMock(returncode=1, stderr="rate limited")
        with patch("app.prompts.load_skill_prompt", return_value="prompt"), \
             patch("app.utils.get_model_config", return_value={"chat": "", "fallback": ""}), \
             patch("app.cli_provider.build_full_command", return_value=["claude", "-p", "test"]):
            with pytest.raises(RuntimeError, match="plan generation failed"):
                handler._generate_plan("/project", "idea")

    @patch("subprocess.run")
    def test_uses_read_only_tools(self, mock_run, handler):
        mock_run.return_value = MagicMock(returncode=0, stdout="plan", stderr="")
        with patch("app.prompts.load_skill_prompt", return_value="prompt"), \
             patch("app.utils.get_model_config", return_value={"chat": "", "fallback": ""}):
            handler._generate_plan("/project", "idea")
            cmd = mock_run.call_args[0][0]
            tools_idx = cmd.index("--allowedTools")
            tools = cmd[tools_idx + 1]
            assert "Read" in tools
            assert "Glob" in tools
            assert "Grep" in tools
            # No write tools
            assert "Write" not in tools
            assert "Edit" not in tools
            assert "Bash" not in tools


# ---------------------------------------------------------------------------
# issue_create (via app.github)
# ---------------------------------------------------------------------------

class TestIssueCreateIntegration:
    """Tests that handler calls issue_create correctly (tested in test_github.py)."""

    def test_handle_new_plan_calls_issue_create(self, handler, ctx):
        with patch.object(handler, "_resolve_project_path", return_value="/project"), \
             patch.object(handler, "_get_repo_info", return_value=("sukria", "koan")), \
             patch.object(handler, "_generate_plan", return_value="## Plan\n\nStep 1"), \
             patch("app.github.subprocess.run", return_value=MagicMock(returncode=0, stdout="https://github.com/sukria/koan/issues/99\n")):
            handler._handle_new_plan(ctx, "koan", "Add feature")
            last_call = ctx.send_message.call_args_list[-1]
            assert "issues/99" in last_call[0][0]


# ---------------------------------------------------------------------------
# _fetch_issue_context
# ---------------------------------------------------------------------------

class TestFetchIssueContext:
    @patch("app.github.subprocess.run")
    def test_returns_title_body_and_comments(self, mock_run, handler):
        # Two calls: issue (title+body) then comments (JSON with author/date)
        comments_data = json.dumps([
            {"author": "alice", "date": "2026-02-01T10:00:00Z", "body": "Looks good"},
            {"author": "bob", "date": "2026-02-02T14:00:00Z", "body": "Needs changes"},
        ])
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps({"title": "My Issue", "body": "Issue body content"})),
            MagicMock(returncode=0, stdout=comments_data),
        ]
        title, body, comments = handler._fetch_issue_context("sukria", "koan", "64")
        assert title == "My Issue"
        assert body == "Issue body content"
        assert "alice" in comments
        assert "Looks good" in comments
        assert "bob" in comments

    @patch("app.github.subprocess.run")
    def test_handles_non_json_issue_response(self, mock_run, handler):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="plain text body"),
            MagicMock(returncode=0, stdout=""),
        ]
        title, body, comments = handler._fetch_issue_context("sukria", "koan", "1")
        assert title == ""
        assert body == "plain text body"

    @patch("app.github.subprocess.run")
    def test_comments_preserve_authorship(self, mock_run, handler):
        comments_data = json.dumps([
            {"author": "sukria", "date": "2026-02-05T09:00:00Z", "body": "Please also handle edge case X"},
        ])
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps({"title": "Title", "body": "body"})),
            MagicMock(returncode=0, stdout=comments_data),
        ]
        _, _, comments = handler._fetch_issue_context("sukria", "koan", "1")
        assert "sukria" in comments
        assert "2026-02-05" in comments
        assert "edge case X" in comments


# ---------------------------------------------------------------------------
# _comment_on_issue — multiline-safe via stdin
# ---------------------------------------------------------------------------

class TestCommentOnIssue:
    @patch("app.github.subprocess.run")
    def test_posts_comment_via_stdin(self, mock_run, handler):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        body = "## Plan\n\nStep 1: Do X\nStep 2: Do Y"
        handler._comment_on_issue("sukria", "koan", "64", body)
        mock_run.assert_called_once()
        # Body passed via stdin (input kwarg)
        assert mock_run.call_args.kwargs.get("input") == body
        cmd = mock_run.call_args[0][0]
        assert "-F" in cmd
        assert "body=@-" in cmd

    @patch("app.github.subprocess.run")
    def test_raises_on_failure(self, mock_run, handler):
        mock_run.return_value = MagicMock(returncode=1, stderr="not authorized")
        with pytest.raises(RuntimeError, match="gh failed"):
            handler._comment_on_issue("owner", "repo", "1", "body")

    @patch("app.github.subprocess.run")
    def test_multiline_body_preserved(self, mock_run, handler):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        body = "Line 1\n\n## Section\n\n- bullet\n- bullet 2\n\n```python\ndef foo():\n    pass\n```"
        handler._comment_on_issue("o", "r", "1", body)
        assert mock_run.call_args.kwargs.get("input") == body


# ---------------------------------------------------------------------------
# _resolve_project_path
# ---------------------------------------------------------------------------

class TestResolveProjectPathFallback:
    """Tests for _resolve_project_path with fallback=True (existing issue mode)."""

    def test_exact_name_match(self, handler):
        with patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan"), ("web", "/home/web")]):
            assert handler._resolve_project_path("koan", fallback=True) == "/home/koan"

    def test_directory_basename_match(self, handler):
        with patch("app.utils.get_known_projects", return_value=[("myproject", "/home/koan")]):
            assert handler._resolve_project_path("koan", fallback=True) == "/home/koan"

    def test_defaults_to_first_project(self, handler):
        with patch("app.utils.get_known_projects", return_value=[("first", "/a"), ("second", "/b")]):
            assert handler._resolve_project_path("unknown", fallback=True) == "/a"

    def test_falls_back_to_env(self, handler):
        with patch("app.utils.get_known_projects", return_value=[]), \
             patch.dict("os.environ", {"KOAN_PROJECT_PATH": "/from/env"}):
            assert handler._resolve_project_path("anything", fallback=True) == "/from/env"

    def test_no_fallback_returns_none(self, handler):
        """Without fallback, unknown project returns None."""
        with patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            assert handler._resolve_project_path("unknown", fallback=False) is None


# ---------------------------------------------------------------------------
# SKILL.md — structure validation
# ---------------------------------------------------------------------------

class TestSkillMd:
    def test_skill_md_parses(self):
        from app.skills import parse_skill_md
        skill = parse_skill_md(Path(__file__).parent.parent / "skills" / "core" / "plan" / "SKILL.md")
        assert skill is not None
        assert skill.name == "plan"
        assert skill.scope == "core"
        assert skill.worker is True
        assert len(skill.commands) == 1
        assert skill.commands[0].name == "plan"

    def test_skill_registered_in_registry(self):
        from app.skills import build_registry
        registry = build_registry()
        skill = registry.find_by_command("plan")
        assert skill is not None
        assert skill.name == "plan"

    def test_skill_handler_exists(self):
        handler_path = Path(__file__).parent.parent / "skills" / "core" / "plan" / "handler.py"
        assert handler_path.exists()


# ---------------------------------------------------------------------------
# System prompt — plan.md
# ---------------------------------------------------------------------------

PLAN_PROMPT_PATH = Path(__file__).parent.parent / "skills" / "core" / "plan" / "prompts" / "plan.md"


class TestPlanPrompt:
    def test_prompt_file_exists(self):
        assert PLAN_PROMPT_PATH.exists()

    def test_prompt_has_placeholders(self):
        content = PLAN_PROMPT_PATH.read_text()
        assert "{IDEA}" in content
        assert "{CONTEXT}" in content

    def test_prompt_has_required_sections(self):
        content = PLAN_PROMPT_PATH.read_text()
        assert "Implementation Steps" in content
        assert "Corner Cases" in content
        assert "Open Questions" in content
        assert "Testing Strategy" in content


# ---------------------------------------------------------------------------
# Issue URL regex
# ---------------------------------------------------------------------------

class TestIssueUrlRegex:
    def test_standard_url(self, handler):
        m = handler._ISSUE_URL_RE.search("https://github.com/sukria/koan/issues/64")
        assert m is not None
        assert m.group("owner") == "sukria"
        assert m.group("repo") == "koan"
        assert m.group("number") == "64"

    def test_http_url(self, handler):
        m = handler._ISSUE_URL_RE.search("http://github.com/a/b/issues/1")
        assert m is not None

    def test_url_with_fragment(self, handler):
        m = handler._ISSUE_URL_RE.search("https://github.com/owner/repo/issues/42#comment-123")
        assert m is not None
        assert m.group("number") == "42"

    def test_url_in_text(self, handler):
        m = handler._ISSUE_URL_RE.search("Check https://github.com/o/r/issues/5 please")
        assert m is not None
        assert m.group("number") == "5"

    def test_pr_url_does_not_match(self, handler):
        m = handler._ISSUE_URL_RE.search("https://github.com/o/r/pull/5")
        assert m is None

    def test_no_url_returns_none(self, handler):
        m = handler._ISSUE_URL_RE.search("just some text")
        assert m is None
