"""Tests for sanity.missions_structure — missions.md structural health checker."""

import os
import pytest
from pathlib import Path

from sanity.missions_structure import find_issues, sanitize, run_sanity_check


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CLEAN_MISSIONS = """\
# Missions

## Pending
- Fix the login bug
- Add dark mode

## In Progress
- [project:koan] Refactor utils.py

## Done
- Session 100: test coverage

## Ideas
- Add retry logic to API calls
"""

MISSIONS_WITH_FOREIGN_SECTIONS = """\
# Missions

## En attente
- koan clean up missions.md
- [project:web-app] fix CORS issue

## En cours

## Recent activity

Recent commits:
7a76fb4 Adjust testsuite for perl-versions
96a1b36 Update CI workflow

Active branches:
origin
origin/master

## Project structure

Directories: examples/, lib/, static/, t/
Files: CONTRIBUTING.md, Changes, Makefile.PL

## Current state

Pending:
- some mission context here

## Your mission

Dive deep into the codebase. Read key files.

## En cours

## Terminées
- Session 200: fixed weakened refs
"""

MISSIONS_WITH_DUPLICATE_PENDING = """\
# Missions

## Pending
- First mission

## In Progress

## Pending
- Second mission (from duplicate)

## Done
"""

MISSIONS_WITH_DUPLICATE_IDEAS = """\
# Missions

## Pending
- A mission

## In Progress

## Done

## Ideas
- Idea one

## Ideas
- Idea two (duplicate section)
"""


# ---------------------------------------------------------------------------
# find_issues tests
# ---------------------------------------------------------------------------

class TestFindIssues:
    def test_clean_file_has_no_issues(self):
        issues = find_issues(CLEAN_MISSIONS)
        assert issues == []

    def test_detects_foreign_sections(self):
        issues = find_issues(MISSIONS_WITH_FOREIGN_SECTIONS)
        foreign = [i for i in issues if "Foreign section" in i]
        assert len(foreign) == 4
        headers = [i.split("'")[1] for i in foreign]
        assert "## Recent activity" in headers
        assert "## Project structure" in headers
        assert "## Current state" in headers
        assert "## Your mission" in headers

    def test_detects_duplicate_sections(self):
        issues = find_issues(MISSIONS_WITH_FOREIGN_SECTIONS)
        dupes = [i for i in issues if "Duplicate section" in i]
        # "## En cours" appears twice
        assert len(dupes) == 1
        assert "En cours" in dupes[0]

    def test_detects_duplicate_pending(self):
        issues = find_issues(MISSIONS_WITH_DUPLICATE_PENDING)
        dupes = [i for i in issues if "Duplicate" in i]
        assert len(dupes) == 1
        assert "Pending" in dupes[0]

    def test_detects_duplicate_ideas(self):
        issues = find_issues(MISSIONS_WITH_DUPLICATE_IDEAS)
        dupes = [i for i in issues if "Duplicate" in i]
        assert len(dupes) == 1
        assert "Ideas" in dupes[0]

    def test_empty_content(self):
        issues = find_issues("")
        assert issues == []

    def test_no_sections(self):
        issues = find_issues("# Missions\n\nJust some text\n")
        assert issues == []

    def test_mixed_french_english_headers(self):
        content = "# Missions\n\n## Pending\n- a\n\n## En cours\n\n## Done\n"
        issues = find_issues(content)
        assert issues == []

    def test_reports_line_numbers(self):
        content = "# Missions\n\n## Pending\n- a\n\n## Recent activity\nstuff\n"
        issues = find_issues(content)
        assert len(issues) == 1
        assert "line 6" in issues[0]


# ---------------------------------------------------------------------------
# sanitize tests
# ---------------------------------------------------------------------------

