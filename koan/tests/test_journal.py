"""Tests for koan/app/journal.py — journal management."""
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def instance_dir(tmp_path):
    """Create a temporary instance directory with journal structure."""
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    return tmp_path


# --- _to_date_string ---


class TestToDateString:
    def test_date_object(self):
        from app.journal import _to_date_string

        assert _to_date_string(date(2026, 2, 6)) == "2026-02-06"

    def test_datetime_object(self):
        from app.journal import _to_date_string

        assert _to_date_string(datetime(2026, 2, 6, 12, 30)) == "2026-02-06"

    def test_string_passthrough(self):
        from app.journal import _to_date_string

        assert _to_date_string("2026-02-06") == "2026-02-06"


# --- get_journal_file ---


class TestGetJournalFile:
    def test_returns_nested_path_when_exists(self, instance_dir):
        from app.journal import get_journal_file

        nested_dir = instance_dir / "journal" / "2026-02-06"
        nested_dir.mkdir()
        nested_file = nested_dir / "koan.md"
        nested_file.write_text("# Session 95")

        result = get_journal_file(instance_dir, "2026-02-06", "koan")
        assert result == nested_file

    def test_returns_flat_path_when_no_nested(self, instance_dir):
        from app.journal import get_journal_file

        flat_file = instance_dir / "journal" / "2026-02-06.md"
        flat_file.write_text("# Flat journal")

        result = get_journal_file(instance_dir, "2026-02-06", "koan")
        assert result == flat_file

    def test_returns_nested_default_when_neither_exists(self, instance_dir):
        from app.journal import get_journal_file

        result = get_journal_file(instance_dir, "2026-02-06", "koan")
        expected = instance_dir / "journal" / "2026-02-06" / "koan.md"
        assert result == expected

    def test_prefers_nested_over_flat(self, instance_dir):
        from app.journal import get_journal_file

        # Create both flat and nested
        flat = instance_dir / "journal" / "2026-02-06.md"
        flat.write_text("flat")
        nested_dir = instance_dir / "journal" / "2026-02-06"
        nested_dir.mkdir()
        nested = nested_dir / "koan.md"
        nested.write_text("nested")

        result = get_journal_file(instance_dir, "2026-02-06", "koan")
        assert result == nested

    def test_accepts_date_object(self, instance_dir):
        from app.journal import get_journal_file

        result = get_journal_file(instance_dir, date(2026, 2, 6), "koan")
        expected = instance_dir / "journal" / "2026-02-06" / "koan.md"
        assert result == expected


# --- read_all_journals ---


class TestReadAllJournals:
    def test_reads_flat_file(self, instance_dir):
        from app.journal import read_all_journals

        flat = instance_dir / "journal" / "2026-02-06.md"
        flat.write_text("Flat journal content")

        result = read_all_journals(instance_dir, "2026-02-06")
        assert "Flat journal content" in result

    def test_reads_nested_files(self, instance_dir):
        from app.journal import read_all_journals

        nested_dir = instance_dir / "journal" / "2026-02-06"
        nested_dir.mkdir()
        (nested_dir / "koan.md").write_text("Koan journal")
        (nested_dir / "webapp.md").write_text("Webapp journal")

        result = read_all_journals(instance_dir, "2026-02-06")
        assert "[koan]" in result
        assert "Koan journal" in result
        assert "[webapp]" in result
        assert "Webapp journal" in result

    def test_combines_flat_and_nested(self, instance_dir):
        from app.journal import read_all_journals

        flat = instance_dir / "journal" / "2026-02-06.md"
        flat.write_text("Legacy flat content")
        nested_dir = instance_dir / "journal" / "2026-02-06"
        nested_dir.mkdir()
        (nested_dir / "koan.md").write_text("Nested content")

        result = read_all_journals(instance_dir, "2026-02-06")
        assert "Legacy flat content" in result
        assert "Nested content" in result
        assert "---" in result  # separator

    def test_empty_when_no_files(self, instance_dir):
        from app.journal import read_all_journals

        result = read_all_journals(instance_dir, "2026-02-06")
        assert result == ""

    def test_ignores_non_md_files(self, instance_dir):
        from app.journal import read_all_journals

        nested_dir = instance_dir / "journal" / "2026-02-06"
        nested_dir.mkdir()
        (nested_dir / "koan.md").write_text("Journal")
        (nested_dir / "notes.txt").write_text("Not a journal")

        result = read_all_journals(instance_dir, "2026-02-06")
        assert "Journal" in result
        assert "Not a journal" not in result

    def test_sorted_alphabetically(self, instance_dir):
        from app.journal import read_all_journals

        nested_dir = instance_dir / "journal" / "2026-02-06"
        nested_dir.mkdir()
        (nested_dir / "backend.md").write_text("B content")
        (nested_dir / "alpha.md").write_text("A content")

        result = read_all_journals(instance_dir, "2026-02-06")
        # alpha should come before backend
        assert result.index("[alpha]") < result.index("[backend]")


