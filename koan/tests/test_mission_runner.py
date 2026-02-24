"""Tests for mission_runner.py — mission execution pipeline."""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


class TestBuildMissionCommand:
    """Test build_mission_command function."""

    @patch("app.cli_provider.get_provider_name", return_value="claude")
    def test_basic_command(self, mock_provider):
        from app.mission_runner import build_mission_command

        cmd = build_mission_command(prompt="Do something")
        # Provider-agnostic: check for prompt and output format, not specific binary
        assert "-p" in cmd or any("Do something" in arg for arg in cmd)
        assert "--output-format" in cmd or any("json" in arg for arg in cmd)

    @patch("app.cli_provider.get_provider_name", return_value="claude")
    def test_includes_allowed_tools(self, mock_provider):
        from app.mission_runner import build_mission_command

        cmd = build_mission_command(prompt="test")
        # Tools should be present in the command (format depends on provider)
        cmd_str = " ".join(cmd)
        # Either Claude format (--allowedTools Read,Write,...) or converted to provider format
        assert any(tool in cmd_str for tool in ["Bash", "Read", "Write", "Edit"])

    @patch("app.cli_provider.get_provider_name", return_value="claude")
    def test_extra_flags_appended(self, mock_provider):
        from app.mission_runner import build_mission_command

        cmd = build_mission_command(prompt="test", extra_flags="--model opus")
        assert "--model" in cmd
        assert "opus" in cmd

    @patch("app.cli_provider.get_provider_name", return_value="claude")
    def test_empty_extra_flags_ignored(self, mock_provider):
        from app.mission_runner import build_mission_command

        cmd = build_mission_command(prompt="test", extra_flags="")
        base = build_mission_command(prompt="test")
        assert len(cmd) == len(base)

    @patch("app.cli_provider.get_provider_name", return_value="claude")
    def test_whitespace_extra_flags_ignored(self, mock_provider):
        from app.mission_runner import build_mission_command

        cmd = build_mission_command(prompt="test", extra_flags="   ")
        base = build_mission_command(prompt="test")
        assert len(cmd) == len(base)

    @patch.dict("os.environ", {"KOAN_CLI_PROVIDER": "copilot"})
    def test_copilot_provider(self):
        # Reset cached provider to pick up env var
        from app.cli_provider import reset_provider
        reset_provider()

        from app.mission_runner import build_mission_command

        cmd = build_mission_command(prompt="test")
        # When copilot is configured, should use gh copilot
        assert "gh" in cmd or "copilot" in cmd[0]

        # Clean up
        reset_provider()

    @patch("app.cli_provider.get_provider_name", return_value="claude")
    def test_plugin_dirs_forwarded(self, mock_provider):
        from app.mission_runner import build_mission_command

        cmd = build_mission_command(
            prompt="test",
            plugin_dirs=["/tmp/koan-plugins"],
        )
        assert "--plugin-dir" in cmd
        idx = cmd.index("--plugin-dir")
        assert cmd[idx + 1] == "/tmp/koan-plugins"

    @patch("app.cli_provider.get_provider_name", return_value="claude")
    def test_plugin_dirs_none_excluded(self, mock_provider):
        from app.mission_runner import build_mission_command

        cmd = build_mission_command(prompt="test")
        assert "--plugin-dir" not in cmd


