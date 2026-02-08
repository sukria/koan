"""Tests for mission_runner.py — mission execution pipeline."""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestBuildMissionCommand:
    """Test build_mission_command function."""

    def test_basic_command(self):
        from app.mission_runner import build_mission_command

        cmd = build_mission_command(prompt="Do something")
        assert cmd[0] == "claude"
        assert "-p" in cmd
        assert "Do something" in cmd
        assert "--output-format" in cmd
        assert "json" in cmd

    def test_includes_allowed_tools(self):
        from app.mission_runner import build_mission_command

        cmd = build_mission_command(prompt="test")
        idx = cmd.index("--allowedTools")
        tools = cmd[idx + 1]
        assert "Bash" in tools
        assert "Read" in tools
        assert "Write" in tools
        assert "Edit" in tools

    def test_extra_flags_appended(self):
        from app.mission_runner import build_mission_command

        cmd = build_mission_command(prompt="test", extra_flags="--model opus")
        assert "--model" in cmd
        assert "opus" in cmd

    def test_empty_extra_flags_ignored(self):
        from app.mission_runner import build_mission_command

        cmd = build_mission_command(prompt="test", extra_flags="")
        assert "--model" not in cmd

    def test_whitespace_extra_flags_ignored(self):
        from app.mission_runner import build_mission_command

        cmd = build_mission_command(prompt="test", extra_flags="   ")
        base = build_mission_command(prompt="test")
        assert len(cmd) == len(base)


class TestGetMissionFlags:
    """Test get_mission_flags function."""

    @patch("app.config.get_claude_flags_for_role", return_value="--model opus")
    def test_returns_flags_from_config(self, mock_flags):
        from app.mission_runner import get_mission_flags

        result = get_mission_flags("deep")
        assert result == "--model opus"
        mock_flags.assert_called_once_with("mission", "deep")

    @patch("app.config.get_claude_flags_for_role", return_value="")
    def test_returns_empty_string_when_no_flags(self, mock_flags):
        from app.mission_runner import get_mission_flags

        result = get_mission_flags()
        assert result == ""


class TestParseClaudeOutput:
    """Test parse_claude_output function."""

    def test_extracts_result_key(self):
        from app.mission_runner import parse_claude_output

        raw = json.dumps({"result": "Mission completed"})
        assert parse_claude_output(raw) == "Mission completed"

    def test_extracts_content_key(self):
        from app.mission_runner import parse_claude_output

        raw = json.dumps({"content": "Some content"})
        assert parse_claude_output(raw) == "Some content"

    def test_extracts_text_key(self):
        from app.mission_runner import parse_claude_output

        raw = json.dumps({"text": "Some text"})
        assert parse_claude_output(raw) == "Some text"

    def test_prefers_result_over_content(self):
        from app.mission_runner import parse_claude_output

        raw = json.dumps({"result": "winner", "content": "loser"})
        assert parse_claude_output(raw) == "winner"

    def test_falls_back_to_raw_on_invalid_json(self):
        from app.mission_runner import parse_claude_output

        assert parse_claude_output("not json at all") == "not json at all"

    def test_returns_empty_on_empty_input(self):
        from app.mission_runner import parse_claude_output

        assert parse_claude_output("") == ""
        assert parse_claude_output("   ") == ""

    def test_handles_non_string_values(self):
        from app.mission_runner import parse_claude_output

        raw = json.dumps({"result": 42, "content": ["a", "b"]})
        # Neither is a string, so fallback to raw
        assert parse_claude_output(raw) == raw.strip()

    def test_handles_json_without_known_keys(self):
        from app.mission_runner import parse_claude_output

        raw = json.dumps({"status": "ok", "data": "test"})
        assert parse_claude_output(raw) == raw.strip()


