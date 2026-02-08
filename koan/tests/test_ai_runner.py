"""Tests for app.ai_runner — AI exploration CLI runner."""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.ai_runner import (
    run_exploration,
    _gather_git_activity,
    _gather_project_structure,
    _get_missions_context,
    _clean_response,
    main,
)


# ---------------------------------------------------------------------------
# _gather_git_activity
# ---------------------------------------------------------------------------

class TestGatherGitActivity:
    @patch("app.git_sync.run_git")
    def test_includes_recent_commits(self, mock_git):
        mock_git.return_value = "abc1234 fix login\ndef5678 add tests"
        result = _gather_git_activity("/tmp")
        assert "fix login" in result

    @patch("app.git_sync.run_git", return_value="")
    def test_handles_empty_output(self, mock_git):
        result = _gather_git_activity("/tmp")
        assert "No git activity" in result

    @patch("app.git_sync.run_git")
    def test_includes_branches(self, mock_git):
        mock_git.return_value = "origin/main\norigin/feature-x"
        result = _gather_git_activity("/tmp")
        assert "origin/main" in result

    @patch("app.git_sync.run_git", return_value="")
    def test_git_failure_returns_no_activity(self, mock_git):
        result = _gather_git_activity("/tmp")
        assert "No git activity" in result


# ---------------------------------------------------------------------------
# _gather_project_structure
# ---------------------------------------------------------------------------

class TestGatherProjectStructure:
    def test_lists_dirs_and_files(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()
        (tmp_path / "README.md").write_text("hello")
        (tmp_path / ".hidden").write_text("skip")

        result = _gather_project_structure(str(tmp_path))
        assert "src/" in result
        assert "tests/" in result
        assert "README.md" in result
        assert ".hidden" not in result

    def test_handles_nonexistent_path(self):
        result = _gather_project_structure("/nonexistent/path")
        assert "unavailable" in result.lower()

    def test_skips_hidden_dirs(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / "src").mkdir()
        result = _gather_project_structure(str(tmp_path))
        assert ".git" not in result
        assert "src/" in result


# ---------------------------------------------------------------------------
# _get_missions_context
# ---------------------------------------------------------------------------

class TestGetMissionsContext:
    def test_returns_in_progress_and_pending(self, tmp_path):
        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n## Pending\n\n- pending task\n\n"
            "## In Progress\n\n- active task\n\n## Done\n"
        )
        result = _get_missions_context(tmp_path)
        assert "active task" in result
        assert "pending task" in result

    def test_returns_no_active_when_empty(self, tmp_path):
        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n"
        )
        result = _get_missions_context(tmp_path)
        assert "No active" in result

    def test_handles_missing_file(self, tmp_path):
        result = _get_missions_context(tmp_path)
        assert "No active" in result

    def test_limits_entries(self, tmp_path):
        """Should limit to 5 entries per section."""
        missions_file = tmp_path / "missions.md"
        pending = "\n".join(f"- task {i}" for i in range(10))
        missions_file.write_text(
            f"# Missions\n\n## Pending\n\n{pending}\n\n"
            "## In Progress\n\n## Done\n"
        )
        result = _get_missions_context(tmp_path)
        assert "task 4" in result
        assert "task 5" not in result


# ---------------------------------------------------------------------------
# _clean_response
# ---------------------------------------------------------------------------

class TestCleanResponse:
    def test_strips_markdown_decorators(self):
        text = "### Header\n**bold** and __underline__"
        cleaned = _clean_response(text)
        assert "###" not in cleaned
        assert "**" not in cleaned
        assert "__" not in cleaned

    def test_strips_code_fences(self):
        text = "```python\nprint('hello')\n```"
        cleaned = _clean_response(text)
        assert "```" not in cleaned

    def test_strips_max_turns_error(self):
        text = "Error: max turns reached\nGood content here"
        cleaned = _clean_response(text)
        assert "max turns" not in cleaned
        assert "Good content" in cleaned

    def test_truncates_long_output(self):
        text = "x" * 3000
        cleaned = _clean_response(text)
        assert len(cleaned) <= 2000
        assert cleaned.endswith("...")

    def test_preserves_short_output(self):
        text = "Short and sweet"
        cleaned = _clean_response(text)
        assert cleaned == "Short and sweet"


