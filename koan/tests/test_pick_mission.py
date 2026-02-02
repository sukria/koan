"""Tests for pick_mission.py — intelligent mission picker."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.pick_mission import parse_picker_output, build_prompt, pick_mission, fallback_extract


class TestParsePickerOutput:
    def test_mission_line(self):
        project, title = parse_picker_output("mission:koan:fixer les warnings dans les tests")
        assert project == "koan"
        assert title == "fixer les warnings dans les tests"

    def test_mission_with_backticks(self):
        project, title = parse_picker_output("`mission:anantys:implement dashboard`")
        assert project == "anantys"
        assert title == "implement dashboard"

    def test_autonomous(self):
        project, title = parse_picker_output("autonomous")
        assert project is None
        assert title is None

    def test_multiline_picks_first_valid(self):
        raw = "Let me think...\nmission:koan:fix tests\nsome other text"
        project, title = parse_picker_output(raw)
        assert project == "koan"
        assert title == "fix tests"

    def test_empty_string(self):
        project, title = parse_picker_output("")
        assert project is None
        assert title is None

    def test_garbage_input(self):
        project, title = parse_picker_output("this is not a valid response at all")
        assert project is None
        assert title is None

    def test_mission_with_colon_in_title(self):
        project, title = parse_picker_output("mission:koan:fix: the bug in module X")
        assert project == "koan"
        assert title == "fix: the bug in module X"


class TestBuildPrompt:
    def test_placeholders_replaced(self):
        prompt = build_prompt(
            missions_content="## En attente\n- task 1",
            projects_str="koan:/path;anantys:/path2",
            run_num="3",
            max_runs="20",
            autonomous_mode="implement",
            last_project="koan",
        )
        assert "koan:/path;anantys:/path2" in prompt
        assert "{PROJECTS}" not in prompt
        assert "{RUN_NUM}" not in prompt
        assert "{LAST_PROJECT}" not in prompt
        assert "## En attente" in prompt


class TestFallbackExtract:
    def test_with_inline_tag(self, tmp_path):
        missions = tmp_path / "missions.md"
        missions.write_text("# Missions\n\n## En attente\n\n- [project:koan] fix tests\n\n## En cours\n\n## Terminées\n")
        project, title = fallback_extract(missions, "koan:/path")
        assert project == "koan"
        assert title == "fix tests"

    def test_without_tag(self, tmp_path):
        missions = tmp_path / "missions.md"
        missions.write_text("# Missions\n\n## En attente\n\n- fix tests\n\n## En cours\n\n## Terminées\n")
        project, title = fallback_extract(missions, "koan:/path;anantys:/path2")
        assert project == "koan"
        assert title == "fix tests"

    def test_no_pending(self, tmp_path):
        missions = tmp_path / "missions.md"
        missions.write_text("# Missions\n\n## En attente\n\n## En cours\n\n## Terminées\n")
        project, title = fallback_extract(missions, "koan:/path")
        assert project is None

    def test_with_subheader_tag(self, tmp_path):
        missions = tmp_path / "missions.md"
        missions.write_text(
            "# Missions\n\n## En attente\n\n"
            "### projet:anantys-back\n\n"
            "### project:koan\n"
            "- fix rotation bug\n\n"
            "## En cours\n\n## Terminées\n"
        )
        # fallback_extract uses extract_next_pending which now respects sub-headers
        # but fallback_extract doesn't pass project_name, so it returns first item
        project, title = fallback_extract(missions, "koan:/path")
        assert project == "koan"
        assert title == "fix rotation bug"

    def test_missing_file(self, tmp_path):
        missions = tmp_path / "missions.md"
        project, title = fallback_extract(missions, "koan:/path")
        assert project is None


class TestPickMission:
    """Integration tests — mock the Claude subprocess call."""

    def _mock_claude_success(self, mission_line):
        """Create a mock subprocess.run that returns a Claude JSON response."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({"result": mission_line})
        return mock_result

    @patch("app.pick_mission.call_claude")
    def test_picks_mission_from_claude(self, mock_claude, tmp_path):
        missions = tmp_path / "missions.md"
        missions.write_text(
            "# Missions\n\n## En attente\n\n### project:koan\n- fix tests\n\n"
            "### project:anantys\n- implement dashboard\n\n## En cours\n\n## Terminées\n"
        )
        mock_claude.return_value = "mission:anantys:implement dashboard"

        result = pick_mission(str(tmp_path), "koan:/p1;anantys:/p2", "2", "implement", "koan")
        assert result == "anantys:implement dashboard"

    @patch("app.pick_mission.call_claude")
    def test_autonomous_when_no_missions(self, mock_claude, tmp_path):
        missions = tmp_path / "missions.md"
        missions.write_text("# Missions\n\n## En attente\n\n## En cours\n\n## Terminées\n")

        result = pick_mission(str(tmp_path), "koan:/p1", "1", "implement")
        # count_pending returns 0, so we never call Claude
        assert result == ""
        mock_claude.assert_not_called()

    @patch("app.pick_mission.call_claude")
    def test_fallback_on_claude_failure(self, mock_claude, tmp_path):
        missions = tmp_path / "missions.md"
        missions.write_text(
            "# Missions\n\n## En attente\n\n- [project:koan] fix tests\n\n## En cours\n\n## Terminées\n"
        )
        mock_claude.return_value = ""  # Claude failed

        result = pick_mission(str(tmp_path), "koan:/p1", "1", "implement")
        assert result == "koan:fix tests"

    @patch("app.pick_mission.call_claude")
    def test_missing_missions_file(self, mock_claude, tmp_path):
        result = pick_mission(str(tmp_path), "koan:/p1", "1", "implement")
        assert result == ""
        mock_claude.assert_not_called()