class TestGetMissionFlags:
    """Test get_mission_flags function."""

    @patch("app.config.get_claude_flags_for_role", return_value="--model opus")
    def test_returns_flags_from_config(self, mock_flags):
        from app.mission_runner import get_mission_flags

        result = get_mission_flags("deep")
        assert result == "--model opus"
        mock_flags.assert_called_once_with("mission", "deep", "")

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

    @patch("app.journal.append_to_journal")
    def test_uses_append_to_journal_for_locking(self, mock_append, tmp_path):
        """Verify archive_pending uses append_to_journal (which has file locking)."""
        from app.mission_runner import archive_pending

        journal_dir = tmp_path / "journal"
        journal_dir.mkdir()
        pending = journal_dir / "pending.md"
        pending.write_text("test content\n")

        archive_pending(str(tmp_path), "myproject", 1)

        mock_append.assert_called_once()
        args = mock_append.call_args
        assert args[0][1] == "myproject"  # project_name
        assert "test content" in args[0][2]  # content


    def test_handles_file_deleted_between_check_and_read(self, tmp_path):
        """TOCTOU: pending.md disappears between exists() and read_text()."""
        from app.mission_runner import archive_pending

        journal_dir = tmp_path / "journal"
        journal_dir.mkdir()
        pending = journal_dir / "pending.md"
        pending.write_text("content")

        # Patch read_text to simulate file disappearing after exists() returns True
        original_read = Path.read_text

        def disappearing_read(self, *args, **kwargs):
            if self.name == "pending.md":
                raise FileNotFoundError("No such file")
            return original_read(self, *args, **kwargs)

        with patch.object(Path, "read_text", disappearing_read):
            result = archive_pending(str(tmp_path), "myproject", 1)

        assert result is False

    def test_handles_file_deleted_between_read_and_unlink(self, tmp_path):
        """pending.md disappears between read_text() and unlink() — missing_ok."""
        from app.mission_runner import archive_pending

        journal_dir = tmp_path / "journal"
        journal_dir.mkdir()
        pending = journal_dir / "pending.md"
        pending.write_text("content to archive\n")

        # Patch unlink to simulate file already deleted
        original_unlink = Path.unlink

        def disappearing_unlink(self, *args, **kwargs):
            if self.name == "pending.md":
                # Delete first, then call with missing_ok (should not raise)
                if self.exists():
                    original_unlink(self, missing_ok=True)
                # Simulate the file being gone — call again to test missing_ok
                original_unlink(self, missing_ok=True)
                return
            return original_unlink(self, *args, **kwargs)

        with patch.object(Path, "unlink", disappearing_unlink):
            result = archive_pending(str(tmp_path), "myproject", 2)

        # Should succeed — content was read before the race
        assert result is True


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
    @patch("app.git_sync.run_git", return_value="koan/my-feature")
    @patch("app.config.get_branch_prefix", return_value="koan/")
    def test_checks_koan_branch(self, mock_prefix, mock_git, mock_merge, tmp_path):
        from app.mission_runner import check_auto_merge

        result = check_auto_merge(str(tmp_path), "project", str(tmp_path))
        assert result == "koan/my-feature"
        mock_merge.assert_called_once()

    @patch("app.git_sync.run_git", return_value="main")
    def test_skips_non_koan_branch(self, mock_git, tmp_path):
        from app.mission_runner import check_auto_merge

        result = check_auto_merge(str(tmp_path), "project", str(tmp_path))
        assert result is None

    @patch("app.git_sync.run_git", return_value="")
    def test_returns_none_on_empty_branch(self, mock_git, tmp_path):
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

    @patch("app.git_sync.run_git")
    def test_commits_and_pushes_when_changes(self, mock_git, tmp_path):
        from app.mission_runner import commit_instance

        # run_git calls: add -A, diff --cached --name-only, commit, rev-parse, push
        mock_git.side_effect = [
            "",                    # git add -A (no output)
            "file1.md\nfile2.md",  # git diff --cached --name-only (has changes)
            "",                    # git commit
            "main",                # git rev-parse --abbrev-ref HEAD
            "",                    # git push origin main
        ]

        result = commit_instance(str(tmp_path))
        assert result is True
        assert mock_git.call_count == 5

    @patch("app.git_sync.run_git")
    def test_returns_false_when_no_changes(self, mock_git, tmp_path):
        from app.mission_runner import commit_instance

        mock_git.side_effect = [
            "",   # git add -A
            "",   # git diff --cached --name-only (no changes → empty string)
        ]

        result = commit_instance(str(tmp_path))
        assert result is False
        assert mock_git.call_count == 2


    @patch("app.git_sync.run_git")
    def test_pushes_to_current_branch_not_hardcoded_main(self, mock_git, tmp_path):
        """commit_instance should detect and push to the current branch."""
        from app.mission_runner import commit_instance

        # run_git calls: add -A, diff, commit, rev-parse, push
        mock_git.side_effect = [
            "",                    # git add -A
            "file1.md",            # git diff --cached --name-only
            "",                    # git commit
            "develop",             # git rev-parse --abbrev-ref HEAD
            "",                    # git push origin develop
        ]

        result = commit_instance(str(tmp_path))
        assert result is True
        assert mock_git.call_count == 5
        # Verify push uses detected branch, not "main"
        push_call = mock_git.call_args_list[4]
        assert push_call[0] == (str(tmp_path), "push", "origin", "develop")

    @patch("app.git_sync.run_git")
    def test_push_falls_back_to_main_on_empty_branch(self, mock_git, tmp_path):
        """If rev-parse returns empty, push to 'main' as fallback."""
        from app.mission_runner import commit_instance

        mock_git.side_effect = [
            "",          # git add -A
            "changes",   # git diff --cached --name-only
            "",          # git commit
            "",          # git rev-parse --abbrev-ref HEAD (empty)
            "",          # git push origin main
        ]

        result = commit_instance(str(tmp_path))
        assert result is True
        push_call = mock_git.call_args_list[4]
        assert push_call[0] == (str(tmp_path), "push", "origin", "main")

    @patch("app.git_sync.run_git")
    def test_returns_false_on_push_failure(self, mock_git, tmp_path):
        """If git push raises, commit_instance returns False gracefully."""
        from app.mission_runner import commit_instance

        mock_git.side_effect = [
            "",          # git add -A
            "changes",   # git diff --cached --name-only
            "",          # git commit
            "main",      # git rev-parse --abbrev-ref HEAD
            Exception("network error"),  # git push fails
        ]

        result = commit_instance(str(tmp_path))
        assert result is False

    @patch("app.git_sync.run_git")
    def test_returns_false_on_commit_failure(self, mock_git, tmp_path):
        """If git commit raises, commit_instance returns False gracefully."""
        from app.mission_runner import commit_instance

        mock_git.side_effect = [
            "",          # git add -A
            "changes",   # git diff --cached --name-only
            Exception("commit failed"),  # git commit raises
        ]

        result = commit_instance(str(tmp_path))
        assert result is False


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


