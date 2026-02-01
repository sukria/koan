"""Tests for git_sync.py — git awareness module."""

import subprocess
from datetime import date
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.git_sync import (
    run_git,
    get_koan_branches,
    get_recent_main_commits,
    get_merged_branches,
    get_unmerged_branches,
    build_sync_report,
    write_sync_to_journal,
)


class TestRunGit:
    def test_returns_stdout(self, tmp_path):
        """run_git returns stdout of successful command."""
        result = run_git(str(tmp_path), "version")
        assert "git version" in result

    def test_returns_empty_on_failure(self, tmp_path):
        """run_git returns empty string on non-existent command."""
        result = run_git(str(tmp_path), "nonexistent-command-xyz")
        assert result == ""

    def test_returns_empty_on_timeout(self):
        """run_git returns empty on timeout."""
        with patch("app.git_sync.subprocess.run", side_effect=subprocess.TimeoutExpired("git", 10)):
            assert run_git("/tmp", "status") == ""


class TestGetKoanBranches:
    def test_parses_local_and_remote(self):
        """Extracts koan/* branches from mixed branch listing."""
        mock_output = (
            "  koan/fix-bug\n"
            "* koan/current\n"
            "  remotes/origin/koan/fix-bug\n"
            "  remotes/origin/koan/other\n"
            "  main\n"
        )
        with patch("app.git_sync.run_git", return_value=mock_output):
            branches = get_koan_branches("/fake")
        assert "koan/fix-bug" in branches
        assert "koan/current" in branches
        assert "koan/other" in branches
        # No duplicates
        assert len([b for b in branches if b == "koan/fix-bug"]) == 1

    def test_empty_output(self):
        with patch("app.git_sync.run_git", return_value=""):
            assert get_koan_branches("/fake") == []


class TestGetMergedBranches:
    def test_parses_merged(self):
        mock_output = "  remotes/origin/koan/done-feature\n  remotes/origin/koan/old-fix\n"
        with patch("app.git_sync.run_git", return_value=mock_output):
            merged = get_merged_branches("/fake")
        assert "koan/done-feature" in merged
        assert "koan/old-fix" in merged


class TestGetUnmergedBranches:
    def test_parses_unmerged(self):
        mock_output = "  koan/wip\n  remotes/origin/koan/pending-review\n"
        with patch("app.git_sync.run_git", return_value=mock_output):
            unmerged = get_unmerged_branches("/fake")
        assert "koan/wip" in unmerged
        assert "koan/pending-review" in unmerged


class TestGetRecentMainCommits:
    def test_parses_commits(self):
        mock_output = "abc1234 fix: something\ndef5678 feat: other thing\n"
        with patch("app.git_sync.run_git", return_value=mock_output):
            commits = get_recent_main_commits("/fake")
        assert len(commits) == 2
        assert "abc1234 fix: something" in commits[0]

    def test_empty(self):
        with patch("app.git_sync.run_git", return_value=""):
            assert get_recent_main_commits("/fake") == []


class TestBuildSyncReport:
    def test_report_includes_merged_and_unmerged(self):
        with patch("app.git_sync.run_git") as mock_git:
            # fetch returns nothing, branch commands return data
            def side_effect(cwd, *args):
                args_str = " ".join(args)
                if "fetch" in args_str:
                    return ""
                if "--merged" in args_str:
                    return "  remotes/origin/koan/merged-one\n"
                if "--no-merged" in args_str:
                    return "  remotes/origin/koan/pending-one\n"
                if "log" in args_str:
                    return "abc123 some commit\n"
                return ""

            mock_git.side_effect = side_effect
            report = build_sync_report("/fake")

        assert "koan/merged-one" in report
        assert "koan/pending-one" in report
        assert "abc123" in report
        assert "Git sync" in report

    def test_report_no_changes(self):
        with patch("app.git_sync.run_git", return_value=""):
            report = build_sync_report("/fake")
        assert "No notable changes" in report


class TestWriteSyncToJournal:
    def test_creates_journal_entry(self, tmp_path):
        """Writes sync report to journal file."""
        instance = tmp_path / "instance"
        instance.mkdir()
        (instance / "journal").mkdir()

        write_sync_to_journal(str(instance), "koan", "Test sync report")

        today = date.today().strftime("%Y-%m-%d")
        journal_file = instance / "journal" / today / "koan.md"
        assert journal_file.exists()
        content = journal_file.read_text()
        assert "Git Sync" in content
        assert "Test sync report" in content

    def test_appends_to_existing(self, tmp_path):
        """Appends to existing journal file, doesn't overwrite."""
        instance = tmp_path / "instance"
        instance.mkdir()
        today = date.today().strftime("%Y-%m-%d")
        journal_dir = instance / "journal" / today
        journal_dir.mkdir(parents=True)
        journal_file = journal_dir / "koan.md"
        journal_file.write_text("## Previous Entry\n\nSome work.\n")

        write_sync_to_journal(str(instance), "koan", "New sync")

        content = journal_file.read_text()
        assert "Previous Entry" in content
        assert "New sync" in content