# --- get_latest_journal ---


class TestGetLatestJournal:
    def test_specific_project(self, instance_dir):
        from app.journal import get_latest_journal

        nested_dir = instance_dir / "journal" / "2026-02-06"
        nested_dir.mkdir()
        (nested_dir / "koan.md").write_text("Session 95 content here")

        result = get_latest_journal(instance_dir, project="koan", target_date="2026-02-06")
        assert "koan" in result
        assert "2026-02-06" in result
        assert "Session 95 content here" in result

    def test_no_journal_for_project(self, instance_dir):
        from app.journal import get_latest_journal

        result = get_latest_journal(instance_dir, project="koan", target_date="2026-02-06")
        assert "No journal for koan" in result

    def test_empty_journal(self, instance_dir):
        from app.journal import get_latest_journal

        nested_dir = instance_dir / "journal" / "2026-02-06"
        nested_dir.mkdir()
        (nested_dir / "koan.md").write_text("")

        result = get_latest_journal(instance_dir, project="koan", target_date="2026-02-06")
        assert "Empty journal" in result

    def test_all_projects(self, instance_dir):
        from app.journal import get_latest_journal

        nested_dir = instance_dir / "journal" / "2026-02-06"
        nested_dir.mkdir()
        (nested_dir / "koan.md").write_text("Content here")

        result = get_latest_journal(instance_dir, target_date="2026-02-06")
        assert "Journal" in result
        assert "2026-02-06" in result

    def test_no_journal_for_date(self, instance_dir):
        from app.journal import get_latest_journal

        result = get_latest_journal(instance_dir, target_date="2025-01-01")
        assert "No journal for 2025-01-01" in result

    def test_truncates_long_content(self, instance_dir):
        from app.journal import get_latest_journal

        nested_dir = instance_dir / "journal" / "2026-02-06"
        nested_dir.mkdir()
        (nested_dir / "koan.md").write_text("A" * 1000)

        result = get_latest_journal(
            instance_dir, project="koan", target_date="2026-02-06", max_chars=100
        )
        assert "..." in result
        # Total content after truncation should be under max_chars + header
        content_part = result.split("\n\n", 1)[1]
        assert len(content_part) <= 100

    def test_defaults_to_today(self, instance_dir):
        from app.journal import get_latest_journal

        today = date.today().strftime("%Y-%m-%d")
        nested_dir = instance_dir / "journal" / today
        nested_dir.mkdir()
        (nested_dir / "koan.md").write_text("Today's journal")

        result = get_latest_journal(instance_dir, project="koan")
        assert "Today's journal" in result

    def test_emoji_in_header(self, instance_dir):
        from app.journal import get_latest_journal

        nested_dir = instance_dir / "journal" / "2026-02-06"
        nested_dir.mkdir()
        (nested_dir / "koan.md").write_text("Content")

        result = get_latest_journal(instance_dir, project="koan", target_date="2026-02-06")
        assert "\U0001f4d3" in result  # notebook emoji


# --- append_to_journal ---


class TestAppendToJournal:
    def test_creates_file_and_appends(self, instance_dir):
        from app.journal import append_to_journal

        append_to_journal(instance_dir, "koan", "First entry\n")
        append_to_journal(instance_dir, "koan", "Second entry\n")

        today = datetime.now().strftime("%Y-%m-%d")
        journal_file = instance_dir / "journal" / today / "koan.md"
        assert journal_file.exists()
        content = journal_file.read_text()
        assert "First entry" in content
        assert "Second entry" in content

    def test_creates_directory_structure(self, instance_dir):
        from app.journal import append_to_journal

        # Remove journal dir to test creation
        import shutil
        shutil.rmtree(instance_dir / "journal")

        append_to_journal(instance_dir, "koan", "Content\n")

        today = datetime.now().strftime("%Y-%m-%d")
        assert (instance_dir / "journal" / today / "koan.md").exists()

    def test_different_projects_different_files(self, instance_dir):
        from app.journal import append_to_journal

        append_to_journal(instance_dir, "koan", "Koan entry\n")
        append_to_journal(instance_dir, "webapp", "Webapp entry\n")

        today = datetime.now().strftime("%Y-%m-%d")
        koan_file = instance_dir / "journal" / today / "koan.md"
        webapp_file = instance_dir / "journal" / today / "webapp.md"
        assert koan_file.read_text() == "Koan entry\n"
        assert webapp_file.read_text() == "Webapp entry\n"