class TestReadPendingContent:
    """Test _read_pending_content private helper."""

    def test_reads_existing_pending_file(self, tmp_path):
        from app.mission_runner import _read_pending_content

        journal_dir = tmp_path / "journal"
        journal_dir.mkdir()
        pending = journal_dir / "pending.md"
        pending.write_text("# Mission: test\n09:00 — started\n")

        content = _read_pending_content(str(tmp_path))
        assert "Mission: test" in content
        assert "09:00" in content

    def test_returns_empty_when_no_pending(self, tmp_path):
        from app.mission_runner import _read_pending_content

        # No journal dir at all
        content = _read_pending_content(str(tmp_path))
        assert content == ""

    def test_returns_empty_when_journal_exists_but_no_pending(self, tmp_path):
        from app.mission_runner import _read_pending_content

        (tmp_path / "journal").mkdir()
        content = _read_pending_content(str(tmp_path))
        assert content == ""

    def test_returns_empty_on_os_error(self, tmp_path):
        from app.mission_runner import _read_pending_content

        journal_dir = tmp_path / "journal"
        journal_dir.mkdir()
        pending = journal_dir / "pending.md"
        pending.write_text("content")

        # Simulate OSError during read
        with patch.object(Path, "read_text", side_effect=OSError("Permission denied")):
            content = _read_pending_content(str(tmp_path))
        assert content == ""


class TestRecordSessionOutcome:
    """Test _record_session_outcome fire-and-forget helper."""

    @patch("app.session_tracker.record_outcome")
    def test_calls_record_outcome(self, mock_record, tmp_path):
        from app.mission_runner import _record_session_outcome

        _record_session_outcome(
            str(tmp_path), "koan", "implement", 15, "journal content"
        )
        mock_record.assert_called_once_with(
            instance_dir=str(tmp_path),
            project="koan",
            mode="implement",
            duration_minutes=15,
            journal_content="journal content",
        )

    @patch("app.session_tracker.record_outcome")
    def test_uses_unknown_mode_when_empty(self, mock_record, tmp_path):
        from app.mission_runner import _record_session_outcome

        _record_session_outcome(str(tmp_path), "koan", "", 5, "")
        mock_record.assert_called_once()
        assert mock_record.call_args.kwargs["mode"] == "unknown"

    @patch("app.session_tracker.record_outcome", side_effect=Exception("db error"))
    def test_silently_catches_exceptions(self, mock_record, tmp_path, capsys):
        from app.mission_runner import _record_session_outcome

        # Should not raise
        _record_session_outcome(str(tmp_path), "koan", "deep", 30, "text")
        captured = capsys.readouterr()
        assert "Session outcome recording failed" in captured.err


