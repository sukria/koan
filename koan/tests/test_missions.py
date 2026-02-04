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
    format_queue,
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


    def test_single_project_no_tags(self):
        """Single-project setup: all missions untagged go to 'default'."""
        content = (
            "## En attente\n\n"
            "- Fix the login bug\n"
            "- Add dark mode\n\n"
            "## En cours\n\n"
            "- Write documentation\n\n"
            "## Terminées\n\n"
            "- Initial setup\n"
        )
        result = group_by_project(content)
        assert list(result.keys()) == ["default"]
        assert len(result["default"]["pending"]) == 2
        assert len(result["default"]["in_progress"]) == 1


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


# --- parse_sections edge cases (complex blocks) ---

class TestParseSectionsComplexBlocks:
    """Tests for ### block flushing at section boundaries, sequential blocks, and EOF."""

    def test_complex_block_flushed_at_section_boundary(self):
        """### block in one section should be flushed when next ## section starts (lines 53-55)."""
        content = (
            "## En cours\n\n"
            "### Big project\n"
            "- Step 1\n"
            "- Step 2\n"
            "## Terminées\n\n"
            "- Done task\n"
        )
        result = parse_sections(content)
        assert len(result["in_progress"]) == 1
        assert "### Big project" in result["in_progress"][0]
        assert "- Step 2" in result["in_progress"][0]
        assert len(result["done"]) == 1
        assert "- Done task" in result["done"][0]

    def test_sequential_complex_blocks_same_section(self):
        """Two ### blocks in the same section should be separate entries (lines 65-66)."""
        content = (
            "## En cours\n\n"
            "### Block A\n"
            "- Detail A\n"
            "### Block B\n"
            "- Detail B\n\n"
            "## Terminées\n"
        )
        result = parse_sections(content)
        assert len(result["in_progress"]) == 2
        assert "### Block A" in result["in_progress"][0]
        assert "### Block B" in result["in_progress"][1]

    def test_complex_block_at_eof_no_trailing_newline(self):
        """### block at end of file with no trailing blank line (lines 82-83)."""
        content = (
            "## En cours\n\n"
            "### Final block\n"
            "- Last item"
        )
        result = parse_sections(content)
        assert len(result["in_progress"]) == 1
        assert "### Final block" in result["in_progress"][0]
        assert "- Last item" in result["in_progress"][0]

    def test_mixed_simple_and_complex_same_section(self):
        """Simple - items followed by ### block in same section."""
        content = (
            "## En attente\n\n"
            "- Simple task\n"
            "### Complex task\n"
            "- Sub-detail\n\n"
            "## En cours\n"
        )
        result = parse_sections(content)
        assert len(result["pending"]) == 2
        assert result["pending"][0] == "- Simple task"
        assert "### Complex task" in result["pending"][1]

    def test_complex_block_with_strikethrough(self):
        """Real-world pattern: ### block with ~~done~~ items (from actual missions.md)."""
        content = (
            "## En cours\n\n"
            "### project:anantys Admin Dashboard\n"
            "- ~~Explorer l'admin~~ done\n"
            "- ~~Cartographier les données~~ done\n"
            "- Reste à faire : V2\n\n"
            "## Terminées\n"
        )
        result = parse_sections(content)
        assert len(result["in_progress"]) == 1
        block = result["in_progress"][0]
        assert "~~Explorer" in block
        assert "Reste à faire" in block

    def test_empty_complex_block(self):
        """### header with no content lines before next ### or section."""
        content = (
            "## En cours\n\n"
            "### Empty block\n"
            "### Second block\n"
            "- Content\n\n"
            "## Terminées\n"
        )
        result = parse_sections(content)
        assert len(result["in_progress"]) == 2

    def test_unrecognized_section_header(self):
        """Content under unrecognized ## header should be ignored."""
        content = (
            "## En attente\n\n"
            "- Task\n\n"
            "## Random section\n\n"
            "- Should be ignored\n\n"
            "## En cours\n"
        )
        result = parse_sections(content)
        assert len(result["pending"]) == 1
        assert result["pending"][0] == "- Task"
        # "Should be ignored" must not appear in any section
        for key in result:
            for item in result[key]:
                assert "Should be ignored" not in item


# --- Sub-header project grouping (### project:X) ---

