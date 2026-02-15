"""Tests for app.github — shared gh CLI wrapper."""

import subprocess
from unittest.mock import patch, MagicMock

import pytest

from app.github import run_gh, pr_create, issue_create, api, get_gh_username, count_open_prs
import app.github as github_module


# ---------------------------------------------------------------------------
# run_gh
# ---------------------------------------------------------------------------

class TestRunGh:
    @patch("app.github.subprocess.run")
    def test_returns_stripped_stdout(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="  output\n")
        assert run_gh("pr", "view", "1") == "output"

    @patch("app.github.subprocess.run")
    def test_passes_cwd_and_timeout(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok")
        run_gh("repo", "view", cwd="/project", timeout=10)
        mock_run.assert_called_once()
        assert mock_run.call_args.kwargs["cwd"] == "/project"
        assert mock_run.call_args.kwargs["timeout"] == 10

    @patch("app.github.subprocess.run")
    def test_builds_correct_command(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        run_gh("pr", "view", "42", "--repo", "owner/repo")
        cmd = mock_run.call_args[0][0]
        assert cmd == ["gh", "pr", "view", "42", "--repo", "owner/repo"]

    @patch("app.github.subprocess.run")
    def test_raises_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr="not found")
        with pytest.raises(RuntimeError, match="gh failed"):
            run_gh("pr", "view", "999")

    @patch("app.github.subprocess.run")
    def test_error_message_includes_stderr(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr="auth required")
        with pytest.raises(RuntimeError, match="auth required"):
            run_gh("api", "repos/o/r")

    @patch("app.github.subprocess.run")
    def test_timeout_propagates(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=5)
        with pytest.raises(subprocess.TimeoutExpired):
            run_gh("pr", "view", "1", timeout=5)


# ---------------------------------------------------------------------------
# pr_create
# ---------------------------------------------------------------------------

class TestPrCreate:
    @patch("app.github.subprocess.run")
    def test_defaults_to_draft(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="https://github.com/o/r/pull/1"
        )
        url = pr_create("My PR", "Description")
        cmd = mock_run.call_args[0][0]
        assert "--draft" in cmd
        assert "pull/1" in url

    @patch("app.github.subprocess.run")
    def test_draft_false_omits_flag(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="https://github.com/o/r/pull/2"
        )
        pr_create("My PR", "Description", draft=False)
        cmd = mock_run.call_args[0][0]
        assert "--draft" not in cmd

    @patch("app.github.subprocess.run")
    def test_includes_base_when_provided(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="url")
        pr_create("Title", "Body", base="develop")
        cmd = mock_run.call_args[0][0]
        assert "--base" in cmd
        idx = cmd.index("--base")
        assert cmd[idx + 1] == "develop"

    @patch("app.github.subprocess.run")
    def test_no_base_omits_flag(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="url")
        pr_create("Title", "Body")
        cmd = mock_run.call_args[0][0]
        assert "--base" not in cmd

    @patch("app.github.subprocess.run")
    def test_passes_cwd(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="url")
        pr_create("Title", "Body", cwd="/my/project")
        assert mock_run.call_args.kwargs["cwd"] == "/my/project"

    @patch("app.github.subprocess.run")
    def test_passes_title_and_body(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="url")
        pr_create("My Title", "My Body")
        cmd = mock_run.call_args[0][0]
        assert "--title" in cmd
        assert "My Title" in cmd
        assert "--body" in cmd
        assert "My Body" in cmd


# ---------------------------------------------------------------------------
# issue_create
# ---------------------------------------------------------------------------

class TestIssueCreate:
    @patch("app.github.subprocess.run")
    def test_creates_issue(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="https://github.com/o/r/issues/42"
        )
        url = issue_create("Bug Title", "Bug description")
        assert "issues/42" in url
        cmd = mock_run.call_args[0][0]
        assert cmd[:2] == ["gh", "issue"]
        assert "--title" in cmd
        assert "Bug Title" in cmd

    @patch("app.github.subprocess.run")
    def test_with_labels(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="url")
        issue_create("Title", "Body", labels=["bug", "priority"])
        cmd = mock_run.call_args[0][0]
        assert "--label" in cmd
        idx = cmd.index("--label")
        assert cmd[idx + 1] == "bug,priority"

    @patch("app.github.subprocess.run")
    def test_no_labels_omits_flag(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="url")
        issue_create("Title", "Body")
        cmd = mock_run.call_args[0][0]
        assert "--label" not in cmd

    @patch("app.github.subprocess.run")
    def test_passes_cwd(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="url")
        issue_create("Title", "Body", cwd="/project")
        assert mock_run.call_args.kwargs["cwd"] == "/project"


# ---------------------------------------------------------------------------
# api
# ---------------------------------------------------------------------------

class TestApi:
    @patch("app.github.subprocess.run")
    def test_get_request(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout='{"id": 1}')
        result = api("repos/owner/repo/issues/1")
        assert '"id": 1' in result
        cmd = mock_run.call_args[0][0]
        assert cmd[:3] == ["gh", "api", "repos/owner/repo/issues/1"]
        # GET should not add -X flag
        assert "-X" not in cmd

    @patch("app.github.subprocess.run")
    def test_with_jq_filter(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="filtered")
        api("repos/o/r/issues", jq=".[] | .title")
        cmd = mock_run.call_args[0][0]
        assert "--jq" in cmd
        idx = cmd.index("--jq")
        assert cmd[idx + 1] == ".[] | .title"

    @patch("app.github.subprocess.run")
    def test_post_with_input_data(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        api("repos/o/r/issues/1/comments", input_data="My comment body")
        assert mock_run.call_args.kwargs.get("input") == "My comment body"
        cmd = mock_run.call_args[0][0]
        assert "-F" in cmd
        assert "body=@-" in cmd

    @patch("app.github.subprocess.run")
    def test_explicit_method(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        api("repos/o/r/issues/1", method="PATCH")
        cmd = mock_run.call_args[0][0]
        assert "-X" in cmd
        idx = cmd.index("-X")
        assert cmd[idx + 1] == "PATCH"

    @patch("app.github.subprocess.run")
    def test_extra_args(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        api("repos/o/r/pulls/1/comments", extra_args=["--paginate"])
        cmd = mock_run.call_args[0][0]
        assert "--paginate" in cmd

    @patch("app.github.subprocess.run")
    def test_raises_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr="not found")
        with pytest.raises(RuntimeError, match="gh failed"):
            api("repos/o/r/nonexistent")

    @patch("app.github.subprocess.run")
    def test_input_data_failure_raises(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr="forbidden")
        with pytest.raises(RuntimeError, match="gh failed"):
            api("repos/o/r/issues/1/comments", input_data="body")


# ---------------------------------------------------------------------------
# get_gh_username
# ---------------------------------------------------------------------------

class TestGetGhUsername:

    def setup_method(self):
        """Reset cached username between tests."""
        github_module._cached_gh_username = None

    @patch("app.github_auth.get_github_user", return_value="koan-bot")
    def test_returns_github_user_env(self, mock_get_user):
        assert get_gh_username() == "koan-bot"

    @patch("app.github_auth.get_github_user", return_value="")
    @patch("app.github.run_gh", return_value="fallback-user")
    def test_falls_back_to_gh_api(self, mock_gh, mock_get_user):
        assert get_gh_username() == "fallback-user"
        mock_gh.assert_called_once_with("api", "user", "--jq", ".login", timeout=15)

    @patch("app.github_auth.get_github_user", return_value="")
    @patch("app.github.run_gh", side_effect=RuntimeError("not logged in"))
    def test_returns_empty_on_failure(self, mock_gh, mock_get_user):
        assert get_gh_username() == ""

    @patch("app.github_auth.get_github_user", return_value="")
    @patch("app.github.run_gh", return_value="cached-user")
    def test_caches_gh_api_result(self, mock_gh, mock_get_user):
        assert get_gh_username() == "cached-user"
        assert get_gh_username() == "cached-user"
        # Only one call to run_gh despite two invocations
        mock_gh.assert_called_once()

    @patch("app.github_auth.get_github_user", return_value="")
    @patch("app.github.run_gh", side_effect=RuntimeError("fail"))
    def test_caches_failure_as_empty(self, mock_gh, mock_get_user):
        assert get_gh_username() == ""
        assert get_gh_username() == ""
        # Only one call — failure is cached too
        mock_gh.assert_called_once()

    @patch("app.github_auth.get_github_user", return_value="env-user")
    def test_env_var_takes_priority_over_cache(self, mock_get_user):
        # Pre-populate cache
        github_module._cached_gh_username = "cached-user"
        assert get_gh_username() == "env-user"


# ---------------------------------------------------------------------------
# count_open_prs
# ---------------------------------------------------------------------------

class TestCountOpenPrs:

    @patch("app.github.run_gh", return_value="5")
    def test_returns_count(self, mock_gh):
        assert count_open_prs("owner/repo", "koan-bot") == 5
        mock_gh.assert_called_once_with(
            "pr", "list",
            "--repo", "owner/repo",
            "--state", "open",
            "--author", "koan-bot",
            "--json", "number",
            "--jq", "length",
            cwd=None, timeout=15,
        )

    @patch("app.github.run_gh", return_value="0")
    def test_returns_zero_when_no_prs(self, mock_gh):
        assert count_open_prs("owner/repo", "koan-bot") == 0

    @patch("app.github.run_gh", side_effect=RuntimeError("auth error"))
    def test_returns_negative_one_on_error(self, mock_gh):
        assert count_open_prs("owner/repo", "koan-bot") == -1

    @patch("app.github.run_gh", side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=15))
    def test_returns_negative_one_on_timeout(self, mock_gh):
        assert count_open_prs("owner/repo", "koan-bot") == -1

    def test_returns_negative_one_for_empty_author(self):
        assert count_open_prs("owner/repo", "") == -1

    @patch("app.github.run_gh", return_value="not-a-number")
    def test_returns_negative_one_on_non_numeric_output(self, mock_gh):
        assert count_open_prs("owner/repo", "koan-bot") == -1

    @patch("app.github.run_gh", return_value="3")
    def test_passes_cwd(self, mock_gh):
        count_open_prs("owner/repo", "koan-bot", cwd="/my/project")
        assert mock_gh.call_args.kwargs["cwd"] == "/my/project"

    @patch("app.github.run_gh", return_value="")
    def test_empty_output_returns_negative_one(self, mock_gh):
        assert count_open_prs("owner/repo", "koan-bot") == -1
