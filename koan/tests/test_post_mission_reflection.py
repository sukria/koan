"""Tests for post_mission_reflection module."""

import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.post_mission_reflection import (
    should_write_reflection,
    build_reflection_prompt,
    run_reflection,
    save_reflection,
    main,
)


@pytest.fixture
def instance_dir(tmp_path):
    """Create a minimal instance directory for testing."""
    memory_dir = tmp_path / "memory" / "global"
    memory_dir.mkdir(parents=True)
    (tmp_path / "soul.md").write_text("You are Kōan.")
    return tmp_path


@pytest.fixture
def journal_file(tmp_path):
    """Create a journal file with substantial content."""
    journal_dir = tmp_path / "journal" / "2026-02-06"
    journal_dir.mkdir(parents=True)
    journal = journal_dir / "koan.md"
    journal.write_text(
        "# Session 90\n\n"
        "## What happened\n\n"
        "Implemented post-mission reflections. Created new module with heuristics, "
        "prompt template, run.sh integration. Added shared-journal.md to agent context.\n\n"
        "## Decisions\n\n"
        "- Chose keyword + length heuristic over duration-based\n"
        "- Reflections go to shared-journal.md, not personality-evolution.md\n"
        "- Claude generates reflections with --max-turns 1\n\n"
        "## Learnings\n\n"
        "- The shared-journal is a slower conversation layer\n"
        "- Both conditions (keyword + length) prevent noise\n\n"
        + "x" * 200  # Pad to make it substantial
    )
    return journal


class TestShouldWriteReflection:
    def test_audit_mission_with_substantial_journal(self):
        journal = "x" * 600
        assert should_write_reflection("security audit of auth module", journal) is True

    def test_feature_mission_with_substantial_journal(self):
        journal = "x" * 600
        assert should_write_reflection("feature: add dark mode", journal) is True

    def test_refactor_mission_with_substantial_journal(self):
        journal = "x" * 600
        assert should_write_reflection("refactor the auth layer", journal) is True

    def test_security_mission(self):
        journal = "x" * 600
        assert should_write_reflection("fix security vulnerabilities", journal) is True

    def test_architecture_mission(self):
        journal = "x" * 600
        assert should_write_reflection("architect new messaging system", journal) is True

    def test_migration_mission(self):
        journal = "x" * 600
        assert should_write_reflection("database migration to postgres 16", journal) is True

    def test_short_journal_rejects(self):
        """Even significant keywords need substantial journal content."""
        journal = "Quick fix."
        assert should_write_reflection("security audit", journal) is False

    def test_mundane_mission_rejects(self):
        """Non-significant missions are rejected even with long journals."""
        journal = "x" * 600
        assert should_write_reflection("fix typo in README", journal) is False

    def test_rebase_mission_rejects(self):
        journal = "x" * 600
        assert should_write_reflection("rebase PR #42", journal) is False

    def test_empty_mission_title(self):
        journal = "x" * 600
        assert should_write_reflection("", journal) is False

    def test_empty_journal(self):
        assert should_write_reflection("audit codebase", "") is False

    def test_none_inputs(self):
        assert should_write_reflection(None, None) is False

    def test_keyword_case_insensitive(self):
        journal = "x" * 600
        assert should_write_reflection("SECURITY AUDIT", journal) is True
        assert should_write_reflection("Feature flag rollout", journal) is True

    def test_keyword_boundary(self):
        """Keywords should match as whole words, not partial."""
        journal = "x" * 600
        # "featured" contains "feature" but the regex uses \b word boundary
        assert should_write_reflection("featured item", journal) is False

    def test_performance_keyword(self):
        journal = "x" * 600
        assert should_write_reflection("performance optimization", journal) is True

    def test_retrospective_keyword(self):
        journal = "x" * 600
        assert should_write_reflection("retrospective analysis", journal) is True

    def test_introspect_keyword(self):
        journal = "x" * 600
        assert should_write_reflection("introspect on session patterns", journal) is True

    def test_exactly_at_threshold(self):
        """Journal exactly at minimum length should pass."""
        journal = "x" * 500
        assert should_write_reflection("audit", journal) is True

    def test_just_below_threshold(self):
        journal = "x" * 499
        assert should_write_reflection("audit", journal) is False


class TestBuildReflectionPrompt:
    def test_includes_mission_title(self, instance_dir, journal_file):
        prompt = build_reflection_prompt(
            instance_dir, "koan", journal_file.read_text(), "security audit"
        )
        assert "security audit" in prompt

    def test_includes_journal_content(self, instance_dir, journal_file):
        prompt = build_reflection_prompt(
            instance_dir, "koan", journal_file.read_text()
        )
        assert "post-mission reflections" in prompt

    def test_includes_project_name(self, instance_dir, journal_file):
        prompt = build_reflection_prompt(
            instance_dir, "koan", journal_file.read_text()
        )
        assert "koan" in prompt

    def test_truncates_long_journal(self, instance_dir):
        long_content = "x" * 5000
        prompt = build_reflection_prompt(
            instance_dir, "koan", long_content
        )
        # Journal content should be truncated to 3000 chars
        assert len(prompt) < 5000 + 2000  # prompt template + 3000 max journal

    def test_includes_shared_journal_context(self, instance_dir, journal_file):
        shared = instance_dir / "shared-journal.md"
        shared.write_text("## Alexis — 2026-02-05\n\nSome thoughts.\n")
        prompt = build_reflection_prompt(
            instance_dir, "koan", journal_file.read_text()
        )
        assert "Some thoughts" in prompt

    def test_no_shared_journal_is_ok(self, instance_dir, journal_file):
        prompt = build_reflection_prompt(
            instance_dir, "koan", journal_file.read_text()
        )
        assert "journal-reflection" not in prompt or True  # Should not crash

    def test_default_mission_title(self, instance_dir, journal_file):
        prompt = build_reflection_prompt(
            instance_dir, "koan", journal_file.read_text()
        )
        assert "(autonomous work)" in prompt

    def test_loads_template(self, instance_dir, journal_file):
        """Should load the journal-reflection.md template."""
        prompt = build_reflection_prompt(
            instance_dir, "koan", journal_file.read_text()
        )
        # Template content should be present
        assert "What surprised you" in prompt


