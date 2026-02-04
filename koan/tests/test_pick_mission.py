"""Tests for pick_mission.py — intelligent mission picker."""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.pick_mission import parse_picker_output, build_prompt, pick_mission, fallback_extract, call_claude


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
        """With 3+ missions and 2+ projects, Claude picker is called."""
        missions = tmp_path / "missions.md"
        missions.write_text(
            "# Missions\n\n## En attente\n\n"
            "- [project:koan] fix tests\n"
            "- [project:koan] refactor utils\n"
            "- [project:anantys] implement dashboard\n\n"
            "## En cours\n\n## Terminées\n"
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

    @patch("app.pick_mission.call_claude")
    def test_smart_picker_skips_claude_single_mission(self, mock_claude, tmp_path):
        """When there's only 1-2 pending missions, use fast fallback."""
        missions = tmp_path / "missions.md"
        missions.write_text(
            "# Missions\n\n## En attente\n\n- [project:koan] fix tests\n\n## En cours\n\n## Terminées\n"
        )
        result = pick_mission(str(tmp_path), "koan:/p1;anantys:/p2", "1", "implement")
        assert result == "koan:fix tests"
        mock_claude.assert_not_called()

    @patch("app.pick_mission.call_claude")
    def test_smart_picker_skips_claude_single_project(self, mock_claude, tmp_path):
        """When there's only 1 project, use fast fallback even with many missions."""
        missions = tmp_path / "missions.md"
        missions.write_text(
            "# Missions\n\n## En attente\n\n"
            "- fix tests\n- add feature\n- refactor module\n\n"
            "## En cours\n\n## Terminées\n"
        )
        result = pick_mission(str(tmp_path), "koan:/p1", "1", "implement")
        assert result == "koan:fix tests"
        mock_claude.assert_not_called()

    @patch("app.pick_mission.call_claude")
    def test_smart_picker_calls_claude_complex_case(self, mock_claude, tmp_path):
        """When 3+ missions AND 2+ projects, Claude picker is used."""
        missions = tmp_path / "missions.md"
        missions.write_text(
            "# Missions\n\n## En attente\n\n"
            "- [project:koan] fix tests\n"
            "- [project:anantys] add feature\n"
            "- [project:koan] refactor module\n\n"
            "## En cours\n\n## Terminées\n"
        )
        mock_claude.return_value = "mission:anantys:add feature"
        result = pick_mission(str(tmp_path), "koan:/p1;anantys:/p2", "1", "implement")
        assert result == "anantys:add feature"
        mock_claude.assert_called_once()


class TestCallClaude:
    @patch("app.pick_mission.get_model_config", return_value={"lightweight": "haiku"})
    @patch("app.pick_mission.build_claude_flags", return_value=["--model", "haiku"])
    @patch("app.pick_mission.subprocess.run")
    def test_successful_json_result(self, mock_run, mock_flags, mock_models):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"result": "mission:koan:fix tests"}),
        )
        result = call_claude("test prompt")
        assert result == "mission:koan:fix tests"

    @patch("app.pick_mission.get_model_config", return_value={"lightweight": "haiku"})
    @patch("app.pick_mission.build_claude_flags", return_value=[])
    @patch("app.pick_mission.subprocess.run")
    def test_nonzero_exit_code(self, mock_run, mock_flags, mock_models):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        result = call_claude("test prompt")
        assert result == ""

    @patch("app.pick_mission.get_model_config", return_value={"lightweight": "haiku"})
    @patch("app.pick_mission.build_claude_flags", return_value=[])
    @patch("app.pick_mission.subprocess.run")
    def test_json_with_content_field(self, mock_run, mock_flags, mock_models):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"content": "mission:koan:audit"}),
        )
        result = call_claude("test prompt")
        assert result == "mission:koan:audit"

    @patch("app.pick_mission.get_model_config", return_value={"lightweight": "haiku"})
    @patch("app.pick_mission.build_claude_flags", return_value=[])
    @patch("app.pick_mission.subprocess.run")
    def test_non_json_output(self, mock_run, mock_flags, mock_models):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="mission:koan:fix tests",
        )
        result = call_claude("test prompt")
        assert result == "mission:koan:fix tests"


class TestParsePickerOutputEdgeCases:
    def test_mission_with_empty_project(self):
        project, title = parse_picker_output("mission::fix tests")
        assert project is None

    def test_mission_with_empty_title(self):
        project, title = parse_picker_output("mission:koan:")
        assert project is None

    def test_mission_only_two_parts(self):
        project, title = parse_picker_output("mission:koan")
        assert project is None


class TestPickMissionCLI:
    """CLI tests — pick_mission.py has no main() function, just __main__ guard.
    We test the missing-args path via runpy, and the happy paths are already
    covered by TestPickMission integration tests."""

    def test_cli_exit_on_missing_args(self):
        with patch.object(sys, "argv", ["pick_mission.py"]):
            with pytest.raises(SystemExit) as exc_info:
                from tests._helpers import run_module
                run_module("app.pick_mission", run_name="__main__")
            assert exc_info.value.code == 1
