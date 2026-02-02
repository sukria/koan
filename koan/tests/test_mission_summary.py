"""Tests for mission_summary.py"""

from datetime import date
from pathlib import Path

import pytest

from app.mission_summary import extract_latest_section, get_mission_summary, summarize_section


class TestExtractLatestSection:
    def test_single_section(self):
        text = "## Session 1\n\nDid some work."
        assert extract_latest_section(text) == "## Session 1\n\nDid some work."

    def test_multiple_sections(self):
        text = "## Session 1\n\nOld stuff.\n\n## Session 2\n\nNew stuff."
        result = extract_latest_section(text)
        assert "Session 2" in result
        assert "New stuff" in result
        assert "Old stuff" not in result

    def test_no_sections(self):
        text = "Just plain text."
        assert extract_latest_section(text) == "Just plain text."

    def test_empty(self):
        assert extract_latest_section("") == ""


class TestSummarizeSection:
    def test_basic(self):
        section = "## Fix auth bug\n\nFixed the token refresh logic.\nAdded retry on 401."
        result = summarize_section(section)
        assert "Fix auth bug" in result
        assert "token refresh" in result

    def test_truncates_long_text(self):
        section = "## Long\n\n" + "A" * 500
        result = summarize_section(section, max_chars=100)
        assert len(result) < 200  # heading + truncated body

    def test_stops_at_code_block(self):
        section = "## Impl\n\nAdded feature.\n```python\ncode here\n```"
        result = summarize_section(section)
        assert "code here" not in result
        assert "Added feature" in result

    def test_empty(self):
        assert summarize_section("") == ""

    def test_max_lines(self):
        section = "## Title\n\nLine 1\nLine 2\nLine 3\nLine 4\nLine 5\nLine 6"
        result = summarize_section(section)
        # Should cap at 4 body lines
        assert "Line 5" not in result


class TestGetMissionSummary:
    def test_nested_journal(self, tmp_path):
        today = date.today().strftime("%Y-%m-%d")
        journal_dir = tmp_path / "journal" / today
        journal_dir.mkdir(parents=True)
        (journal_dir / "myproject.md").write_text(
            "## Session 1\n\nEarly work.\n\n## Session 2\n\nLatest findings."
        )
        result = get_mission_summary(str(tmp_path), "myproject")
        assert "Session 2" in result
        assert "Latest findings" in result

    def test_flat_journal_fallback(self, tmp_path):
        today = date.today().strftime("%Y-%m-%d")
        journal_dir = tmp_path / "journal"
        journal_dir.mkdir(parents=True)
        (journal_dir / f"{today}.md").write_text("## Work\n\nDid things.")
        result = get_mission_summary(str(tmp_path), "anyproject")
        assert "Did things" in result

    def test_no_journal(self, tmp_path):
        result = get_mission_summary(str(tmp_path), "noproject")
        assert result == ""

    def test_empty_journal(self, tmp_path):
        today = date.today().strftime("%Y-%m-%d")
        journal_dir = tmp_path / "journal" / today
        journal_dir.mkdir(parents=True)
        (journal_dir / "proj.md").write_text("")
        result = get_mission_summary(str(tmp_path), "proj")
        assert result == ""


class TestSummarizeSectionEdgeCases:
    """Cover line 51 (rsplit truncation) and line 64 (no heading)."""

    def test_truncation_with_rsplit(self):
        """Long body line triggers rsplit truncation at word boundary."""
        section = "## Title\n\n" + "word " * 100
        result = summarize_section(section, max_chars=50)
        assert result.endswith("...")

    def test_no_heading_returns_body_only(self):
        """Section without ## heading returns body lines only."""
        section = "Just body text\nMore text"
        result = summarize_section(section)
        assert "Just body text" in result

    def test_skips_separator_lines(self):
        """--- lines are skipped."""
        section = "## Title\n\n---\nActual content"
        result = summarize_section(section)
        assert "---" not in result
        assert "Actual content" in result


class TestMissionSummaryCLI:
    """Tests for __main__ CLI entry point (lines 84-95)."""

    def test_cli_prints_summary(self, tmp_path, monkeypatch):
        from tests._helpers import run_module; import io, contextlib
        today = date.today().strftime("%Y-%m-%d")
        journal_dir = tmp_path / "journal" / today
        journal_dir.mkdir(parents=True)
        (journal_dir / "proj.md").write_text("## Session\n\nWork done.")

        monkeypatch.setattr("sys.argv", ["mission_summary.py", str(tmp_path), "proj"])
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            run_module("app.mission_summary", run_name="__main__")
        assert "Work done" in f.getvalue()

    def test_cli_with_max_chars(self, tmp_path, monkeypatch):
        from tests._helpers import run_module; import io, contextlib
        today = date.today().strftime("%Y-%m-%d")
        journal_dir = tmp_path / "journal" / today
        journal_dir.mkdir(parents=True)
        (journal_dir / "proj.md").write_text("## Session\n\n" + "x " * 500)

        monkeypatch.setattr("sys.argv", ["mission_summary.py", str(tmp_path), "proj", "50"])
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            run_module("app.mission_summary", run_name="__main__")
        output = f.getvalue().strip()
        assert len(output) < 200

    def test_cli_no_args(self, monkeypatch):
        from tests._helpers import run_module
        monkeypatch.setattr("sys.argv", ["mission_summary.py"])
        with pytest.raises(SystemExit) as exc_info:
            run_module("app.mission_summary", run_name="__main__")
        assert exc_info.value.code == 1

    def test_cli_no_output_when_empty(self, tmp_path, monkeypatch):
        from tests._helpers import run_module; import io, contextlib
        monkeypatch.setattr("sys.argv", ["mission_summary.py", str(tmp_path), "proj"])
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            run_module("app.mission_summary", run_name="__main__")
        assert f.getvalue().strip() == ""
