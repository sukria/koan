"""Tests for extract_mission.py and memory_manager.py."""

import pytest
from pathlib import Path

from app.extract_mission import extract_next_mission
from app.memory_manager import (
    parse_summary_sessions,
    scoped_summary,
    compact_summary,
    cleanup_learnings,
)


# --- extract_mission tests ---


class TestExtractMission:
    def test_basic_extraction(self, tmp_path):
        missions = tmp_path / "missions.md"
        missions.write_text(
            "# Missions\n\n## Pending\n\n- Fix the bug\n- Another task\n\n## In Progress\n\n## Done\n"
        )
        assert extract_next_mission(str(missions)) == "- Fix the bug"

    def test_skips_other_sections(self, tmp_path):
        missions = tmp_path / "missions.md"
        missions.write_text(
            "# Missions\n\n## Pending\n\n\n## In Progress\n\n- In progress task\n\n## Done\n\n- Done task\n"
        )
        assert extract_next_mission(str(missions)) == ""

    def test_project_filter_match(self, tmp_path):
        missions = tmp_path / "missions.md"
        missions.write_text(
            "# Missions\n\n## Pending\n\n- [projet:anantys] Fix stripe\n- [projet:koan] Fix memory\n\n## In Progress\n"
        )
        assert extract_next_mission(str(missions), "koan") == "- [projet:koan] Fix memory"

    def test_project_filter_untagged_matches(self, tmp_path):
        missions = tmp_path / "missions.md"
        missions.write_text(
            "# Missions\n\n## Pending\n\n- Untagged task\n\n## In Progress\n"
        )
        assert extract_next_mission(str(missions), "koan") == "- Untagged task"

    def test_project_filter_skips_other_project(self, tmp_path):
        missions = tmp_path / "missions.md"
        missions.write_text(
            "# Missions\n\n## Pending\n\n- [projet:anantys] Fix stripe\n\n## In Progress\n"
        )
        assert extract_next_mission(str(missions), "koan") == ""

    def test_english_section_names(self, tmp_path):
        missions = tmp_path / "missions.md"
        missions.write_text(
            "# Missions\n\n## Pending\n\n- English task\n\n## In Progress\n"
        )
        assert extract_next_mission(str(missions)) == "- English task"

    def test_no_file(self, tmp_path):
        assert extract_next_mission(str(tmp_path / "nonexistent.md")) == ""

    def test_empty_pending(self, tmp_path):
        missions = tmp_path / "missions.md"
        missions.write_text("# Missions\n\n## Pending\n\n\n## In Progress\n")
        assert extract_next_mission(str(missions)) == ""


# --- memory_manager tests ---


SAMPLE_SUMMARY = """# Résumé des sessions

## 2026-01-31

Session 2 : Exploration autonome du repo koan. Blah blah.

Session 3 (projet: anantys) : Cartographie complète du backend Anantys.

## 2026-02-01

Session 23 (projet: koan) : Mode autonome. Implémentation run.py.

Session 24 (projet: koan) : Mode autonome. Housekeeping.

Session 25 (projet: anantys) : Fix banking route.
"""


class TestParseSummarySessions:
    def test_parses_all_sessions(self):
        sessions = parse_summary_sessions(SAMPLE_SUMMARY)
        assert len(sessions) == 5

    def test_extracts_project_hints(self):
        sessions = parse_summary_sessions(SAMPLE_SUMMARY)
        projects = [s[2] for s in sessions]
        assert projects == ["", "anantys", "koan", "koan", "anantys"]


class TestScopedSummary:
    def test_filters_to_koan(self, tmp_path):
        instance = tmp_path
        (instance / "memory").mkdir(parents=True)
        (instance / "memory" / "summary.md").write_text(SAMPLE_SUMMARY)

        result = scoped_summary(str(instance), "koan")
        assert "Session 23 (projet: koan)" in result
        assert "Session 24 (projet: koan)" in result
        assert "Session 2 : Exploration" in result  # No tag = included
        assert "anantys" not in result.replace("Session 2", "").lower() or "Session 25" not in result

    def test_filters_to_anantys(self, tmp_path):
        instance = tmp_path
        (instance / "memory").mkdir(parents=True)
        (instance / "memory" / "summary.md").write_text(SAMPLE_SUMMARY)

        result = scoped_summary(str(instance), "anantys")
        assert "Session 3 (projet: anantys)" in result
        assert "Session 25 (projet: anantys)" in result
        assert "Session 23 (projet: koan)" not in result


class TestCompactSummary:
    def test_compacts_to_limit(self, tmp_path):
        instance = tmp_path
        (instance / "memory").mkdir(parents=True)
        (instance / "memory" / "summary.md").write_text(SAMPLE_SUMMARY)

        removed = compact_summary(str(instance), max_sessions=3)
        assert removed == 2

        # Verify remaining content
        content = (instance / "memory" / "summary.md").read_text()
        sessions = parse_summary_sessions(content)
        assert len(sessions) == 3

    def test_no_compaction_needed(self, tmp_path):
        instance = tmp_path
        (instance / "memory").mkdir(parents=True)
        (instance / "memory" / "summary.md").write_text(SAMPLE_SUMMARY)

        removed = compact_summary(str(instance), max_sessions=20)
        assert removed == 0


class TestCleanupLearnings:
    def test_removes_duplicates(self, tmp_path):
        instance = tmp_path
        learnings_dir = instance / "memory" / "projects" / "koan"
        learnings_dir.mkdir(parents=True)
        (learnings_dir / "learnings.md").write_text(
            "# Learnings\n\n## Section\n\n- Fact one\n- Fact two\n- Fact one\n- Fact three\n"
        )

        removed = cleanup_learnings(str(instance), "koan")
        assert removed == 1

        content = (learnings_dir / "learnings.md").read_text()
        assert content.count("Fact one") == 1
        assert "Fact two" in content
        assert "Fact three" in content

    def test_no_duplicates(self, tmp_path):
        instance = tmp_path
        learnings_dir = instance / "memory" / "projects" / "koan"
        learnings_dir.mkdir(parents=True)
        (learnings_dir / "learnings.md").write_text(
            "# Learnings\n\n- Unique line\n"
        )

        removed = cleanup_learnings(str(instance), "koan")
        assert removed == 0