class TestSubHeaderProjectGrouping:
    """Tests for ### project:X sub-headers in pending section."""

    SUBHEADER_CONTENT = (
        "# Missions\n\n"
        "## En attente\n\n"
        "### projet:anantys-back\n\n"
        "### project:koan\n"
        "- Fix the rotation bug\n"
        "- Fix test warnings\n\n"
        "## En cours\n\n"
        "## Terminées\n"
    )

    def test_extract_pending_with_subheader_filter_match(self):
        """Missions under ### project:koan should match when filtering for koan."""
        result = extract_next_pending(self.SUBHEADER_CONTENT, "koan")
        assert result == "- Fix the rotation bug"

    def test_extract_pending_with_subheader_filter_skip(self):
        """Missions under ### project:koan should NOT match when filtering for anantys-back."""
        result = extract_next_pending(self.SUBHEADER_CONTENT, "anantys-back")
        assert result == ""

    def test_extract_pending_no_filter_returns_first(self):
        """Without project filter, returns first mission regardless of sub-header."""
        result = extract_next_pending(self.SUBHEADER_CONTENT)
        assert result == "- Fix the rotation bug"

    def test_inline_tag_overrides_subheader(self):
        """Inline [project:X] tag takes priority over ### sub-header context."""
        content = (
            "## En attente\n\n"
            "### project:koan\n"
            "- [project:anantys] Overridden task\n"
            "- Normal koan task\n\n"
            "## En cours\n"
        )
        result = extract_next_pending(content, "koan")
        assert result == "- Normal koan task"

    def test_untagged_outside_subheader_matches_any(self):
        """Missions outside any sub-header (untagged) match any project filter."""
        content = (
            "## En attente\n\n"
            "- Untagged task\n"
            "### project:koan\n"
            "- Koan task\n\n"
            "## En cours\n"
        )
        result = extract_next_pending(content, "anantys")
        assert result == "- Untagged task"

    def test_french_subheader_variant(self):
        """### projet:X (French) should also work."""
        content = (
            "## En attente\n\n"
            "### projet:anantys-back\n"
            "- French tagged task\n\n"
            "## En cours\n"
        )
        result = extract_next_pending(content, "anantys-back")
        assert result == "- French tagged task"

    def test_extract_project_tag_from_subheader(self):
        """extract_project_tag should match ### project:X format in block text."""
        block = "### project:koan\n- Fix bug\n- Fix tests"
        assert extract_project_tag(block) == "koan"

    def test_extract_project_tag_french_subheader(self):
        block = "### projet:anantys-back\n- Task"
        assert extract_project_tag(block) == "anantys-back"

    def test_group_by_project_with_subheaders(self):
        """group_by_project should correctly assign missions under ### sub-headers."""
        content = (
            "## En attente\n\n"
            "### project:koan\n"
            "- Koan task 1\n"
            "- Koan task 2\n\n"
            "### project:anantys\n"
            "- Anantys task\n\n"
            "## En cours\n\n"
            "## Terminées\n"
        )
        result = group_by_project(content)
        assert "koan" in result
        assert len(result["koan"]["pending"]) >= 1
        assert "anantys" in result
        assert len(result["anantys"]["pending"]) >= 1


# --- format_queue ---

class TestFormatQueue:
    """Tests for format_queue() — full numbered mission queue display."""

    def test_empty_queue(self):
        content = "# Missions\n\n## En attente\n\n## En cours\n\n## Terminées\n"
        result = format_queue(content)
        assert "vide" in result.lower()

    def test_pending_only(self):
        content = (
            "# Missions\n\n"
            "## En attente\n\n"
            "- fix the login bug\n"
            "- add dark mode\n"
            "- refactor auth\n\n"
            "## En cours\n\n"
            "## Terminées\n"
        )
        result = format_queue(content)
        assert "1. fix the login bug" in result
        assert "2. add dark mode" in result
        assert "3. refactor auth" in result
        assert "Pending (3)" in result

    def test_in_progress_and_pending(self):
        content = (
            "# Missions\n\n"
            "## En attente\n\n"
            "- task two\n\n"
            "## En cours\n\n"
            "- task one\n\n"
            "## Terminées\n"
        )
        result = format_queue(content)
        assert "In progress" in result
        assert "→ task one" in result
        assert "1. task two" in result

    def test_strips_project_tags(self):
        content = (
            "## En attente\n\n"
            "- [project:koan] add tests\n"
            "- [project:web-app] fix CSRF\n\n"
            "## En cours\n\n"
            "- [project:koan] doing stuff\n\n"
        )
        result = format_queue(content)
        assert "[project:koan]" not in result
        assert "[project:web-app]" not in result
        assert "[koan]" in result
        assert "[web-app]" in result
        assert "add tests" in result
        assert "fix CSRF" in result

    def test_no_tag_for_default_project(self):
        content = (
            "## En attente\n\n"
            "- untagged task\n\n"
            "## En cours\n\n"
        )
        result = format_queue(content)
        assert "1. untagged task" in result
        assert "[default]" not in result

    def test_done_missions_excluded(self):
        content = (
            "## En attente\n\n"
            "- pending task\n\n"
            "## En cours\n\n"
            "## Terminées\n\n"
            "- old done task\n"
        )
        result = format_queue(content)
        assert "pending task" in result
        assert "old done task" not in result

    def test_in_progress_only(self):
        content = (
            "## En attente\n\n"
            "## En cours\n\n"
            "- working on it\n\n"
            "## Terminées\n"
        )
        result = format_queue(content)
        assert "In progress" in result
        assert "→ working on it" in result
        assert "Pending" not in result
