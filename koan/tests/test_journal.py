"""Tests for app.journal — journal file management."""

import pytest
from datetime import date
from pathlib import Path


@pytest.fixture
def instance_dir(tmp_path):
    """Create an instance dir with journal structure."""
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    return tmp_path


# --- _to_date_string ---


class TestToDateString:
    def test_date_object(self):
        from app.journal import _to_date_string
        assert _to_date_string(date(2026, 2, 7)) == "2026-02-07"

    def test_string_passthrough(self):
        from app.journal import _to_date_string
        assert _to_date_string("2026-02-07") == "2026-02-07"


# --- get_journal_file ---


class TestGetJournalFile:
    def test_nested_exists(self, instance_dir):
        from app.journal import get_journal_file
        nested = instance_dir / "journal" / "2026-02-07" / "koan.md"
        nested.parent.mkdir(parents=True, exist_ok=True)
        nested.write_text("# Session")
        result = get_journal_file(instance_dir, "2026-02-07", "koan")
        assert result == nested

    def test_flat_exists(self, instance_dir):
        from app.journal import get_journal_file
        flat = instance_dir / "journal" / "2026-02-07.md"
        flat.write_text("# Session")
        result = get_journal_file(instance_dir, "2026-02-07", "koan")
        assert result == flat

    def test_neither_exists_returns_nested(self, instance_dir):
        from app.journal import get_journal_file
        result = get_journal_file(instance_dir, "2026-02-07", "koan")
        expected = instance_dir / "journal" / "2026-02-07" / "koan.md"
        assert result == expected
        assert not result.exists()

    def test_date_object(self, instance_dir):
        from app.journal import get_journal_file
        result = get_journal_file(instance_dir, date(2026, 2, 7), "koan")
        expected = instance_dir / "journal" / "2026-02-07" / "koan.md"
        assert result == expected

    def test_nested_preferred_over_flat(self, instance_dir):
        """When both exist, nested is returned."""
        from app.journal import get_journal_file
        nested = instance_dir / "journal" / "2026-02-07" / "koan.md"
        nested.parent.mkdir(parents=True, exist_ok=True)
        nested.write_text("nested")
        flat = instance_dir / "journal" / "2026-02-07.md"
        flat.write_text("flat")
        result = get_journal_file(instance_dir, "2026-02-07", "koan")
        assert result == nested


# --- read_all_journals ---


class TestReadAllJournals:
    def test_empty(self, instance_dir):
        from app.journal import read_all_journals
        assert read_all_journals(instance_dir, "2026-02-07") == ""

    def test_flat_file(self, instance_dir):
        from app.journal import read_all_journals
        flat = instance_dir / "journal" / "2026-02-07.md"
        flat.write_text("Flat content")
        result = read_all_journals(instance_dir, "2026-02-07")
        assert result == "Flat content"

    def test_nested_files(self, instance_dir):
        from app.journal import read_all_journals
        day_dir = instance_dir / "journal" / "2026-02-07"
        day_dir.mkdir(parents=True)
        (day_dir / "alpha.md").write_text("Alpha journal")
        (day_dir / "beta.md").write_text("Beta journal")
        result = read_all_journals(instance_dir, "2026-02-07")
        assert "[alpha]" in result
        assert "Alpha journal" in result
        assert "[beta]" in result
        assert "Beta journal" in result

    def test_combined_flat_and_nested(self, instance_dir):
        from app.journal import read_all_journals
        flat = instance_dir / "journal" / "2026-02-07.md"
        flat.write_text("Flat content")
        day_dir = instance_dir / "journal" / "2026-02-07"
        day_dir.mkdir()
        (day_dir / "proj.md").write_text("Nested content")
        result = read_all_journals(instance_dir, "2026-02-07")
        assert "Flat content" in result
        assert "Nested content" in result


# --- get_latest_journal ---


class TestGetLatestJournal:
    def test_no_journal(self, instance_dir):
        from app.journal import get_latest_journal
        result = get_latest_journal(instance_dir, project="koan", target_date="2026-02-07")
        assert "No journal" in result

    def test_empty_journal(self, instance_dir):
        from app.journal import get_latest_journal
        nested = instance_dir / "journal" / "2026-02-07" / "koan.md"
        nested.parent.mkdir(parents=True)
        nested.write_text("")
        result = get_latest_journal(instance_dir, project="koan", target_date="2026-02-07")
        assert "Empty journal" in result

    def test_with_content(self, instance_dir):
        from app.journal import get_latest_journal
        nested = instance_dir / "journal" / "2026-02-07" / "koan.md"
        nested.parent.mkdir(parents=True)
        nested.write_text("Session notes here")
        result = get_latest_journal(instance_dir, project="koan", target_date="2026-02-07")
        assert "koan" in result
        assert "Session notes here" in result

    def test_truncation(self, instance_dir):
        from app.journal import get_latest_journal
        nested = instance_dir / "journal" / "2026-02-07" / "koan.md"
        nested.parent.mkdir(parents=True)
        nested.write_text("x" * 1000)
        result = get_latest_journal(instance_dir, project="koan",
                                    target_date="2026-02-07", max_chars=100)
        assert "..." in result
        assert len(result) < 200

    def test_all_projects(self, instance_dir):
        from app.journal import get_latest_journal
        day_dir = instance_dir / "journal" / "2026-02-07"
        day_dir.mkdir(parents=True)
        (day_dir / "koan.md").write_text("Koan notes")
        result = get_latest_journal(instance_dir, target_date="2026-02-07")
        assert "Journal" in result
        assert "Koan notes" in result


# --- append_to_journal ---


class TestAppendToJournal:
    def test_creates_directory(self, instance_dir):
        from app.journal import append_to_journal
        append_to_journal(instance_dir, "koan", "New entry")
        # Check a file was created today
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        journal_file = instance_dir / "journal" / today / "koan.md"
        assert journal_file.exists()
        assert "New entry" in journal_file.read_text()

    def test_appends_to_existing(self, instance_dir):
        from app.journal import append_to_journal
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        journal_dir = instance_dir / "journal" / today
        journal_dir.mkdir(parents=True)
        journal_file = journal_dir / "koan.md"
        journal_file.write_text("First entry\n")
        append_to_journal(instance_dir, "koan", "Second entry\n")
        content = journal_file.read_text()
        assert "First entry" in content
        assert "Second entry" in content


# --- backward compatibility ---


class TestBackwardCompat:
    def test_journal_functions_accessible_from_utils(self):
        from app.utils import get_journal_file, read_all_journals, append_to_journal
        assert callable(get_journal_file)
        assert callable(read_all_journals)
        assert callable(append_to_journal)
