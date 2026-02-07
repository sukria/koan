"""Tests for app.github — shared gh CLI wrapper."""

import subprocess
from unittest.mock import patch, MagicMock

import pytest

from app.github import run_gh, pr_create, issue_create, api


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
