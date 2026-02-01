"""Tests for send_retrospective module."""

from datetime import date
from pathlib import Path
from unittest.mock import patch

from app.send_retrospective import (
    get_todays_journal,
    extract_session_summary,
    append_to_outbox,
    create_retrospective,
)


class TestGetTodaysJournal:
    def test_prefers_nested_structure(self, instance_dir):
        today = date.today().strftime("%Y-%m-%d")
        nested = instance_dir / "journal" / today
        nested.mkdir(parents=True)
        journal_file = nested / "koan.md"
        journal_file.write_text("nested journal")

        result = get_todays_journal(instance_dir, "koan")
        assert result == journal_file
        assert result.exists()

    def test_falls_back_to_flat_structure(self, instance_dir):
        today = date.today().strftime("%Y-%m-%d")
        flat_file = instance_dir / "journal" / f"{today}.md"
        flat_file.write_text("flat journal")

        result = get_todays_journal(instance_dir, "koan")
        assert result == flat_file

    def test_returns_nested_path_when_nothing_exists(self, instance_dir):
        today = date.today().strftime("%Y-%m-%d")
        result = get_todays_journal(instance_dir, "koan")
        expected = instance_dir / "journal" / today / "koan.md"
        assert result == expected
        assert not result.exists()


class TestExtractSessionSummary:
    def test_returns_default_when_no_journal(self, tmp_path):
        result = extract_session_summary(tmp_path / "nonexistent.md")
        assert "brief" in result

    def test_returns_default_when_empty_journal(self, tmp_path):
        f = tmp_path / "empty.md"
        f.write_text("   \n  ")
        result = extract_session_summary(f)
        assert "brief" in result

    def test_returns_full_content_when_short(self, tmp_path):
        f = tmp_path / "journal.md"
        f.write_text("## Session 1\n\nDid some work.")
        result = extract_session_summary(f)
        assert "Did some work" in result

    def test_truncates_long_content_with_sections(self, tmp_path):
        f = tmp_path / "journal.md"
        sections = "\n## ".join([f"Section {i}\n{'x' * 100}" for i in range(20)])
        f.write_text(sections)
        result = extract_session_summary(f, max_chars=400)
        assert len(result) <= 400 + 10  # small margin for join

    def test_truncates_long_content_without_sections(self, tmp_path):
        f = tmp_path / "journal.md"
        f.write_text("a" * 2000)
        result = extract_session_summary(f, max_chars=800)
        assert result.startswith("...")
        assert len(result) <= 804  # "..." + 800


class TestAppendToOutbox:
    def test_appends_message(self, instance_dir):
        append_to_outbox(instance_dir, "Hello")
        content = (instance_dir / "outbox.md").read_text()
        assert "Hello\n" in content

    def test_appends_multiple_messages(self, instance_dir):
        append_to_outbox(instance_dir, "First")
        append_to_outbox(instance_dir, "Second")
        content = (instance_dir / "outbox.md").read_text()
        assert "First" in content
        assert "Second" in content

    def test_handles_missing_outbox_gracefully(self, tmp_path):
        # Should not raise — creates the file
        append_to_outbox(tmp_path, "New message")
        assert (tmp_path / "outbox.md").read_text() == "New message\n"


class TestCreateRetrospective:
    def test_creates_retrospective_in_outbox(self, instance_dir):
        today = date.today().strftime("%Y-%m-%d")
        journal_dir = instance_dir / "journal" / today
        journal_dir.mkdir(parents=True)
        (journal_dir / "koan.md").write_text("## Session\n\nWorked on tests.")

        create_retrospective(instance_dir, "koan")

        outbox = (instance_dir / "outbox.md").read_text()
        assert "Retrospective" in outbox
        assert "koan" in outbox
        assert "Budget exhausted" in outbox

    def test_works_without_journal(self, instance_dir):
        create_retrospective(instance_dir, "koan")
        outbox = (instance_dir / "outbox.md").read_text()
        assert "Retrospective" in outbox
        assert "brief" in outbox
