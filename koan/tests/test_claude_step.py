"""Tests for claude_step.py — shared CI/CD pipeline helpers.

Tests _run_git, _truncate, _rebase_onto_target, run_claude,
commit_if_changes, and run_claude_step.
"""

import subprocess
from unittest.mock import MagicMock, call, patch

import pytest

from app.claude_step import (
    _rebase_onto_target,
    _run_git,
    _truncate,
    commit_if_changes,
    run_claude,
    run_claude_step,
    strip_cli_noise,
)


# ---------- _run_git ----------


class TestRunGit:
    """Tests for _run_git helper."""

    @patch("app.cli_exec.subprocess.run")
    def test_success_returns_stdout(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="  abc123  \n")
        result = _run_git(["git", "rev-parse", "HEAD"])
        assert result == "abc123"

    @patch("app.cli_exec.subprocess.run")
    def test_failure_raises(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=128, stderr="fatal: not a git repo"
        )
        with pytest.raises(RuntimeError, match="git failed"):
            _run_git(["git", "status"])

    @patch("app.cli_exec.subprocess.run")
    def test_passes_cwd_and_timeout(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok")
        _run_git(["git", "status"], cwd="/tmp/test", timeout=30)
        mock_run.assert_called_once_with(
            ["git", "status"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
            cwd="/tmp/test",
        )

    @patch("app.cli_exec.subprocess.run")
    def test_error_message_truncates_stderr(self, mock_run):
        long_stderr = "x" * 500
        mock_run.return_value = MagicMock(returncode=1, stderr=long_stderr)
        with pytest.raises(RuntimeError) as exc_info:
            _run_git(["git", "bad"])
        # Stderr in error message should be truncated to 200 chars
        assert len(str(exc_info.value)) < 300


# ---------- _truncate ----------


class TestTruncate:
    """Tests for _truncate helper."""

    def test_short_text_unchanged(self):
        assert _truncate("hello", 10) == "hello"

    def test_exact_limit_unchanged(self):
        assert _truncate("12345", 5) == "12345"

    def test_over_limit_truncated(self):
        result = _truncate("1234567890", 5)
        assert result.startswith("12345")
        assert "truncated" in result

    def test_empty_string(self):
        assert _truncate("", 10) == ""


# ---------- strip_cli_noise ----------


class TestStripCliNoise:
    """Tests for strip_cli_noise helper."""

    def test_removes_max_turns_error(self):
        text = "Some reflection text.\nError: Reached max turns (1)"
        assert strip_cli_noise(text) == "Some reflection text."

    def test_removes_higher_turn_counts(self):
        text = "Output\nError: Reached max turns (3)"
        assert strip_cli_noise(text) == "Output"

    def test_preserves_clean_text(self):
        text = "A genuine reflection.\nWith multiple lines."
        assert strip_cli_noise(text) == text

    def test_empty_string(self):
        assert strip_cli_noise("") == ""

    def test_only_error_line_returns_empty(self):
        assert strip_cli_noise("Error: Reached max turns (1)") == ""

    def test_multiline_with_error_in_middle(self):
        text = "Line 1\nError: Reached max turns (1)\nLine 3"
        assert strip_cli_noise(text) == "Line 1\nLine 3"

    def test_case_insensitive(self):
        text = "Output\nerror: reached MAX TURNS (2)"
        assert strip_cli_noise(text) == "Output"

    def test_preserves_unrelated_error_lines(self):
        text = "Output\nError: something else happened"
        assert strip_cli_noise(text) == text

    def test_multiple_error_lines(self):
        text = "Line 1\nError: Reached max turns (1)\nLine 2\nError: Reached max turns (1)"
        assert strip_cli_noise(text) == "Line 1\nLine 2"


# ---------- _rebase_onto_target ----------


class TestRebaseOntoTarget:
    """Tests for _rebase_onto_target."""

    @patch("app.claude_step._run_git")
    def test_origin_success(self, mock_git):
        result = _rebase_onto_target("main", "/project")
        assert result == "origin"
        assert mock_git.call_count == 2
        mock_git.assert_any_call(
            ["git", "fetch", "origin", "main"], cwd="/project"
        )

    @patch("app.cli_exec.subprocess.run")
    @patch("app.claude_step._run_git")
    def test_origin_fails_upstream_succeeds(self, mock_git, mock_subprocess):
        def side_effect(cmd, **kwargs):
            if "origin" in cmd:
                raise RuntimeError("fetch failed")
            return MagicMock(returncode=0, stdout="ok")

        mock_git.side_effect = side_effect
        result = _rebase_onto_target("main", "/project")
        assert result == "upstream"

    @patch("app.cli_exec.subprocess.run")
    @patch("app.claude_step._run_git")
    def test_both_fail_returns_none(self, mock_git, mock_subprocess):
        mock_git.side_effect = RuntimeError("fail")
        result = _rebase_onto_target("main", "/project")
        assert result is None

    @patch("app.cli_exec.subprocess.run")
    @patch("app.claude_step._run_git")
    def test_rebase_abort_called_on_failure(self, mock_git, mock_subprocess):
        mock_git.side_effect = RuntimeError("conflict")
        _rebase_onto_target("main", "/project")
        # Should call rebase --abort for each failed remote
        abort_calls = [
            c
            for c in mock_subprocess.call_args_list
            if "rebase" in c[0][0] and "--abort" in c[0][0]
        ]
        assert len(abort_calls) == 2


# ---------- run_claude ----------


class TestRunClaude:
    """Tests for run_claude — CLI invocation wrapper."""

    @patch("app.cli_exec.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="  done  \n", stderr=""
        )
        result = run_claude(["claude", "-p", "test"], "/project")
        assert result["success"] is True
        assert result["output"] == "done"
        assert result["error"] == ""

    @patch("app.cli_exec.subprocess.run")
    def test_failure_with_stderr(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="partial", stderr="something broke"
        )
        result = run_claude(["claude", "-p", "test"], "/project")
        assert result["success"] is False
        assert "Exit code 1" in result["error"]
        assert "something broke" in result["error"]

    @patch("app.cli_exec.subprocess.run")
    def test_failure_no_stderr(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr=""
        )
        result = run_claude(["claude", "-p", "test"], "/project")
        assert result["success"] is False
        assert "no stderr" in result["error"]

    @patch("app.cli_exec.subprocess.run")
    def test_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=600)
        result = run_claude(["claude", "-p", "test"], "/project")
        assert result["success"] is False
        assert "Timeout" in result["error"]
        assert "600" in result["error"]

    @patch("app.cli_exec.subprocess.run")
    def test_custom_timeout(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        run_claude(["claude", "-p", "test"], "/project", timeout=120)
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["timeout"] == 120
        assert call_kwargs["cwd"] == "/project"

    @patch("app.cli_exec.subprocess.run")
    def test_long_stderr_truncated(self, mock_run):
        long_err = "E" * 1000
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr=long_err
        )
        result = run_claude(["claude", "-p", "test"], "/project")
        # Should only keep last 500 chars of stderr
        assert len(result["error"]) < 600


# ---------- commit_if_changes ----------


class TestCommitIfChanges:
    """Tests for commit_if_changes."""

    @patch("app.claude_step._run_git")
    @patch("app.cli_exec.subprocess.run")
    def test_no_changes_returns_false(self, mock_run, mock_git):
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        result = commit_if_changes("/project", "test msg")
        assert result is False
        # Should not call git add or commit
        mock_git.assert_not_called()

    @patch("app.claude_step._run_git")
    @patch("app.cli_exec.subprocess.run")
    def test_with_changes_commits(self, mock_run, mock_git):
        mock_run.return_value = MagicMock(
            stdout=" M file.py\n", returncode=0
        )
        result = commit_if_changes("/project", "test msg")
        assert result is True
        assert mock_git.call_count == 2
        mock_git.assert_any_call(["git", "add", "-A"], cwd="/project")
        mock_git.assert_any_call(
            ["git", "commit", "-m", "test msg"], cwd="/project"
        )

    @patch("app.claude_step._run_git")
    @patch("app.cli_exec.subprocess.run")
    def test_whitespace_only_status_is_no_changes(self, mock_run, mock_git):
        mock_run.return_value = MagicMock(stdout="   \n  ", returncode=0)
        result = commit_if_changes("/project", "msg")
        assert result is False


# ---------- run_claude_step ----------


class TestRunClaudeStep:
    """Tests for run_claude_step — orchestrator."""

    @patch("app.claude_step.commit_if_changes", return_value=True)
    @patch("app.claude_step.run_claude")
    @patch("app.claude_step.build_full_command", return_value=["claude", "-p", "fix bug", "--allowedTools", "Bash,Read,Write,Glob,Grep,Edit", "--model", "opus"])
    @patch(
        "app.claude_step.get_model_config",
        return_value={"mission": "opus", "fallback": "sonnet", "chat": "", "lightweight": "", "review_mode": ""},
    )
    def test_success_with_commit(self, mock_config, mock_flags, mock_claude, mock_commit):
        mock_claude.return_value = {"success": True, "output": "done", "error": ""}
        actions = []
        result = run_claude_step(
            prompt="fix bug",
            project_path="/project",
            commit_msg="fix: bug",
            success_label="Bug fixed",
            failure_label="Fix failed",
            actions_log=actions,
        )
        assert result is True
        assert "Bug fixed" in actions

    @patch("app.claude_step.commit_if_changes", return_value=False)
    @patch("app.claude_step.run_claude")
    @patch("app.claude_step.build_full_command", return_value=["claude", "-p", "test"])
    @patch(
        "app.claude_step.get_model_config",
        return_value={"mission": "", "fallback": "", "chat": "", "lightweight": "", "review_mode": ""},
    )
    def test_success_no_commit(self, mock_config, mock_flags, mock_claude, mock_commit):
        mock_claude.return_value = {"success": True, "output": "ok", "error": ""}
        actions = []
        result = run_claude_step(
            prompt="review code",
            project_path="/project",
            commit_msg="chore: review",
            success_label="Reviewed",
            failure_label="Review failed",
            actions_log=actions,
        )
        assert result is False
        assert actions == []

    @patch("app.claude_step.run_claude")
    @patch("app.claude_step.build_full_command", return_value=["claude", "-p", "test"])
    @patch(
        "app.claude_step.get_model_config",
        return_value={"mission": "", "fallback": "", "chat": "", "lightweight": "", "review_mode": ""},
    )
    def test_failure_logs_error(self, mock_config, mock_flags, mock_claude):
        mock_claude.return_value = {
            "success": False,
            "output": "",
            "error": "Exit code 1: crash",
        }
        actions = []
        result = run_claude_step(
            prompt="fix bug",
            project_path="/project",
            commit_msg="fix: bug",
            success_label="Fixed",
            failure_label="Fix failed",
            actions_log=actions,
        )
        assert result is False
        assert len(actions) == 1
        assert "Fix failed" in actions[0]
        assert "crash" in actions[0]

    @patch("app.claude_step.run_claude")
    @patch("app.claude_step.build_full_command", return_value=["claude", "-p", "test"])
    @patch(
        "app.claude_step.get_model_config",
        return_value={"mission": "", "fallback": "", "chat": "", "lightweight": "", "review_mode": ""},
    )
    def test_failure_empty_label_no_log(self, mock_config, mock_flags, mock_claude):
        mock_claude.return_value = {
            "success": False,
            "output": "",
            "error": "fail",
        }
        actions = []
        result = run_claude_step(
            prompt="test",
            project_path="/p",
            commit_msg="x",
            success_label="OK",
            failure_label="",
            actions_log=actions,
        )
        assert result is False
        assert actions == []

    @patch("app.claude_step.commit_if_changes", return_value=True)
    @patch("app.claude_step.run_claude")
    @patch("app.claude_step.build_full_command", return_value=["claude", "-p", "test"])
    @patch(
        "app.claude_step.get_model_config",
        return_value={"mission": "", "fallback": "", "chat": "", "lightweight": "", "review_mode": ""},
    )
    def test_use_skill_adds_skill_tool(self, mock_config, mock_cmd, mock_claude, mock_commit):
        mock_claude.return_value = {"success": True, "output": "done", "error": ""}
        run_claude_step(
            prompt="refactor",
            project_path="/project",
            commit_msg="refactor",
            success_label="OK",
            failure_label="Fail",
            actions_log=[],
            use_skill=True,
        )
        # Verify build_full_command was called with Skill in allowed_tools
        call_kwargs = mock_cmd.call_args
        allowed = call_kwargs.kwargs.get("allowed_tools", [])
        assert "Skill" in allowed

    @patch("app.claude_step.commit_if_changes", return_value=True)
    @patch("app.claude_step.run_claude")
    @patch("app.claude_step.build_full_command", return_value=["claude", "-p", "test"])
    @patch(
        "app.claude_step.get_model_config",
        return_value={"mission": "", "fallback": "", "chat": "", "lightweight": "", "review_mode": ""},
    )
    def test_no_skill_by_default(self, mock_config, mock_cmd, mock_claude, mock_commit):
        mock_claude.return_value = {"success": True, "output": "done", "error": ""}
        run_claude_step(
            prompt="fix",
            project_path="/project",
            commit_msg="fix",
            success_label="OK",
            failure_label="Fail",
            actions_log=[],
        )
        # Verify build_full_command was called without Skill in allowed_tools
        call_kwargs = mock_cmd.call_args
        allowed = call_kwargs.kwargs.get("allowed_tools", [])
        assert "Skill" not in allowed

    @patch("app.claude_step.commit_if_changes", return_value=True)
    @patch("app.claude_step.run_claude")
    @patch("app.claude_step.build_full_command", return_value=["claude", "-p", "test"])
    @patch(
        "app.claude_step.get_model_config",
        return_value={"mission": "", "fallback": "", "chat": "", "lightweight": "", "review_mode": ""},
    )
    def test_custom_max_turns_and_timeout(self, mock_config, mock_cmd, mock_claude, mock_commit):
        mock_claude.return_value = {"success": True, "output": "ok", "error": ""}
        run_claude_step(
            prompt="deep work",
            project_path="/project",
            commit_msg="chore: deep",
            success_label="Done",
            failure_label="Fail",
            actions_log=[],
            max_turns=5,
            timeout=120,
        )
        # Verify build_full_command was called with max_turns=5
        call_kwargs = mock_cmd.call_args
        assert call_kwargs.kwargs.get("max_turns") == 5
        # Timeout passed to run_claude
        assert mock_claude.call_args[1]["timeout"] == 120

    @patch("app.claude_step.commit_if_changes", return_value=True)
    @patch("app.claude_step.run_claude")
    @patch("app.claude_step.build_full_command", return_value=["claude", "-p", "fix bug", "--allowedTools", "Bash,Read,Write,Glob,Grep,Edit", "--model", "opus"])
    @patch(
        "app.claude_step.get_model_config",
        return_value={"mission": "opus", "fallback": "sonnet", "chat": "", "lightweight": "", "review_mode": ""},
    )
    def test_model_config_passed_to_flags(self, mock_config, mock_cmd, mock_claude, mock_commit):
        mock_claude.return_value = {"success": True, "output": "ok", "error": ""}
        run_claude_step(
            prompt="test",
            project_path="/p",
            commit_msg="test",
            success_label="OK",
            failure_label="Fail",
            actions_log=[],
        )
        # Verify model and fallback passed to build_full_command
        call_kwargs = mock_cmd.call_args.kwargs
        assert call_kwargs["model"] == "opus"
        assert call_kwargs["fallback"] == "sonnet"

    @patch("app.claude_step.commit_if_changes", return_value=True)
    @patch("app.claude_step.run_claude")
    @patch("app.claude_step.build_full_command", return_value=["claude", "-p", "test"])
    @patch(
        "app.claude_step.get_model_config",
        return_value={"mission": "", "fallback": "", "chat": "", "lightweight": "", "review_mode": ""},
    )
    def test_success_empty_label_no_log(self, mock_config, mock_flags, mock_claude, mock_commit):
        mock_claude.return_value = {"success": True, "output": "ok", "error": ""}
        actions = []
        result = run_claude_step(
            prompt="test",
            project_path="/p",
            commit_msg="test",
            success_label="",
            failure_label="Fail",
            actions_log=actions,
        )
        # commit_if_changes returns True but label is empty — still returns False
        assert result is False
        assert actions == []