# ---------------------------------------------------------------------------
# run_command (provider-level helper, tested via ai_runner integration)
# ---------------------------------------------------------------------------

class TestRunCommand:
    """Tests for the shared run_command helper in app.provider."""

    @patch("app.config.get_model_config", return_value={"chat": "sonnet", "fallback": ""})
    @patch("app.provider.build_full_command", return_value=["claude", "-p", "test"])
    @patch("app.provider.subprocess.run")
    def test_returns_stdout_on_success(self, mock_run, mock_cmd, mock_model):
        from app.cli_provider import run_command
        mock_run.return_value = MagicMock(
            returncode=0, stdout="Exploration results", stderr=""
        )
        result = run_command("test prompt", "/tmp", allowed_tools=["Read"])
        assert result == "Exploration results"

    @patch("app.config.get_model_config", return_value={"chat": "sonnet", "fallback": ""})
    @patch("app.provider.build_full_command", return_value=["claude", "-p", "test"])
    @patch("app.provider.subprocess.run")
    def test_raises_on_failure(self, mock_run, mock_cmd, mock_model):
        from app.cli_provider import run_command
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="quota exceeded"
        )
        with pytest.raises(RuntimeError, match="CLI invocation failed"):
            run_command("test prompt", "/tmp", allowed_tools=["Read"])

    @patch("app.config.get_model_config", return_value={"chat": "sonnet", "fallback": ""})
    @patch("app.provider.build_full_command", return_value=["claude", "-p", "test"])
    @patch("app.provider.subprocess.run")
    def test_passes_allowed_tools(self, mock_run, mock_cmd, mock_model):
        from app.cli_provider import run_command
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        run_command("test", "/tmp", allowed_tools=["Read", "Glob", "Grep", "Bash"])
        call_kwargs = mock_cmd.call_args[1]
        assert "Read" in call_kwargs["allowed_tools"]
        assert "Bash" in call_kwargs["allowed_tools"]

    @patch("app.config.get_model_config", return_value={"chat": "sonnet", "fallback": ""})
    @patch("app.provider.build_full_command", return_value=["claude", "-p", "test"])
    @patch("app.provider.subprocess.run")
    def test_passes_max_turns(self, mock_run, mock_cmd, mock_model):
        from app.cli_provider import run_command
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        run_command("test", "/tmp", allowed_tools=["Read"], max_turns=5)
        call_kwargs = mock_cmd.call_args[1]
        assert call_kwargs["max_turns"] == 5

    @patch("app.config.get_model_config", return_value={"chat": "sonnet", "fallback": ""})
    @patch("app.provider.build_full_command", return_value=["claude", "-p", "test"])
    @patch("app.provider.subprocess.run")
    def test_sets_cwd_to_project_path(self, mock_run, mock_cmd, mock_model):
        from app.cli_provider import run_command
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        run_command("test", "/my/project", allowed_tools=["Read"])
        assert mock_run.call_args[1]["cwd"] == "/my/project"


# ---------------------------------------------------------------------------
# run_exploration
# ---------------------------------------------------------------------------

