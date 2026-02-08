"""Tests for extract_mission.py — CLI wrapper around missions.extract_next_pending."""

import pytest
from unittest.mock import patch
from pathlib import Path

from app.extract_mission import extract_next_mission


class TestExtractNextMission:
    def test_returns_first_pending_mission(self, tmp_path):
        """Basic case: returns first pending mission."""
        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n"
            "## Pending\n\n"
            "- Fix the bug\n"
            "- Add feature\n\n"
            "## In Progress\n\n"
            "## Done\n\n"
        )
        result = extract_next_mission(str(missions_file))
        assert result == "- Fix the bug"

    def test_returns_empty_when_no_pending(self, tmp_path):
        """No pending missions returns empty string."""
        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n"
            "## Pending\n\n"
            "## In Progress\n\n"
            "## Done\n\n"
            "- Old task\n"
        )
        result = extract_next_mission(str(missions_file))
        assert result == ""

    def test_returns_empty_when_file_missing(self, tmp_path):
        """Non-existent file returns empty string."""
        result = extract_next_mission(str(tmp_path / "nonexistent.md"))
        assert result == ""

    def test_project_filter_matches_tagged(self, tmp_path):
        """With project filter, returns matching tagged mission."""
        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n"
            "## Pending\n\n"
            "- [project:backend] Deploy fix\n"
            "- [project:koan] Write tests\n\n"
            "## In Progress\n\n"
        )
        result = extract_next_mission(str(missions_file), "koan")
        assert "Write tests" in result

    def test_project_filter_matches_untagged(self, tmp_path):
        """With project filter, untagged missions also match."""
        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n"
            "## Pending\n\n"
            "- Untagged task\n\n"
            "## In Progress\n\n"
        )
        result = extract_next_mission(str(missions_file), "koan")
        assert result == "- Untagged task"

    def test_project_filter_skips_other_project(self, tmp_path):
        """With project filter, skips missions tagged for other projects."""
        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n"
            "## Pending\n\n"
            "- [project:backend] Not for koan\n\n"
            "## In Progress\n\n"
        )
        result = extract_next_mission(str(missions_file), "koan")
        assert result == ""

    def test_english_section_names(self, tmp_path):
        """Works with English section names too."""
        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n"
            "## Pending\n\n"
            "- English task\n\n"
            "## In Progress\n\n"
        )
        result = extract_next_mission(str(missions_file))
        assert result == "- English task"

    def test_does_not_match_in_progress_section(self, tmp_path):
        """Missions in 'In Progress' are NOT returned."""
        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n"
            "## Pending\n\n"
            "## In Progress\n\n"
            "- In progress task\n\n"
            "## Done\n\n"
        )
        result = extract_next_mission(str(missions_file))
        assert result == ""

    def test_empty_file(self, tmp_path):
        """Empty file returns empty string."""
        missions_file = tmp_path / "missions.md"
        missions_file.write_text("")
        result = extract_next_mission(str(missions_file))
        assert result == ""

    def test_projet_french_tag(self, tmp_path):
        """French [projet:X] tag also works."""
        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n"
            "## Pending\n\n"
            "- [projet:koan] French tag task\n\n"
            "## In Progress\n\n"
        )
        result = extract_next_mission(str(missions_file), "koan")
        assert "French tag task" in result


class TestExtractMissionCLI:
    """Tests for __main__ CLI entry point (lines 31-39)."""

    def test_cli_prints_mission(self, tmp_path, monkeypatch):
        from tests._helpers import run_module
        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n## Pending\n\n- CLI task\n\n## In Progress\n\n"
        )
        monkeypatch.setattr("sys.argv", ["extract_mission.py", str(missions_file)])
        # Capture stdout
        import io, contextlib
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            run_module("app.extract_mission", run_name="__main__")
        assert "CLI task" in f.getvalue()

    def test_cli_with_project_filter(self, tmp_path, monkeypatch):
        from tests._helpers import run_module; import io, contextlib
        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n## Pending\n\n- [project:koan] Tagged\n\n## In Progress\n\n"
        )
        monkeypatch.setattr("sys.argv", ["extract_mission.py", str(missions_file), "koan"])
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            run_module("app.extract_mission", run_name="__main__")
        assert "Tagged" in f.getvalue()

    def test_cli_no_args_exits(self, monkeypatch):
        from tests._helpers import run_module
        monkeypatch.setattr("sys.argv", ["extract_mission.py"])
        with pytest.raises(SystemExit) as exc_info:
            run_module("app.extract_mission", run_name="__main__")
        assert exc_info.value.code == 1
