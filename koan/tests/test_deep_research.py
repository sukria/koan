"""Tests for deep_research.py — DEEP mode topic suggestion system."""

import json
import subprocess
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.deep_research import DeepResearch


@pytest.fixture
def research_env(tmp_path):
    """Create a minimal environment for DeepResearch testing."""
    instance = tmp_path / "instance"
    project_path = tmp_path / "project"
    project_name = "testproj"

    # Create directory structure
    (instance / "memory" / "projects" / project_name).mkdir(parents=True)
    (instance / "journal").mkdir(parents=True)
    project_path.mkdir()

    return {
        "instance": instance,
        "project_path": project_path,
        "project_name": project_name,
    }


class TestGetPriorities:
    """Tests for priorities.md parsing."""

    def test_missing_priorities_file(self, research_env):
        """Returns empty sections when priorities.md doesn't exist."""
        research = DeepResearch(
            research_env["instance"],
            research_env["project_name"],
            research_env["project_path"],
        )

        result = research.get_priorities()

        assert result["current_focus"] == []
        assert result["strategic_goals"] == []
        assert result["technical_debt"] == []
        assert result["do_not_touch"] == []
        assert result["notes"] == ""

    def test_parse_all_sections(self, research_env):
        """Parses all sections from priorities.md."""
        priorities_content = """# Project Priorities

## Current Focus

- Implement user authentication
- Fix critical bug in checkout

## Strategic Goals

- Launch mobile app
- Improve test coverage

## Technical Debt

- Refactor legacy payment module
- Remove deprecated API endpoints

## Do Not Touch

- Legacy billing code (being rewritten)
- Mobile app (separate team)

## Notes

We're preparing for launch next month.
"""
        priorities_file = (
            research_env["instance"]
            / "memory"
            / "projects"
            / research_env["project_name"]
            / "priorities.md"
        )
        priorities_file.write_text(priorities_content)

        research = DeepResearch(
            research_env["instance"],
            research_env["project_name"],
            research_env["project_path"],
        )

        result = research.get_priorities()

        assert "Implement user authentication" in result["current_focus"]
        assert "Fix critical bug in checkout" in result["current_focus"]
        assert "Launch mobile app" in result["strategic_goals"]
        assert "Refactor legacy payment module" in result["technical_debt"]
        assert "Legacy billing code (being rewritten)" in result["do_not_touch"]
        assert "launch next month" in result["notes"]

    def test_skips_placeholder_items(self, research_env):
        """Skips items that are just placeholders like (What to do?)."""
        priorities_content = """# Project Priorities

## Current Focus

- (What's the most important thing?)
- Real task here

## Technical Debt

- (Known issues?)
"""
        priorities_file = (
            research_env["instance"]
            / "memory"
            / "projects"
            / research_env["project_name"]
            / "priorities.md"
        )
        priorities_file.write_text(priorities_content)

        research = DeepResearch(
            research_env["instance"],
            research_env["project_name"],
            research_env["project_path"],
        )

        result = research.get_priorities()

        assert len(result["current_focus"]) == 1
        assert "Real task here" in result["current_focus"]
        assert len(result["technical_debt"]) == 0

    def test_handles_html_comments(self, research_env):
        """Ignores HTML comment blocks in sections."""
        priorities_content = """## Current Focus

<!--
This is a template comment.
It should be ignored.
-->

- Actual priority item
"""
        priorities_file = (
            research_env["instance"]
            / "memory"
            / "projects"
            / research_env["project_name"]
            / "priorities.md"
        )
        priorities_file.write_text(priorities_content)

        research = DeepResearch(
            research_env["instance"],
            research_env["project_name"],
            research_env["project_path"],
        )

        result = research.get_priorities()

        assert len(result["current_focus"]) == 1
        assert "Actual priority item" in result["current_focus"]