class TestRunExploration:
    @patch("app.cli_provider.run_command", return_value="Found 3 issues")
    @patch("app.ai_runner._get_missions_context", return_value="No active missions.")
    @patch("app.ai_runner._gather_project_structure", return_value="Directories: src/")
    @patch("app.ai_runner._gather_git_activity", return_value="Recent commits: abc")
    @patch("app.prompts.load_skill_prompt", return_value="Explore myapp")
    def test_success_returns_true(
        self, mock_prompt, mock_git, mock_struct, mock_missions, mock_claude,
        tmp_path
    ):
        notify = MagicMock()
        success, summary = run_exploration(
            str(tmp_path), "myapp", str(tmp_path),
            notify_fn=notify,
        )
        assert success is True
        assert "completed" in summary.lower()

    @patch("app.cli_provider.run_command", return_value="Found 3 issues")
    @patch("app.ai_runner._get_missions_context", return_value="No active missions.")
    @patch("app.ai_runner._gather_project_structure", return_value="Directories: src/")
    @patch("app.ai_runner._gather_git_activity", return_value="Recent commits: abc")
    @patch("app.prompts.load_skill_prompt", return_value="Explore myapp")
    def test_notifies_start_and_result(
        self, mock_prompt, mock_git, mock_struct, mock_missions, mock_claude,
        tmp_path
    ):
        notify = MagicMock()
        run_exploration(
            str(tmp_path), "myapp", str(tmp_path),
            notify_fn=notify,
        )
        assert notify.call_count == 2
        # First call: "Exploring myapp..."
        assert "Exploring" in notify.call_args_list[0][0][0]
        # Second call: exploration result
        assert "myapp" in notify.call_args_list[1][0][0]

    @patch("app.cli_provider.run_command", side_effect=RuntimeError("quota exceeded"))
    @patch("app.ai_runner._get_missions_context", return_value="No active missions.")
    @patch("app.ai_runner._gather_project_structure", return_value="Directories: src/")
    @patch("app.ai_runner._gather_git_activity", return_value="Recent commits: abc")
    @patch("app.prompts.load_skill_prompt", return_value="Explore myapp")
    def test_failure_returns_false(
        self, mock_prompt, mock_git, mock_struct, mock_missions, mock_claude,
        tmp_path
    ):
        notify = MagicMock()
        success, summary = run_exploration(
            str(tmp_path), "myapp", str(tmp_path),
            notify_fn=notify,
        )
        assert success is False
        assert "failed" in summary.lower()

    @patch("app.cli_provider.run_command", return_value="")
    @patch("app.ai_runner._get_missions_context", return_value="No active missions.")
    @patch("app.ai_runner._gather_project_structure", return_value="Directories: src/")
    @patch("app.ai_runner._gather_git_activity", return_value="Recent commits: abc")
    @patch("app.prompts.load_skill_prompt", return_value="Explore myapp")
    def test_empty_result_returns_false(
        self, mock_prompt, mock_git, mock_struct, mock_missions, mock_claude,
        tmp_path
    ):
        notify = MagicMock()
        success, summary = run_exploration(
            str(tmp_path), "myapp", str(tmp_path),
            notify_fn=notify,
        )
        assert success is False
        assert "empty" in summary.lower()

    @patch("app.cli_provider.run_command", return_value="Found 3 issues")
    @patch("app.ai_runner._get_missions_context", return_value="No active missions.")
    @patch("app.ai_runner._gather_project_structure", return_value="Directories: src/")
    @patch("app.ai_runner._gather_git_activity", return_value="Recent commits: abc")
    @patch("app.prompts.load_skill_prompt", return_value="Explore myapp")
    def test_loads_prompt_from_skill_dir(
        self, mock_prompt, mock_git, mock_struct, mock_missions, mock_claude,
        tmp_path
    ):
        notify = MagicMock()
        custom_dir = tmp_path / "custom"
        custom_dir.mkdir()
        run_exploration(
            str(tmp_path), "myapp", str(tmp_path),
            notify_fn=notify, skill_dir=custom_dir,
        )
        assert mock_prompt.call_args[0][0] == custom_dir
        assert mock_prompt.call_args[0][1] == "ai-explore"

    @patch("app.cli_provider.run_command", return_value="Found 3 issues")
    @patch("app.ai_runner._get_missions_context", return_value="No active missions.")
    @patch("app.ai_runner._gather_project_structure", return_value="Directories: src/")
    @patch("app.ai_runner._gather_git_activity", return_value="Recent commits: abc")
    @patch("app.prompts.load_skill_prompt", return_value="Explore myapp")
    def test_prompt_substitutions(
        self, mock_prompt, mock_git, mock_struct, mock_missions, mock_claude,
        tmp_path
    ):
        """Prompt should receive PROJECT_NAME, GIT_ACTIVITY, etc."""
        notify = MagicMock()
        run_exploration(
            str(tmp_path), "myapp", str(tmp_path),
            notify_fn=notify,
        )
        kwargs = mock_prompt.call_args[1]
        assert kwargs["PROJECT_NAME"] == "myapp"
        assert "GIT_ACTIVITY" in kwargs
        assert "PROJECT_STRUCTURE" in kwargs
        assert "MISSIONS_CONTEXT" in kwargs

    @patch("app.cli_provider.run_command", return_value="x" * 3000)
    @patch("app.ai_runner._get_missions_context", return_value="No active missions.")
    @patch("app.ai_runner._gather_project_structure", return_value="Directories: src/")
    @patch("app.ai_runner._gather_git_activity", return_value="Recent commits: abc")
    @patch("app.prompts.load_skill_prompt", return_value="Explore myapp")
    def test_truncates_telegram_output(
        self, mock_prompt, mock_git, mock_struct, mock_missions, mock_claude,
        tmp_path
    ):
        notify = MagicMock()
        run_exploration(
            str(tmp_path), "myapp", str(tmp_path),
            notify_fn=notify,
        )
        result_msg = notify.call_args_list[1][0][0]
        assert len(result_msg) <= 2100  # header + 2000 content


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