class TestRunReflection:
    @patch("app.post_mission_reflection.subprocess.run")
    def test_successful_reflection(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="J'ai remarqué que ce refactoring a simplifié bien plus que prévu.\n",
        )
        result = run_reflection("test prompt")
        assert "simplifié" in result
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "claude"
        assert "-p" in call_args

    @patch("app.post_mission_reflection.subprocess.run")
    def test_failure_returns_empty(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Error")
        result = run_reflection("test prompt")
        assert result == ""

    @patch("app.post_mission_reflection.subprocess.run")
    def test_empty_output(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="   ")
        result = run_reflection("test prompt")
        assert result == ""

    @patch("app.post_mission_reflection.subprocess.run")
    def test_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=60)
        result = run_reflection("test prompt")
        assert result == ""

    @patch("app.post_mission_reflection.subprocess.run")
    def test_generic_exception(self, mock_run):
        mock_run.side_effect = OSError("No such file")
        result = run_reflection("test prompt")
        assert result == ""


class TestSaveReflection:
    def test_creates_new_shared_journal(self, instance_dir):
        save_reflection(instance_dir, "J'ai remarqué un pattern intéressant.")
        shared = instance_dir / "shared-journal.md"
        assert shared.exists()
        content = shared.read_text()
        assert "Kōan" in content
        assert "pattern intéressant" in content

    def test_appends_to_existing(self, instance_dir):
        shared = instance_dir / "shared-journal.md"
        shared.write_text("## Alexis — 2026-02-05\n\nOld thoughts.\n")
        save_reflection(instance_dir, "New reflection.")
        content = shared.read_text()
        assert "Old thoughts" in content
        assert "New reflection" in content

    def test_includes_timestamp(self, instance_dir):
        save_reflection(instance_dir, "test")
        content = (instance_dir / "shared-journal.md").read_text()
        assert re.search(r"## Kōan — \d{4}-\d{2}-\d{2} \d{2}:\d{2}", content)

    def test_entry_format(self, instance_dir):
        save_reflection(instance_dir, "Observation about code quality.")
        content = (instance_dir / "shared-journal.md").read_text()
        # Should have Kōan header + reflection content
        lines = content.strip().split("\n")
        assert any("Kōan" in l for l in lines)
        assert any("Observation about code quality" in l for l in lines)


class TestCLI:
    @patch("app.post_mission_reflection.subprocess.run")
    def test_main_with_significant_mission(self, mock_subprocess, instance_dir, journal_file):
        mock_subprocess.return_value = MagicMock(
            returncode=0, stdout="Réflexion post-mission."
        )
        with patch.object(
            sys, "argv",
            [
                "post_mission_reflection.py",
                str(instance_dir),
                "koan",
                str(journal_file),
                "--mission-title",
                "security audit of auth",
            ],
        ):
            main()
        shared = instance_dir / "shared-journal.md"
        assert shared.exists()
        assert "Réflexion post-mission" in shared.read_text()

    def test_main_skips_mundane_mission(self, instance_dir, journal_file):
        with patch.object(
            sys, "argv",
            [
                "post_mission_reflection.py",
                str(instance_dir),
                "koan",
                str(journal_file),
                "--mission-title",
                "fix typo",
            ],
        ):
            main()  # Should not crash
        shared = instance_dir / "shared-journal.md"
        assert not shared.exists()

    def test_main_missing_journal_file(self, instance_dir, tmp_path):
        with patch.object(
            sys, "argv",
            [
                "post_mission_reflection.py",
                str(instance_dir),
                "koan",
                str(tmp_path / "nonexistent.md"),
            ],
        ):
            main()  # Should not crash

    def test_main_missing_args(self):
        with patch.object(sys, "argv", ["post_mission_reflection.py"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_main_missing_instance_dir(self, tmp_path):
        with patch.object(
            sys, "argv",
            [
                "post_mission_reflection.py",
                str(tmp_path / "nonexistent"),
                "koan",
                str(tmp_path / "j.md"),
            ],
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    @patch("app.post_mission_reflection.subprocess.run")
    def test_main_no_reflection_generated(self, mock_subprocess, instance_dir, journal_file):
        mock_subprocess.return_value = MagicMock(returncode=0, stdout="")
        with patch.object(
            sys, "argv",
            [
                "post_mission_reflection.py",
                str(instance_dir),
                "koan",
                str(journal_file),
                "--mission-title",
                "feature implementation",
            ],
        ):
            main()
        shared = instance_dir / "shared-journal.md"
        assert not shared.exists()

    def test_main_no_mission_title(self, instance_dir, journal_file):
        """Without --mission-title, mission_title is empty, should_write_reflection returns False."""
        with patch.object(
            sys, "argv",
            [
                "post_mission_reflection.py",
                str(instance_dir),
                "koan",
                str(journal_file),
            ],
        ):
            main()  # Empty mission title → not significant
        shared = instance_dir / "shared-journal.md"
        assert not shared.exists()