class TestArchivePending:
    """Test archive_pending function."""

    def test_archives_pending_to_journal(self, tmp_path):
        from app.mission_runner import archive_pending

        # Create pending.md
        journal_dir = tmp_path / "journal"
        journal_dir.mkdir()
        pending = journal_dir / "pending.md"
        pending.write_text("# Mission: test\n09:00 — did something\n")

        result = archive_pending(str(tmp_path), "myproject", 5)

        assert result is True
        assert not pending.exists()
        # Check daily journal was created
        import datetime
        today = datetime.date.today().strftime("%Y-%m-%d")
        journal_file = journal_dir / today / "myproject.md"
        assert journal_file.exists()
        content = journal_file.read_text()
        assert "Run 5" in content
        assert "auto-archived from pending" in content
        assert "did something" in content

    def test_returns_false_when_no_pending(self, tmp_path):
        from app.mission_runner import archive_pending

        result = archive_pending(str(tmp_path), "myproject", 1)
        assert result is False

    def test_appends_to_existing_journal(self, tmp_path):
        from app.mission_runner import archive_pending

        import datetime
        today = datetime.date.today().strftime("%Y-%m-%d")
        journal_dir = tmp_path / "journal" / today
        journal_dir.mkdir(parents=True)
        journal_file = journal_dir / "myproject.md"
        journal_file.write_text("# Previous entry\n")

        pending = tmp_path / "journal" / "pending.md"
        pending.write_text("New content\n")

        archive_pending(str(tmp_path), "myproject", 3)

        content = journal_file.read_text()
        assert "Previous entry" in content
        assert "New content" in content


class TestUpdateUsage:
    """Test update_usage function."""

    @patch("app.usage_estimator.cmd_update")
    def test_calls_cmd_update(self, mock_update):
        from app.mission_runner import update_usage

        result = update_usage("/tmp/out.json", "/tmp/state.json", "/tmp/usage.md")
        assert result is True
        mock_update.assert_called_once()

    @patch("app.usage_estimator.cmd_update", side_effect=Exception("fail"))
    def test_returns_false_on_error(self, mock_update):
        from app.mission_runner import update_usage

        result = update_usage("/tmp/out.json", "/tmp/state.json", "/tmp/usage.md")
        assert result is False


class TestTriggerReflection:
    """Test trigger_reflection function."""

    @patch("app.post_mission_reflection.write_to_journal")
    @patch("app.post_mission_reflection.run_reflection", return_value="Deep insight")
    @patch("app.post_mission_reflection.is_significant_mission", return_value=True)
    @patch("app.post_mission_reflection._read_journal_file", return_value="substantial content")
    def test_generates_reflection_for_significant_mission(
        self, mock_read, mock_sig, mock_run, mock_write, tmp_path
    ):
        from app.mission_runner import trigger_reflection

        result = trigger_reflection(str(tmp_path), "audit security", 60, project_name="koan")
        assert result is True
        mock_write.assert_called_once()
        # Verify journal content is passed to run_reflection
        mock_run.assert_called_once()
        call_kwargs_or_args = mock_run.call_args
        assert "substantial content" in str(call_kwargs_or_args)

    @patch("app.post_mission_reflection.is_significant_mission", return_value=False)
    @patch("app.post_mission_reflection._read_journal_file", return_value="")
    def test_skips_insignificant_missions(self, mock_read, mock_sig, tmp_path):
        from app.mission_runner import trigger_reflection

        result = trigger_reflection(str(tmp_path), "small fix", 5, project_name="koan")
        assert result is False

    @patch("app.post_mission_reflection.run_reflection", return_value="")
    @patch("app.post_mission_reflection.is_significant_mission", return_value=True)
    @patch("app.post_mission_reflection._read_journal_file", return_value="content")
    def test_returns_false_when_no_reflection_generated(self, mock_read, mock_sig, mock_run, tmp_path):
        from app.mission_runner import trigger_reflection

        result = trigger_reflection(str(tmp_path), "deep refactor", 60, project_name="koan")
        assert result is False

    @patch("app.post_mission_reflection.write_to_journal")
    @patch("app.post_mission_reflection.run_reflection", return_value="Insight")
    @patch("app.post_mission_reflection.is_significant_mission", return_value=True)
    @patch("app.post_mission_reflection._read_journal_file", return_value="journal text")
    def test_passes_project_name_to_read_journal(
        self, mock_read, mock_sig, mock_run, mock_write, tmp_path
    ):
        from app.mission_runner import trigger_reflection

        trigger_reflection(str(tmp_path), "audit", 60, project_name="myproject")
        mock_read.assert_called_once()
        call_args = mock_read.call_args
        assert call_args[0][1] == "myproject"