class TestSanitize:
    def test_clean_file_unchanged(self):
        cleaned, changes = sanitize(CLEAN_MISSIONS)
        assert changes == []
        # Content should be equivalent (normalize_content may adjust whitespace)
        from app.missions import normalize_content
        assert cleaned == normalize_content(CLEAN_MISSIONS)

    def test_removes_foreign_sections(self):
        cleaned, changes = sanitize(MISSIONS_WITH_FOREIGN_SECTIONS)
        assert "## Recent activity" not in cleaned
        assert "## Project structure" not in cleaned
        assert "## Current state" not in cleaned
        assert "## Your mission" not in cleaned
        assert "Adjust testsuite" not in cleaned
        assert "Dive deep" not in cleaned

    def test_preserves_valid_sections(self):
        cleaned, changes = sanitize(MISSIONS_WITH_FOREIGN_SECTIONS)
        assert "## En attente" in cleaned or "## Pending" in cleaned
        assert "clean up missions" in cleaned
        assert "fix CORS issue" in cleaned
        assert "## Terminées" in cleaned or "## Done" in cleaned
        assert "Session 200" in cleaned

    def test_merges_duplicate_pending(self):
        cleaned, changes = sanitize(MISSIONS_WITH_DUPLICATE_PENDING)
        assert cleaned.count("## Pending") == 1
        assert "First mission" in cleaned
        assert "Second mission" in cleaned
        merge_changes = [c for c in changes if "Merged" in c]
        assert len(merge_changes) == 1

    def test_merges_duplicate_ideas(self):
        cleaned, changes = sanitize(MISSIONS_WITH_DUPLICATE_IDEAS)
        assert cleaned.lower().count("## ideas") == 1
        assert "Idea one" in cleaned
        assert "Idea two" in cleaned

    def test_removes_duplicate_empty_section(self):
        cleaned, changes = sanitize(MISSIONS_WITH_FOREIGN_SECTIONS)
        # The duplicate "## En cours" (empty) should be merged/removed
        en_cours_count = sum(
            1 for line in cleaned.splitlines()
            if line.strip().lower() in ("## en cours", "## in progress")
        )
        assert en_cours_count == 1

    def test_changes_list_describes_removals(self):
        _, changes = sanitize(MISSIONS_WITH_FOREIGN_SECTIONS)
        removed = [c for c in changes if "Removed" in c]
        assert len(removed) == 4  # 4 foreign sections

    def test_changes_list_describes_merges(self):
        _, changes = sanitize(MISSIONS_WITH_FOREIGN_SECTIONS)
        merged = [c for c in changes if "Merged" in c]
        assert len(merged) == 1  # duplicate En cours

    def test_preserves_title(self):
        cleaned, _ = sanitize(MISSIONS_WITH_FOREIGN_SECTIONS)
        assert cleaned.startswith("# Missions")

    def test_empty_content(self):
        cleaned, changes = sanitize("")
        assert changes == []
        assert cleaned == ""

    def test_only_title(self):
        cleaned, changes = sanitize("# Missions\n")
        assert changes == []

    def test_multiline_mission_items_preserved(self):
        content = """\
# Missions

## Pending
- First mission with details
  continued on next line
  and another line
- Second mission

## In Progress

## Done
"""
        cleaned, changes = sanitize(content)
        assert changes == []
        assert "continued on next line" in cleaned
        assert "and another line" in cleaned

    def test_real_world_pollution_pattern(self):
        """Simulate the actual pattern seen in instance/missions.md."""
        content = """\
# Missions

## Pending
- [project:koan] fix the auth bug
- [project:web-app] add CORS headers

## In Progress

## Recent activity

Recent commits:
7a76fb4 Adjust testsuite for perl-versions
96a1b36 Update CI workflow
2653068 Fix braces in example

Active branches:
origin
origin/master
origin/perl-versions

Recent changes:
.github/workflows/testsuite.yml |  73 ++++++++++++

## Project structure

Directories: examples/, lib/, static/, t/
Files: CONTRIBUTING.md, Changes, Makefile.PL, README.md

## Current state

Pending:
- [project:koan] we need deep exploration hours

## Your mission

Dive deep into the codebase. Read key files, understand patterns.

Think about:
- UX improvements
- Code quality issues

## Recent activity

Recent commits:
ac00d86 Trial release for 0.48_01
32b1d39 Remove TODO from cow test

## Project structure

Directories: t/
Files: Changes, Clone.pm, Clone.xs

## Current state

Pending:
- same stuff repeated

## Your mission

Same exploration prompt repeated.

## In Progress

## Done
- Session 207: investigate max turns error

## Ideas

### Clone
1. Add test coverage for overloaded objects
"""
        cleaned, changes = sanitize(content)

        # All foreign sections removed
        assert "## Recent activity" not in cleaned
        assert "## Project structure" not in cleaned
        assert "## Current state" not in cleaned
        assert "## Your mission" not in cleaned
        assert "Adjust testsuite" not in cleaned
        assert "Trial release" not in cleaned
        assert "Dive deep" not in cleaned

        # Valid content preserved
        assert "fix the auth bug" in cleaned
        assert "add CORS headers" in cleaned
        assert "Session 207" in cleaned
        assert "## Ideas" in cleaned
        assert "overloaded objects" in cleaned

        # Only one of each structural section
        lines = cleaned.splitlines()
        pending_count = sum(1 for l in lines if l.strip().lower() in ("## pending", "## en attente"))
        progress_count = sum(1 for l in lines if l.strip().lower() in ("## in progress", "## en cours"))
        done_count = sum(1 for l in lines if l.strip().lower() in ("## done", "## terminées"))
        assert pending_count == 1
        assert progress_count == 1
        assert done_count == 1

    def test_section_with_subheaders_preserved(self):
        """### sub-headers within valid sections should be preserved."""
        content = """\
# Missions

## Pending
### project:koan
- Mission one
### project:web-app
- Mission two

## In Progress

## Done
"""
        cleaned, changes = sanitize(content)
        assert changes == []
        assert "### project:koan" in cleaned
        assert "### project:web-app" in cleaned

    def test_ideas_with_subheaders_preserved(self):
        content = """\
# Missions

## Pending

## In Progress

## Done

## Ideas

### Clone
1. Add tests

### Simple-Accessor
1. Fix CI
"""
        cleaned, changes = sanitize(content)
        assert changes == []
        assert "### Clone" in cleaned
        assert "### Simple-Accessor" in cleaned