class TestRunPostMissionDuration:
    """Test duration computation in run_post_mission."""

    @patch("app.mission_runner._record_session_outcome")
    @patch("app.mission_runner.check_auto_merge", return_value=None)
    @patch("app.mission_runner.trigger_reflection", return_value=False)
    @patch("app.mission_runner.archive_pending", return_value=False)
    @patch("app.mission_runner._read_pending_content", return_value="")
    @patch("app.quota_handler.handle_quota_exhaustion", return_value=None)
    @patch("app.mission_runner.update_usage", return_value=True)
    def test_duration_computed_from_start_time(
        self, mock_usage, mock_quota, mock_read_pending, mock_archive,
        mock_reflect, mock_merge, mock_record, tmp_path
    ):
        from app.mission_runner import run_post_mission
        import time

        instance_dir = str(tmp_path / "instance")
        os.makedirs(instance_dir, exist_ok=True)
        start = int(time.time()) - 600  # 10 minutes ago

        run_post_mission(
            instance_dir=instance_dir,
            project_name="koan",
            project_path=str(tmp_path),
            run_num=1,
            exit_code=0,
            stdout_file="/tmp/out.json",
            stderr_file="/tmp/err.txt",
            start_time=start,
        )

        # trigger_reflection should receive duration ~10 minutes
        mock_reflect.assert_called_once()
        call_args = mock_reflect.call_args
        duration = call_args[1].get("duration_minutes") if "duration_minutes" in (call_args[1] or {}) else call_args[0][2]
        assert 9 <= duration <= 11

    @patch("app.mission_runner._record_session_outcome")
    @patch("app.mission_runner.check_auto_merge", return_value=None)
    @patch("app.mission_runner.trigger_reflection", return_value=False)
    @patch("app.mission_runner.archive_pending", return_value=False)
    @patch("app.mission_runner._read_pending_content", return_value="")
    @patch("app.quota_handler.handle_quota_exhaustion", return_value=None)
    @patch("app.mission_runner.update_usage", return_value=True)
    def test_zero_start_time_gives_zero_duration(
        self, mock_usage, mock_quota, mock_read_pending, mock_archive,
        mock_reflect, mock_merge, mock_record, tmp_path
    ):
        from app.mission_runner import run_post_mission

        instance_dir = str(tmp_path / "instance")
        os.makedirs(instance_dir, exist_ok=True)

        run_post_mission(
            instance_dir=instance_dir,
            project_name="koan",
            project_path=str(tmp_path),
            run_num=1,
            exit_code=0,
            stdout_file="/tmp/out.json",
            stderr_file="/tmp/err.txt",
            start_time=0,
        )

        mock_reflect.assert_called_once()
        call_args = mock_reflect.call_args
        duration = call_args[0][2]  # positional arg
        assert duration == 0

    @patch("app.mission_runner._record_session_outcome")
    @patch("app.mission_runner.check_auto_merge", return_value=None)
    @patch("app.mission_runner.trigger_reflection", return_value=False)
    @patch("app.mission_runner.archive_pending", return_value=False)
    @patch("app.mission_runner._read_pending_content", return_value="")
    @patch("app.quota_handler.handle_quota_exhaustion", return_value=None)
    @patch("app.mission_runner.update_usage", return_value=True)
    def test_autonomous_mode_fallback_mission_text(
        self, mock_usage, mock_quota, mock_read_pending, mock_archive,
        mock_reflect, mock_merge, mock_record, tmp_path
    ):
        """When no mission_title, reflection uses 'Autonomous X on Y' text."""
        from app.mission_runner import run_post_mission

        instance_dir = str(tmp_path / "instance")
        os.makedirs(instance_dir, exist_ok=True)

        run_post_mission(
            instance_dir=instance_dir,
            project_name="koan",
            project_path=str(tmp_path),
            run_num=1,
            exit_code=0,
            stdout_file="/tmp/out.json",
            stderr_file="/tmp/err.txt",
            autonomous_mode="deep",
        )

        mock_reflect.assert_called_once()
        mission_text = mock_reflect.call_args[0][1]
        assert "Autonomous deep on koan" in mission_text

    @patch("app.mission_runner._record_session_outcome")
    @patch("app.mission_runner.check_auto_merge", return_value=None)
    @patch("app.mission_runner.trigger_reflection", return_value=False)
    @patch("app.mission_runner.archive_pending", return_value=False)
    @patch("app.mission_runner._read_pending_content", return_value="pending content here")
    @patch("app.quota_handler.handle_quota_exhaustion", return_value=None)
    @patch("app.mission_runner.update_usage", return_value=True)
    def test_pending_content_passed_to_record_outcome(
        self, mock_usage, mock_quota, mock_read_pending, mock_archive,
        mock_reflect, mock_merge, mock_record, tmp_path
    ):
        """Pending content is read before archival and passed to _record_session_outcome."""
        from app.mission_runner import run_post_mission

        instance_dir = str(tmp_path / "instance")
        os.makedirs(instance_dir, exist_ok=True)

        run_post_mission(
            instance_dir=instance_dir,
            project_name="koan",
            project_path=str(tmp_path),
            run_num=1,
            exit_code=0,
            stdout_file="/tmp/out.json",
            stderr_file="/tmp/err.txt",
        )

        mock_record.assert_called_once()
        # _record_session_outcome is called with positional args
        call_args = mock_record.call_args[0]
        assert call_args[4] == "pending content here"  # journal_content is 5th positional arg