class TestCheckAutoMerge:
    """Test check_auto_merge function."""

    @patch("app.git_auto_merge.auto_merge_branch")
    @patch("subprocess.run")
    def test_checks_koan_branch(self, mock_run, mock_merge, tmp_path):
        from app.mission_runner import check_auto_merge

        mock_run.return_value = MagicMock(stdout="koan/my-feature\n")
        result = check_auto_merge(str(tmp_path), "project", str(tmp_path))
        assert result == "koan/my-feature"
        mock_merge.assert_called_once()

    @patch("subprocess.run")
    def test_skips_non_koan_branch(self, mock_run, tmp_path):
        from app.mission_runner import check_auto_merge

        mock_run.return_value = MagicMock(stdout="main\n")
        result = check_auto_merge(str(tmp_path), "project", str(tmp_path))
        assert result is None

    @patch("subprocess.run", side_effect=Exception("git not found"))
    def test_returns_none_on_error(self, mock_run, tmp_path):
        from app.mission_runner import check_auto_merge

        result = check_auto_merge(str(tmp_path), "project", str(tmp_path))
        assert result is None


class TestRunPostMission:
    """Test run_post_mission orchestration function."""

    @patch("app.mission_runner.commit_instance")
    @patch("app.mission_runner.check_auto_merge", return_value=None)
    @patch("app.mission_runner.trigger_reflection", return_value=False)
    @patch("app.mission_runner.archive_pending", return_value=False)
    @patch("app.quota_handler.handle_quota_exhaustion", return_value=None)
    @patch("app.mission_runner.update_usage", return_value=True)
    def test_successful_run(self, mock_usage, mock_quota, mock_archive,
                            mock_reflect, mock_merge, mock_commit, tmp_path):
        from app.mission_runner import run_post_mission

        instance_dir = str(tmp_path / "instance")
        os.makedirs(instance_dir, exist_ok=True)

        result = run_post_mission(
            instance_dir=instance_dir,
            project_name="koan",
            project_path=str(tmp_path),
            run_num=1,
            exit_code=0,
            stdout_file="/tmp/out.json",
            stderr_file="/tmp/err.txt",
        )

        assert result["success"] is True
        assert result["usage_updated"] is True
        assert result["quota_exhausted"] is False

    @patch("app.mission_runner.check_auto_merge", return_value=None)
    @patch("app.mission_runner.trigger_reflection", return_value=False)
    @patch("app.mission_runner.archive_pending", return_value=False)
    @patch("app.quota_handler.handle_quota_exhaustion", return_value=("resets 10am", "Auto-resume in 5h"))
    @patch("app.mission_runner.update_usage", return_value=True)
    def test_quota_exhaustion_early_return(self, mock_usage, mock_quota,
                                           mock_archive, mock_reflect, mock_merge, tmp_path):
        from app.mission_runner import run_post_mission

        instance_dir = str(tmp_path / "instance")
        os.makedirs(instance_dir, exist_ok=True)

        result = run_post_mission(
            instance_dir=instance_dir,
            project_name="koan",
            project_path=str(tmp_path),
            run_num=5,
            exit_code=0,
            stdout_file="/tmp/out.json",
            stderr_file="/tmp/err.txt",
        )

        assert result["quota_exhausted"] is True
        assert result["quota_info"] == ("resets 10am", "Auto-resume in 5h")
        # Should NOT call archive, reflection, or merge
        mock_archive.assert_not_called()
        mock_reflect.assert_not_called()
        mock_merge.assert_not_called()

    @patch("app.mission_runner.check_auto_merge", return_value=None)
    @patch("app.mission_runner.trigger_reflection", return_value=False)
    @patch("app.mission_runner.archive_pending", return_value=True)
    @patch("app.quota_handler.handle_quota_exhaustion", return_value=None)
    @patch("app.mission_runner.update_usage", return_value=True)
    def test_pending_archived(self, mock_usage, mock_quota, mock_archive,
                              mock_reflect, mock_merge, tmp_path):
        from app.mission_runner import run_post_mission

        instance_dir = str(tmp_path / "instance")
        os.makedirs(instance_dir, exist_ok=True)

        result = run_post_mission(
            instance_dir=instance_dir,
            project_name="koan",
            project_path=str(tmp_path),
            run_num=2,
            exit_code=0,
            stdout_file="/tmp/out.json",
            stderr_file="/tmp/err.txt",
        )

        assert result["pending_archived"] is True

    @patch("app.mission_runner.check_auto_merge", return_value=None)
    @patch("app.mission_runner.trigger_reflection", return_value=False)
    @patch("app.mission_runner.archive_pending", return_value=False)
    @patch("app.quota_handler.handle_quota_exhaustion", return_value=None)
    @patch("app.mission_runner.update_usage", return_value=True)
    def test_failed_run_skips_reflection_and_merge(self, mock_usage, mock_quota,
                                                    mock_archive, mock_reflect,
                                                    mock_merge, tmp_path):
        from app.mission_runner import run_post_mission

        instance_dir = str(tmp_path / "instance")
        os.makedirs(instance_dir, exist_ok=True)

        result = run_post_mission(
            instance_dir=instance_dir,
            project_name="koan",
            project_path=str(tmp_path),
            run_num=3,
            exit_code=1,
            stdout_file="/tmp/out.json",
            stderr_file="/tmp/err.txt",
        )

        assert result["success"] is False
        mock_reflect.assert_not_called()
        mock_merge.assert_not_called()

    @patch("app.mission_runner.check_auto_merge", return_value="koan/feature")
    @patch("app.mission_runner.trigger_reflection", return_value=True)
    @patch("app.mission_runner.archive_pending", return_value=False)
    @patch("app.quota_handler.handle_quota_exhaustion", return_value=None)
    @patch("app.mission_runner.update_usage", return_value=True)
    def test_full_pipeline_success(self, mock_usage, mock_quota, mock_archive,
                                    mock_reflect, mock_merge, tmp_path):
        from app.mission_runner import run_post_mission

        instance_dir = str(tmp_path / "instance")
        os.makedirs(instance_dir, exist_ok=True)

        result = run_post_mission(
            instance_dir=instance_dir,
            project_name="koan",
            project_path=str(tmp_path),
            run_num=1,
            exit_code=0,
            stdout_file="/tmp/out.json",
            stderr_file="/tmp/err.txt",
            mission_title="audit security",
            start_time=1000000,
        )

        assert result["success"] is True
        assert result["reflection_written"] is True
        assert result["auto_merge_branch"] == "koan/feature"


