"""Tests for missions.py — centralized missions.md parser."""

import pytest
from app.missions import (
    classify_section,
    parse_sections,
    insert_mission,
    count_pending,
    extract_next_pending,
    extract_project_tag,
    group_by_project,
    find_section_boundaries,
    DEFAULT_SKELETON,
)


# --- classify_section ---

class TestClassifySection:
    def test_french_pending(self):
        assert classify_section("En attente") == "pending"

    def test_english_pending(self):
        assert classify_section("Pending") == "pending"

    def test_french_in_progress(self):
        assert classify_section("En cours") == "in_progress"

    def test_english_in_progress(self):
        assert classify_section("In Progress") == "in_progress"

    def test_french_done(self):
        assert classify_section("Terminées") == "done"

    def test_english_done(self):
        assert classify_section("Done") == "done"
        assert classify_section("Completed") == "done"

    def test_unknown(self):
        assert classify_section("Random") is None

    def test_case_insensitive(self):
        assert classify_section("EN ATTENTE") == "pending"
        assert classify_section("pending") == "pending"


# --- parse_sections ---

SAMPLE_CONTENT = (
    "# Missions\n\n"
    "## En attente\n\n"
    "- Fix the bug\n"
    "- Another task\n\n"
    "## En cours\n\n"
    "- Working on it\n\n"
    "## Terminées\n\n"
    "- **Done task** (session 1)\n"
)

class TestParseSections:
    def test_basic_parsing(self):
        result = parse_sections(SAMPLE_CONTENT)
        assert len(result["pending"]) == 2
        assert len(result["in_progress"]) == 1
        assert len(result["done"]) == 1

    def test_empty_content(self):
        result = parse_sections("")
        assert result == {"pending": [], "in_progress": [], "done": []}

    def test_complex_mission(self):
        content = (
            "## En cours\n\n"
            "### Big project\n"
            "- Step 1\n"
            "- Step 2\n\n"
            "## Terminées\n"
        )
        result = parse_sections(content)
        assert len(result["in_progress"]) == 1
        assert "### Big project" in result["in_progress"][0]
        assert "- Step 1" in result["in_progress"][0]

    def test_english_headers(self):
        content = "## Pending\n\n- Task\n\n## In Progress\n\n## Done\n"
        result = parse_sections(content)
        assert len(result["pending"]) == 1

    def test_continuation_lines(self):
        content = "## En attente\n\n- Main task\n  sub-item detail\n"
        result = parse_sections(content)
        assert len(result["pending"]) == 1
        assert "sub-item detail" in result["pending"][0]


# --- insert_mission ---

class TestInsertMission:
    def test_insert_into_existing(self):
        content = "# Missions\n\n## En attente\n\n## En cours\n"
        result = insert_mission(content, "- New task")
        assert "- New task" in result
        # Should be before "## En cours"
        assert result.index("- New task") < result.index("## En cours")

    def test_insert_into_empty(self):
        result = insert_mission("", "- New task")
        assert "## En attente" in result
        assert "- New task" in result

    def test_insert_english_header(self):
        content = "## Pending\n\n## In Progress\n"
        result = insert_mission(content, "- Task")
        assert "- Task" in result

    def test_insert_no_pending_section(self):
        content = "# Missions\n\n## En cours\n"
        result = insert_mission(content, "- Task")
        assert "## En attente" in result
        assert "- Task" in result


# --- count_pending ---

class TestCountPending:
    def test_count(self):
        assert count_pending(SAMPLE_CONTENT) == 2

    def test_empty(self):
        assert count_pending("## En attente\n\n## En cours\n") == 0

    def test_ignores_in_progress(self):
        content = "## En attente\n\n- One\n\n## En cours\n\n- Two\n"
        assert count_pending(content) == 1


# --- extract_next_pending ---

class TestExtractNextPending:
    def test_basic(self):
        assert extract_next_pending(SAMPLE_CONTENT) == "- Fix the bug"

    def test_empty(self):
        assert extract_next_pending("## En attente\n\n## En cours\n") == ""

    def test_project_filter_match(self):
        content = "## En attente\n\n- [projet:koan] Fix memory\n- [projet:anantys] Fix stripe\n"
        assert extract_next_pending(content, "koan") == "- [projet:koan] Fix memory"

    def test_project_filter_skip(self):
        content = "## En attente\n\n- [projet:anantys] Fix stripe\n"
        assert extract_next_pending(content, "koan") == ""

    def test_untagged_matches_any_project(self):
        content = "## En attente\n\n- Untagged task\n"
        assert extract_next_pending(content, "koan") == "- Untagged task"

    def test_english_sections(self):
        content = "## Pending\n\n- English task\n\n## In Progress\n"
        assert extract_next_pending(content) == "- English task"


# --- extract_project_tag ---

class TestExtractProjectTag:
    def test_with_tag(self):
        assert extract_project_tag("- [project:koan] Fix bug") == "koan"

    def test_french_tag(self):
        assert extract_project_tag("- [projet:anantys] Fix stripe") == "anantys"

    def test_no_tag(self):
        assert extract_project_tag("- Plain task") == "default"


# --- group_by_project ---

class TestGroupByProject:
    def test_grouping(self):
        content = (
            "## En attente\n\n"
            "- [project:koan] Fix memory\n"
            "- [project:anantys] Fix stripe\n"
            "- Untagged\n\n"
            "## En cours\n\n"
            "- [project:koan] Working\n"
        )
        result = group_by_project(content)
        assert len(result["koan"]["pending"]) == 1
        assert len(result["koan"]["in_progress"]) == 1
        assert len(result["anantys"]["pending"]) == 1
        assert len(result["default"]["pending"]) == 1


# --- find_section_boundaries ---

class TestFindSectionBoundaries:
    def test_boundaries(self):
        lines = [
            "# Missions",
            "",
            "## En attente",
            "",
            "- Task",
            "",
            "## En cours",
            "",
            "## Terminées",
            "",
        ]
        result = find_section_boundaries(lines)
        assert result["pending"] == (2, 6)
        assert result["in_progress"] == (6, 8)
        assert result["done"] == (8, 10)

    def test_missing_section(self):
        lines = ["## En attente", "", "- Task"]
        result = find_section_boundaries(lines)
        assert "pending" in result
        assert "in_progress" not in result