class TestGitHubIntegration:
    """Tests for GitHub issue/PR fetching."""

    def test_get_open_issues_success(self, research_env):
        """Parses GitHub issues from gh CLI output."""
        mock_issues = [
            {"number": 42, "title": "Bug in login", "labels": [{"name": "bug"}], "createdAt": "2024-01-01"},
            {"number": 43, "title": "Add feature X", "labels": [{"name": "enhancement"}], "createdAt": "2024-01-02"},
        ]

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(mock_issues),
            )

            research = DeepResearch(
                research_env["instance"],
                research_env["project_name"],
                research_env["project_path"],
            )

            result = research.get_open_issues()

            assert len(result) == 2
            assert result[0]["number"] == 42
            assert result[1]["title"] == "Add feature X"

    def test_get_open_issues_gh_not_available(self, research_env):
        """Returns empty list when gh CLI is not available."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()

            research = DeepResearch(
                research_env["instance"],
                research_env["project_name"],
                research_env["project_path"],
            )

            result = research.get_open_issues()

            assert result == []

    def test_get_open_issues_timeout(self, research_env):
        """Returns empty list on timeout."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=30)

            research = DeepResearch(
                research_env["instance"],
                research_env["project_name"],
                research_env["project_path"],
            )

            result = research.get_open_issues()

            assert result == []

    def test_get_pending_prs(self, research_env):
        """Parses open PRs from gh CLI output."""
        mock_prs = [
            {"number": 100, "title": "Fix typo", "createdAt": "2024-01-01", "headRefName": "koan/fix-typo"},
        ]

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(mock_prs),
            )

            research = DeepResearch(
                research_env["instance"],
                research_env["project_name"],
                research_env["project_path"],
            )

            result = research.get_pending_prs()

            assert len(result) == 1
            assert result[0]["headRefName"] == "koan/fix-typo"


class TestJournalAnalysis:
    """Tests for recent journal topic extraction."""

    def test_extracts_session_headers(self, research_env):
        """Extracts ## headers from journal files."""
        today = datetime.now().strftime("%Y-%m-%d")
        journal_dir = research_env["instance"] / "journal" / today
        journal_dir.mkdir(parents=True)
        journal_file = journal_dir / f"{research_env['project_name']}.md"
        journal_file.write_text("""# Journal

## Session 100 — Test coverage expansion

Did some tests.

## Session 101 — Authentication refactor

Refactored auth.
""")

        research = DeepResearch(
            research_env["instance"],
            research_env["project_name"],
            research_env["project_path"],
        )

        result = research.get_recent_journal_topics()

        assert "Session 100 — Test coverage expansion" in result
        assert "Session 101 — Authentication refactor" in result

    def test_no_journal_files(self, research_env):
        """Returns empty list when no journal files exist."""
        research = DeepResearch(
            research_env["instance"],
            research_env["project_name"],
            research_env["project_path"],
        )

        result = research.get_recent_journal_topics()

        assert result == []