# ---------------------------------------------------------------------------
# run_sanity_check tests
# ---------------------------------------------------------------------------

class TestRunSanityCheck:
    def test_nonexistent_file(self, tmp_path):
        modified, changes = run_sanity_check(str(tmp_path / "missing.md"))
        assert not modified
        assert changes == []

    def test_empty_file(self, tmp_path):
        f = tmp_path / "missions.md"
        f.write_text("")
        modified, changes = run_sanity_check(str(f))
        assert not modified
        assert changes == []

    def test_clean_file_not_modified(self, tmp_path):
        f = tmp_path / "missions.md"
        f.write_text(CLEAN_MISSIONS)
        modified, changes = run_sanity_check(str(f))
        assert not modified
        assert changes == []

    def test_dirty_file_is_cleaned(self, tmp_path):
        f = tmp_path / "missions.md"
        f.write_text(MISSIONS_WITH_FOREIGN_SECTIONS)
        modified, changes = run_sanity_check(str(f))
        assert modified
        assert len(changes) > 0

        # Verify file was rewritten
        content = f.read_text()
        assert "## Recent activity" not in content
        assert "## Project structure" not in content

    def test_file_is_valid_after_cleanup(self, tmp_path):
        """After cleanup, the file should parse cleanly."""
        f = tmp_path / "missions.md"
        f.write_text(MISSIONS_WITH_FOREIGN_SECTIONS)
        run_sanity_check(str(f))

        content = f.read_text()
        issues = find_issues(content)
        assert issues == [], f"File still has issues after cleanup: {issues}"

    def test_idempotent(self, tmp_path):
        """Running sanitize twice should produce the same result."""
        f = tmp_path / "missions.md"
        f.write_text(MISSIONS_WITH_FOREIGN_SECTIONS)

        run_sanity_check(str(f))
        content_after_first = f.read_text()

        modified, changes = run_sanity_check(str(f))
        assert not modified
        assert changes == []
        assert f.read_text() == content_after_first

    def test_preserves_mission_items_after_round_trip(self, tmp_path):
        """All valid mission items survive the cleanup round-trip."""
        f = tmp_path / "missions.md"
        f.write_text(MISSIONS_WITH_FOREIGN_SECTIONS)
        run_sanity_check(str(f))

        content = f.read_text()
        assert "clean up missions" in content
        assert "fix CORS issue" in content
        assert "Session 200" in content

    def test_duplicate_merge_round_trip(self, tmp_path):
        f = tmp_path / "missions.md"
        f.write_text(MISSIONS_WITH_DUPLICATE_PENDING)
        run_sanity_check(str(f))

        content = f.read_text()
        assert "First mission" in content
        assert "Second mission" in content
        assert content.count("## Pending") == 1


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_section_header_case_insensitive(self):
        content = "# Missions\n\n## PENDING\n- a\n\n## IN PROGRESS\n\n## DONE\n"
        issues = find_issues(content)
        assert issues == []

    def test_foreign_section_with_no_content(self):
        content = "# Missions\n\n## Pending\n- a\n\n## Recent activity\n\n## Done\n"
        cleaned, changes = sanitize(content)
        assert "## Recent activity" not in cleaned
        assert len(changes) == 1

    def test_foreign_section_at_end_of_file(self):
        content = "# Missions\n\n## Pending\n\n## Done\n\n## Your mission\nSome text\n"
        cleaned, changes = sanitize(content)
        assert "## Your mission" not in cleaned
        assert "Some text" not in cleaned

    def test_multiple_different_foreign_sections(self):
        content = (
            "# Missions\n\n## Pending\n- a\n\n"
            "## Recent activity\ncommits\n\n"
            "## Project structure\nfiles\n\n"
            "## Current state\nstate\n\n"
            "## Your mission\ndo stuff\n\n"
            "## Done\n"
        )
        cleaned, changes = sanitize(content)
        removed = [c for c in changes if "Removed" in c]
        assert len(removed) == 4

    def test_completed_section_french(self):
        content = "# Missions\n\n## En attente\n- a\n\n## En cours\n\n## Terminées\n- done\n"
        issues = find_issues(content)
        assert issues == []

    def test_preserves_content_before_first_section(self):
        content = "# Missions\n\nSome preamble text\n\n## Pending\n- a\n\n## Done\n"
        cleaned, changes = sanitize(content)
        assert "Some preamble text" in cleaned