class TestCommitInstance:
    """Test commit_instance function."""

    @patch("subprocess.run")
    def test_commits_and_pushes_when_changes(self, mock_run, tmp_path):
        from app.mission_runner import commit_instance

        # First call: git add -A
        # Second call: git diff --cached --quiet (returncode=1 = has changes)
        # Third call: git commit
        # Fourth call: git push
        mock_run.side_effect = [
            MagicMock(),  # git add
            MagicMock(returncode=1),  # git diff (changes)
            MagicMock(),  # git commit
            MagicMock(),  # git push
        ]

        result = commit_instance(str(tmp_path))
        assert result is True
        assert mock_run.call_count == 4

    @patch("subprocess.run")
    def test_returns_false_when_no_changes(self, mock_run, tmp_path):
        from app.mission_runner import commit_instance

        mock_run.side_effect = [
            MagicMock(),  # git add
            MagicMock(returncode=0),  # git diff (no changes)
        ]

        result = commit_instance(str(tmp_path))
        assert result is False
        assert mock_run.call_count == 2


class TestCLIParseOutput:
    """Test parse-output CLI subcommand."""

    def test_cli_parse_output(self, tmp_path):
        from app.mission_runner import _cli_parse_output

        json_file = tmp_path / "output.json"
        json_file.write_text(json.dumps({"result": "Hello world"}))

        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f):
            _cli_parse_output([str(json_file)])
        assert f.getvalue().strip() == "Hello world"

    def test_cli_parse_output_missing_file(self):
        from app.mission_runner import _cli_parse_output

        with pytest.raises(SystemExit) as exc_info:
            _cli_parse_output(["/nonexistent/file.json"])
        assert exc_info.value.code == 1

    def test_cli_parse_output_no_args(self):
        from app.mission_runner import _cli_parse_output

        with pytest.raises(SystemExit) as exc_info:
            _cli_parse_output([])
        assert exc_info.value.code == 1