class TestCLI:
    @patch("app.ai_runner.run_exploration", return_value=(True, "Done"))
    def test_main_success_returns_0(self, mock_run):
        exit_code = main([
            "--project-path", "/tmp/myapp",
            "--project-name", "myapp",
            "--instance-dir", "/tmp/instance",
        ])
        assert exit_code == 0
        mock_run.assert_called_once()

    @patch("app.ai_runner.run_exploration", return_value=(False, "Failed"))
    def test_main_failure_returns_1(self, mock_run):
        exit_code = main([
            "--project-path", "/tmp/myapp",
            "--project-name", "myapp",
            "--instance-dir", "/tmp/instance",
        ])
        assert exit_code == 1

    @patch("app.ai_runner.run_exploration", return_value=(True, "Done"))
    def test_main_passes_correct_args(self, mock_run):
        main([
            "--project-path", "/tmp/myapp",
            "--project-name", "myapp",
            "--instance-dir", "/tmp/instance",
        ])
        kwargs = mock_run.call_args[1]
        assert kwargs["project_path"] == "/tmp/myapp"
        assert kwargs["project_name"] == "myapp"
        assert kwargs["instance_dir"] == "/tmp/instance"

    @patch("app.ai_runner.run_exploration", return_value=(True, "Done"))
    def test_main_sets_skill_dir(self, mock_run):
        main([
            "--project-path", "/tmp/myapp",
            "--project-name", "myapp",
            "--instance-dir", "/tmp/instance",
        ])
        kwargs = mock_run.call_args[1]
        skill_dir = kwargs["skill_dir"]
        assert skill_dir.name == "ai"
        assert "skills/core/ai" in str(skill_dir)

    def test_main_requires_project_path(self):
        with pytest.raises(SystemExit):
            main(["--project-name", "myapp", "--instance-dir", "/tmp"])

    def test_main_requires_project_name(self):
        with pytest.raises(SystemExit):
            main(["--project-path", "/tmp", "--instance-dir", "/tmp"])

    def test_main_requires_instance_dir(self):
        with pytest.raises(SystemExit):
            main(["--project-path", "/tmp", "--project-name", "myapp"])