class TestTopicSuggestions:
    """Tests for the suggestion algorithm."""

    def test_prioritizes_current_focus(self, research_env):
        """Current focus items get priority 1."""
        priorities_content = """## Current Focus

- Critical bug fix

## Technical Debt

- Refactor module
"""
        priorities_file = (
            research_env["instance"]
            / "memory"
            / "projects"
            / research_env["project_name"]
            / "priorities.md"
        )
        priorities_file.write_text(priorities_content)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)  # gh fails

            research = DeepResearch(
                research_env["instance"],
                research_env["project_name"],
                research_env["project_path"],
            )

            result = research.suggest_topics()

            assert result[0]["priority"] == 1
            assert "Critical bug fix" in result[0]["topic"]

    def test_bug_issues_get_high_priority(self, research_env):
        """GitHub issues labeled 'bug' get priority 1."""
        mock_issues = [
            {"number": 1, "title": "Feature request", "labels": [{"name": "enhancement"}], "createdAt": "2024-01-01"},
            {"number": 2, "title": "Critical bug", "labels": [{"name": "bug"}], "createdAt": "2024-01-01"},
        ]

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(mock_issues),
            )

            research = DeepResearch(
                research_env["instance"],
                research_env["project_name"],
                research_env["project_path"],
            )

            result = research.suggest_topics()

            # Bug should be first (priority 1)
            bug_suggestion = next(s for s in result if "#2" in s["topic"])
            feature_suggestion = next(s for s in result if "#1" in s["topic"])

            assert bug_suggestion["priority"] == 1
            assert feature_suggestion["priority"] == 2

    def test_skips_recently_worked_topics(self, research_env):
        """Skips issues that match recent journal topics."""
        today = datetime.now().strftime("%Y-%m-%d")
        journal_dir = research_env["instance"] / "journal" / today
        journal_dir.mkdir(parents=True)
        journal_file = journal_dir / f"{research_env['project_name']}.md"
        journal_file.write_text("## Session — Test coverage expansion\n")

        mock_issues = [
            {"number": 1, "title": "Test coverage expansion", "labels": [], "createdAt": "2024-01-01"},
            {"number": 2, "title": "New feature", "labels": [], "createdAt": "2024-01-01"},
        ]

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(mock_issues),
            )

            research = DeepResearch(
                research_env["instance"],
                research_env["project_name"],
                research_env["project_path"],
            )

            result = research.suggest_topics()

            # Should only have the "New feature" issue
            titles = [s["topic"] for s in result]
            assert not any("Test coverage" in t for t in titles)
            assert any("#2" in t for t in titles)


class TestOutputFormatting:
    """Tests for output formatting."""

    def test_format_for_agent_with_suggestions(self, research_env):
        """Generates markdown with suggestions."""
        priorities_content = """## Current Focus

- Priority one

## Notes

Context for the agent.
"""
        priorities_file = (
            research_env["instance"]
            / "memory"
            / "projects"
            / research_env["project_name"]
            / "priorities.md"
        )
        priorities_file.write_text(priorities_content)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)

            research = DeepResearch(
                research_env["instance"],
                research_env["project_name"],
                research_env["project_path"],
            )

            result = research.format_for_agent()

            assert "## Deep Research Suggestions" in result
            assert "Priority one" in result
            assert "Context for the agent" in result
            assert "Choose ONE topic" in result

    def test_format_for_agent_with_do_not_touch(self, research_env):
        """Includes do-not-touch areas in output."""
        priorities_content = """## Do Not Touch

- Legacy billing module
"""
        priorities_file = (
            research_env["instance"]
            / "memory"
            / "projects"
            / research_env["project_name"]
            / "priorities.md"
        )
        priorities_file.write_text(priorities_content)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)

            research = DeepResearch(
                research_env["instance"],
                research_env["project_name"],
                research_env["project_path"],
            )

            result = research.format_for_agent()

            assert "### Avoid These Areas" in result
            assert "Legacy billing module" in result

    def test_format_for_agent_empty(self, research_env):
        """Returns empty string when no suggestions or constraints."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)

            research = DeepResearch(
                research_env["instance"],
                research_env["project_name"],
                research_env["project_path"],
            )

            result = research.format_for_agent()

            assert result == ""

    def test_to_json_includes_all_data(self, research_env):
        """JSON output includes all analyzed data."""
        priorities_content = """## Current Focus

- Task one
"""
        priorities_file = (
            research_env["instance"]
            / "memory"
            / "projects"
            / research_env["project_name"]
            / "priorities.md"
        )
        priorities_file.write_text(priorities_content)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)

            research = DeepResearch(
                research_env["instance"],
                research_env["project_name"],
                research_env["project_path"],
            )

            result = json.loads(research.to_json())

            assert "priorities" in result
            assert "suggestions" in result
            assert "do_not_touch" in result
            assert "open_issues" in result
            assert "recent_topics" in result