class TestCLIPostMission:
    """Test post-mission CLI subcommand."""

    @patch("app.mission_runner.run_post_mission")
    def test_cli_post_mission_success(self, mock_run, tmp_path):
        from app.mission_runner import _cli_post_mission

        mock_run.return_value = {
            "success": True,
            "usage_updated": True,
            "pending_archived": False,
            "reflection_written": False,
            "auto_merge_branch": None,
            "quota_exhausted": False,
            "quota_info": None,
        }

        with pytest.raises(SystemExit) as exc_info:
            _cli_post_mission([
                "--instance", str(tmp_path),
                "--project-name", "koan",
                "--project-path", str(tmp_path),
                "--run-num", "1",
                "--exit-code", "0",
                "--stdout-file", "/tmp/out",
                "--stderr-file", "/tmp/err",
            ])
        assert exc_info.value.code == 0

    @patch("app.mission_runner.run_post_mission")
    def test_cli_post_mission_quota_exhausted(self, mock_run, tmp_path):
        from app.mission_runner import _cli_post_mission

        mock_run.return_value = {
            "success": True,
            "usage_updated": True,
            "pending_archived": False,
            "reflection_written": False,
            "auto_merge_branch": None,
            "quota_exhausted": True,
            "quota_info": ("resets 10am", "Auto-resume at 10am"),
        }

        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with pytest.raises(SystemExit) as exc_info:
            with redirect_stdout(f):
                _cli_post_mission([
                    "--instance", str(tmp_path),
                    "--project-name", "koan",
                    "--project-path", str(tmp_path),
                    "--run-num", "5",
                    "--exit-code", "0",
                    "--stdout-file", "/tmp/out",
                    "--stderr-file", "/tmp/err",
                ])
        assert exc_info.value.code == 2
        assert "QUOTA_EXHAUSTED" in f.getvalue()

    @patch("app.mission_runner.run_post_mission")
    def test_cli_post_mission_failure(self, mock_run, tmp_path):
        from app.mission_runner import _cli_post_mission

        mock_run.return_value = {
            "success": False,
            "usage_updated": True,
            "pending_archived": False,
            "reflection_written": False,
            "auto_merge_branch": None,
            "quota_exhausted": False,
            "quota_info": None,
        }

        with pytest.raises(SystemExit) as exc_info:
            _cli_post_mission([
                "--instance", str(tmp_path),
                "--project-name", "koan",
                "--project-path", str(tmp_path),
                "--run-num", "3",
                "--exit-code", "1",
                "--stdout-file", "/tmp/out",
                "--stderr-file", "/tmp/err",
            ])
        assert exc_info.value.code == 1


class TestCLIMain:
    """Test main CLI entry point."""

    def test_unknown_subcommand(self):
        from app.mission_runner import main

        with patch.object(sys, "argv", ["mission_runner.py", "unknown"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_no_args(self):
        from app.mission_runner import main

        with patch.object(sys, "argv", ["mission_runner.py"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1


class TestRunShIntegration:
    """Test that run.sh correctly references mission_runner.py."""

    def test_run_sh_calls_parse_output(self):
        """run.sh should use mission_runner parse-output instead of jq."""
        run_sh = Path(__file__).parent.parent / "run.sh"
        content = run_sh.read_text()
        assert "app.mission_runner parse-output" in content
        # jq should NOT be used for output parsing anymore
        assert "jq -r '.result" not in content

    def test_run_sh_calls_post_mission(self):
        """run.sh should use mission_runner post-mission."""
        run_sh = Path(__file__).parent.parent / "run.sh"
        content = run_sh.read_text()
        assert "app.mission_runner post-mission" in content

    def test_run_sh_no_direct_usage_estimator_update(self):
        """run.sh should not call usage_estimator update/refresh directly —
        iteration_manager handles refresh, mission_runner handles update.
        reset-time is allowed (computes pause timestamp for wait mode)."""
        run_sh = Path(__file__).parent.parent / "run.sh"
        content = run_sh.read_text()
        lines = [l for l in content.splitlines()
                 if "usage_estimator" in l and "reset-time" not in l]
        assert len(lines) == 0, f"Direct usage_estimator calls remain: {lines}"

    def test_run_sh_no_dead_mission_summary_var(self):
        """MISSION_SUMMARY variable should be removed (dead code)."""
        run_sh = Path(__file__).parent.parent / "run.sh"
        content = run_sh.read_text()
        assert "MISSION_SUMMARY" not in content
