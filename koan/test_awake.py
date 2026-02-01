"""Tests for awake.py — message classification, mission handling, project parsing."""

import re
from unittest.mock import patch, MagicMock

import pytest

from awake import (
    is_mission,
    is_command,
    parse_project,
    handle_mission,
    _build_status,
    MISSIONS_FILE,
)


# ---------------------------------------------------------------------------
# is_mission
# ---------------------------------------------------------------------------

class TestIsMission:
    """Test mission detection heuristics."""

    def test_explicit_mission_prefix(self):
        assert is_mission("mission: audit the backend") is True

    def test_explicit_mission_prefix_with_space(self):
        assert is_mission("mission : fix the login bug") is True

    def test_imperative_verb_start(self):
        assert is_mission("implement dark mode") is True
        assert is_mission("fix the authentication bug") is True
        assert is_mission("audit the security layer") is True
        assert is_mission("create a new endpoint") is True
        assert is_mission("add tests for awake.py") is True
        assert is_mission("review the PR") is True
        assert is_mission("analyze the logs") is True
        assert is_mission("explore the codebase") is True
        assert is_mission("build the dashboard") is True
        assert is_mission("write a migration script") is True
        assert is_mission("run the test suite") is True
        assert is_mission("deploy to staging") is True
        assert is_mission("test the new feature") is True
        assert is_mission("refactor the auth module") is True

    def test_long_message_with_verb(self):
        long_text = "implement " + "x " * 150  # >200 chars
        assert is_mission(long_text) is True

    def test_short_question_not_mission(self):
        assert is_mission("how are you?") is False
        assert is_mission("what's the status?") is False

    def test_greeting_not_mission(self):
        assert is_mission("hello") is False
        assert is_mission("good morning") is False

    def test_case_insensitive(self):
        assert is_mission("Implement dark mode") is True
        assert is_mission("MISSION: do something") is True

    def test_empty_string(self):
        assert is_mission("") is False


# ---------------------------------------------------------------------------
# is_command
# ---------------------------------------------------------------------------

class TestIsCommand:
    def test_slash_commands(self):
        assert is_command("/stop") is True
        assert is_command("/status") is True
        assert is_command("/resume") is True

    def test_non_commands(self):
        assert is_command("hello") is False
        assert is_command("fix the bug") is False
        assert is_command("") is False


# ---------------------------------------------------------------------------
# parse_project
# ---------------------------------------------------------------------------

class TestParseProject:
    def test_with_project_tag(self):
        project, text = parse_project("[project:anantys] fix the login")
        assert project == "anantys"
        assert text == "fix the login"

    def test_without_project_tag(self):
        project, text = parse_project("fix the login")
        assert project is None
        assert text == "fix the login"

    def test_project_tag_with_hyphen(self):
        project, text = parse_project("[project:anantys-back] deploy")
        assert project == "anantys-back"
        assert text == "deploy"

    def test_project_tag_with_underscore(self):
        project, text = parse_project("[project:my_project] test")
        assert project == "my_project"
        assert text == "test"

    def test_project_tag_in_middle(self):
        project, text = parse_project("fix [project:koan] the bug")
        assert project == "koan"
        assert text == "fix the bug"

    def test_french_projet_tag(self):
        project, text = parse_project("[projet:anantys] fix the login")
        assert project == "anantys"
        assert text == "fix the login"

    def test_french_projet_tag_in_middle(self):
        project, text = parse_project("fix [projet:koan] the bug")
        assert project == "koan"
        assert text == "fix the bug"


# ---------------------------------------------------------------------------
# handle_mission (integration with filesystem)
# ---------------------------------------------------------------------------

class TestHandleMission:
    @patch("awake.send_telegram")
    @patch("awake.MISSIONS_FILE")
    @patch("awake.INSTANCE_DIR")
    def test_mission_appended_to_pending(self, mock_inst, mock_file, mock_send, tmp_path):
        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n## En attente\n\n(aucune)\n\n## En cours\n\n## Terminées\n"
        )
        mock_file.__class__ = type(missions_file)
        # Directly test the file manipulation logic
        with patch("awake.MISSIONS_FILE", missions_file):
            handle_mission("mission: audit security")

        content = missions_file.read_text()
        assert "- audit security" in content
        mock_send.assert_called_once()

    @patch("awake.send_telegram")
    def test_mission_with_project_tag(self, mock_send, tmp_path):
        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n## En attente\n\n(aucune)\n\n## En cours\n\n"
        )
        with patch("awake.MISSIONS_FILE", missions_file):
            handle_mission("[project:koan] add tests")

        content = missions_file.read_text()
        assert "- [project:koan] add tests" in content


# ---------------------------------------------------------------------------
# _build_status
# ---------------------------------------------------------------------------

class TestBuildStatus:
    @patch("awake.MISSIONS_FILE")
    def test_status_with_french_sections(self, mock_file, tmp_path):
        """_build_status handles French section names from missions.md."""
        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n"
            "## En attente\n\n"
            "- [project:koan] add tests\n"
            "- fix bug\n\n"
            "## En cours\n\n"
            "- [project:koan] doing stuff\n\n"
        )
        with patch("awake.MISSIONS_FILE", missions_file), \
             patch("awake.KOAN_ROOT", tmp_path):
            status = _build_status()

        assert "Kōan Status" in status
        # Missions split by project: koan gets 1 pending + 1 in_progress, default gets 1 pending
        assert "**koan**" in status
        assert "**default**" in status
        assert "In progress: 1" in status

    @patch("awake.MISSIONS_FILE")
    def test_status_empty(self, mock_file, tmp_path):
        missions_file = tmp_path / "missions.md"
        missions_file.write_text("# Missions\n\n## En attente\n\n## En cours\n\n")
        with patch("awake.MISSIONS_FILE", missions_file), \
             patch("awake.KOAN_ROOT", tmp_path):
            status = _build_status()

        assert "Kōan Status" in status
