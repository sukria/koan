"""Tests for post_mission_reflection.py module."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.post_mission_reflection import (
    SIGNIFICANT_KEYWORDS,
    MIN_DURATION_MINUTES,
    build_reflection_prompt,
    is_significant_mission,
    run_reflection,
    write_to_journal,
)


class TestIsSignificantMission:
    """Tests for is_significant_mission()."""

    def test_mission_with_audit_keyword_is_significant(self):
        """Missions containing 'audit' are significant."""
        assert is_significant_mission("Security audit of auth module", 10)
        assert is_significant_mission("AUDIT BANKING CODE", 5)

    def test_mission_with_feature_keyword_is_significant(self):
        """Missions containing 'feature' are significant."""
        assert is_significant_mission("New feature: dark mode", 10)
        assert is_significant_mission("Major feature implementation", 5)

    def test_mission_with_refactor_keyword_is_significant(self):
        """Missions containing 'refactor' are significant."""
        assert is_significant_mission("Refactor portfolio handler", 10)
        assert is_significant_mission("Code refactoring session", 5)

    def test_mission_with_architecture_keyword_is_significant(self):
        """Missions containing 'architecture' are significant."""
        assert is_significant_mission("Architecture review", 10)

    def test_mission_with_deep_keyword_is_significant(self):
        """Missions containing 'deep' are significant."""
        assert is_significant_mission("Deep research on caching", 10)

    def test_long_mission_is_significant(self):
        """Long missions (>= 45 min) are significant even without keywords."""
        assert is_significant_mission("Fix typo in readme", MIN_DURATION_MINUTES)
        assert is_significant_mission("Simple task", 60)

    def test_short_mission_without_keywords_not_significant(self):
        """Short missions without keywords are not significant."""
        assert not is_significant_mission("Fix typo", 10)
        assert not is_significant_mission("Update readme", 30)

    def test_all_keywords_detected(self):
        """Verify all keywords trigger significance."""
        for keyword in SIGNIFICANT_KEYWORDS:
            assert is_significant_mission(f"Mission about {keyword}", 5), f"Keyword '{keyword}' not detected"


class TestBuildReflectionPrompt:
    """Tests for build_reflection_prompt()."""

    def test_includes_soul_when_exists(self, tmp_path):
        """Prompt includes soul.md content if present."""
        soul_file = tmp_path / "soul.md"
        soul_file.write_text("You are Koan. A sparring partner.")

        prompt = build_reflection_prompt(tmp_path, "Test mission")

        assert "Koan" in prompt
        assert "sparring partner" in prompt

    def test_includes_emotional_memory_when_exists(self, tmp_path):
        """Prompt includes emotional-memory.md if present."""
        memory_dir = tmp_path / "memory" / "global"
        memory_dir.mkdir(parents=True)
        (memory_dir / "emotional-memory.md").write_text("Alexis appreciates directness.")

        prompt = build_reflection_prompt(tmp_path, "Test mission")

        assert "directness" in prompt

    def test_includes_shared_journal_context(self, tmp_path):
        """Prompt includes recent shared journal entries."""
        journal_file = tmp_path / "shared-journal.md"
        journal_file.write_text("# Previous entries\n\nSome deep reflection here.")

        prompt = build_reflection_prompt(tmp_path, "Test mission")

        assert "Previous entries" in prompt or "deep reflection" in prompt

    def test_mission_text_included(self, tmp_path):
        """The mission text is included in the prompt."""
        prompt = build_reflection_prompt(tmp_path, "Security audit of banking module")

        assert "Security audit of banking module" in prompt

    def test_prompt_requests_french(self, tmp_path):
        """Prompt requests French output."""
        prompt = build_reflection_prompt(tmp_path, "Test mission")

        assert "French" in prompt or "Alexis" in prompt


class TestWriteToJournal:
    """Tests for write_to_journal()."""

    def test_creates_journal_if_not_exists(self, tmp_path):
        """Creates shared-journal.md if it doesn't exist."""
        write_to_journal(tmp_path, "First reflection.")

        journal_file = tmp_path / "shared-journal.md"
        assert journal_file.exists()
        assert "First reflection." in journal_file.read_text()

    def test_appends_to_existing_journal(self, tmp_path):
        """Appends new reflection to existing journal."""
        journal_file = tmp_path / "shared-journal.md"
        journal_file.write_text("# Journal\n\nOld entry.")

        write_to_journal(tmp_path, "New reflection.")

        content = journal_file.read_text()
        assert "Old entry." in content
        assert "New reflection." in content

    def test_includes_koan_header_and_timestamp(self, tmp_path):
        """Each reflection has a Koan header with timestamp."""
        write_to_journal(tmp_path, "Test reflection.")

        content = (tmp_path / "shared-journal.md").read_text()
        assert "### Kōan —" in content


class TestRunReflection:
    """Tests for run_reflection()."""

    @patch("app.post_mission_reflection.subprocess.run")
    def test_calls_claude_with_prompt(self, mock_run, tmp_path):
        """Calls claude CLI with the built prompt."""
        mock_run.return_value = MagicMock(returncode=0, stdout="Test reflection output")

        result = run_reflection(tmp_path, "Audit mission")

        assert mock_run.called
        args = mock_run.call_args[0][0]
        assert args[0] == "claude"
        assert "-p" in args
        assert result == "Test reflection output"

    @patch("app.post_mission_reflection.subprocess.run")
    def test_returns_empty_on_skip_signal(self, mock_run, tmp_path):
        """Returns empty string when Claude outputs skip signal."""
        mock_run.return_value = MagicMock(returncode=0, stdout="—")

        result = run_reflection(tmp_path, "Test mission")

        assert result == ""

    @patch("app.post_mission_reflection.subprocess.run")
    def test_returns_empty_on_timeout(self, mock_run, tmp_path):
        """Returns empty string on timeout."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=60)

        result = run_reflection(tmp_path, "Test mission")

        assert result == ""

    @patch("app.post_mission_reflection.subprocess.run")
    def test_returns_empty_on_error(self, mock_run, tmp_path):
        """Returns empty string on subprocess error."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        result = run_reflection(tmp_path, "Test mission")

        assert result == ""