class TestCheckAutoMergeErrors:
    """Test check_auto_merge error handling."""

    @patch("app.git_auto_merge.auto_merge_branch", side_effect=Exception("merge conflict"))
    @patch("app.git_sync.run_git", return_value="koan/feature")
    @patch("app.config.get_branch_prefix", return_value="koan/")
    def test_returns_none_on_merge_error(self, mock_prefix, mock_git, mock_merge, tmp_path, capsys):
        from app.mission_runner import check_auto_merge

        result = check_auto_merge(str(tmp_path), "koan", str(tmp_path))
        assert result is None
        captured = capsys.readouterr()
        assert "Auto-merge check failed" in captured.err

    @patch("app.git_sync.run_git", side_effect=Exception("git error"))
    def test_returns_none_on_git_error(self, mock_git, tmp_path, capsys):
        from app.mission_runner import check_auto_merge

        result = check_auto_merge(str(tmp_path), "koan", str(tmp_path))
        assert result is None
        captured = capsys.readouterr()
        assert "Auto-merge check failed" in captured.err


class TestTriggerReflectionErrors:
    """Test trigger_reflection error handling."""

    @patch("app.post_mission_reflection._read_journal_file", side_effect=Exception("IO error"))
    def test_returns_false_on_exception(self, mock_read, tmp_path, capsys):
        from app.mission_runner import trigger_reflection

        result = trigger_reflection(str(tmp_path), "audit", 60, project_name="koan")
        assert result is False
        captured = capsys.readouterr()
        assert "Reflection failed" in captured.err


class TestParseClaudeOutputEdgeCases:
    """Additional edge cases for parse_claude_output."""

    def test_json_null_value(self):
        from app.mission_runner import parse_claude_output

        raw = json.dumps({"result": None, "content": None})
        # None is not str, so falls back to raw
        assert parse_claude_output(raw) == raw.strip()

    def test_json_with_nested_content(self):
        from app.mission_runner import parse_claude_output

        raw = json.dumps({"result": {"inner": "data"}, "text": "plain"})
        # result is dict (not str), text is str → should return "plain"
        assert parse_claude_output(raw) == "plain"

    def test_json_with_empty_string_result(self):
        from app.mission_runner import parse_claude_output

        raw = json.dumps({"result": "", "content": "fallback"})
        # Empty string IS a string, so it's returned (truthy check not done on value)
        assert parse_claude_output(raw) == ""

    def test_whitespace_only_json(self):
        from app.mission_runner import parse_claude_output

        assert parse_claude_output("  \n\t  ") == ""


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


