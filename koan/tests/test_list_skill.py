"""Tests for the /list core skill — mission listing."""

import re
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from app.skills import SkillContext


# ---------------------------------------------------------------------------
# Handler tests (direct handler invocation)
# ---------------------------------------------------------------------------

class TestListHandler:
    """Test the list skill handler directly."""

    def _make_ctx(self, tmp_path, missions_content=None):
        """Create a SkillContext with optional missions.md."""
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir(exist_ok=True)
        if missions_content is not None:
            (instance_dir / "missions.md").write_text(missions_content)
        return SkillContext(
            koan_root=tmp_path,
            instance_dir=instance_dir,
            command_name="list",
        )

    def test_no_missions_file(self, tmp_path):
        from skills.core.list.handler import handle

        ctx = self._make_ctx(tmp_path)
        result = handle(ctx)
        assert "No missions file" in result

    def test_empty_missions(self, tmp_path):
        from skills.core.list.handler import handle

        ctx = self._make_ctx(tmp_path, "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n")
        result = handle(ctx)
        assert "No missions pending or in progress" in result

    def test_pending_missions(self, tmp_path):
        from skills.core.list.handler import handle

        missions = textwrap.dedent("""\
            # Missions

            ## Pending

            - fix the login bug
            - add dark mode
            - refactor auth module

            ## In Progress

            ## Done
        """)
        ctx = self._make_ctx(tmp_path, missions)
        result = handle(ctx)
        assert "PENDING" in result
        assert "1. fix the login bug" in result
        assert "2. add dark mode" in result
        assert "3. refactor auth module" in result
        assert "IN PROGRESS" not in result

    def test_in_progress_missions(self, tmp_path):
        from skills.core.list.handler import handle

        missions = textwrap.dedent("""\
            # Missions

            ## Pending

            ## In Progress

            - implement new feature

            ## Done
        """)
        ctx = self._make_ctx(tmp_path, missions)
        result = handle(ctx)
        assert "IN PROGRESS" in result
        assert "1. implement new feature" in result
        assert "PENDING" not in result

    def test_both_sections(self, tmp_path):
        from skills.core.list.handler import handle

        missions = textwrap.dedent("""\
            # Missions

            ## Pending

            - fix bug A
            - fix bug B

            ## In Progress

            - working on feature X

            ## Done

            - done task
        """)
        ctx = self._make_ctx(tmp_path, missions)
        result = handle(ctx)
        assert "IN PROGRESS" in result
        assert "1. working on feature X" in result
        assert "PENDING" in result
        assert "1. fix bug A" in result
        assert "2. fix bug B" in result
        # IN PROGRESS should appear before PENDING
        assert result.index("IN PROGRESS") < result.index("PENDING")

    def test_project_tags_displayed(self, tmp_path):
        from skills.core.list.handler import handle

        missions = textwrap.dedent("""\
            # Missions

            ## Pending

            - [project:koan] fix the parser
            - [project:webapp] add CSRF

            ## In Progress

            ## Done
        """)
        ctx = self._make_ctx(tmp_path, missions)
        result = handle(ctx)
        assert "[koan]" in result
        assert "[webapp]" in result
        assert "fix the parser" in result
        assert "add CSRF" in result
        # No raw tag format in output
        assert "[project:" not in result

    def test_long_missions_truncated(self, tmp_path):
        from skills.core.list.handler import handle

        long_mission = "- " + "x" * 200
        missions = f"# Missions\n\n## Pending\n\n{long_mission}\n\n## In Progress\n\n## Done\n"
        ctx = self._make_ctx(tmp_path, missions)
        result = handle(ctx)
        assert "..." in result
        # Should be truncated to ~120 chars
        for line in result.split("\n"):
            if line.strip().startswith("1."):
                assert len(line.strip()) <= 130  # 120 + numbering prefix

    def test_french_section_headers(self, tmp_path):
        from skills.core.list.handler import handle

        missions = textwrap.dedent("""\
            # Missions

            ## En attente

            - mission en français

            ## En cours

            - tâche active

            ## Terminées
        """)
        ctx = self._make_ctx(tmp_path, missions)
        result = handle(ctx)
        assert "IN PROGRESS" in result
        assert "PENDING" in result


# ---------------------------------------------------------------------------
# _clean_mission helper
# ---------------------------------------------------------------------------

class TestCleanMission:
    def test_strip_dash_prefix(self):
        from app.missions import clean_mission_display
        assert clean_mission_display("- fix the bug") == "fix the bug"

    def test_strip_project_tag(self):
        from app.missions import clean_mission_display
        result = clean_mission_display("- [project:koan] fix parser")
        assert result == "[koan] fix parser"

    def test_strip_projet_tag(self):
        from app.missions import clean_mission_display
        result = clean_mission_display("- [projet:webapp] add feature")
        assert result == "[webapp] add feature"

    def test_no_tag(self):
        from app.missions import clean_mission_display
        assert clean_mission_display("- simple task") == "simple task"

    def test_truncation(self):
        from app.missions import clean_mission_display
        long = "- " + "a" * 200
        result = clean_mission_display(long)
        assert result.endswith("...")
        assert len(result) == 120


# ---------------------------------------------------------------------------
# Integration: command routing via awake.py
# ---------------------------------------------------------------------------

class TestListCommandRouting:
    """Test that /list, /queue, /ls route to the list skill via awake."""

    @patch("app.awake.send_telegram")
    def test_list_routes_via_skill(self, mock_send, tmp_path):
        from app.awake import handle_command

        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n## Pending\n\n- test mission\n\n## In Progress\n\n## Done\n"
        )
        with patch("app.awake.KOAN_ROOT", tmp_path), \
             patch("app.awake.INSTANCE_DIR", tmp_path), \
             patch("app.awake.MISSIONS_FILE", missions_file):
            handle_command("/list")
        mock_send.assert_called_once()
        output = mock_send.call_args[0][0]
        assert "PENDING" in output
        assert "test mission" in output

    @patch("app.awake.send_telegram")
    def test_queue_alias_routes_to_list(self, mock_send, tmp_path):
        from app.awake import handle_command

        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n## Pending\n\n- queued task\n\n## In Progress\n\n## Done\n"
        )
        with patch("app.awake.KOAN_ROOT", tmp_path), \
             patch("app.awake.INSTANCE_DIR", tmp_path), \
             patch("app.awake.MISSIONS_FILE", missions_file):
            handle_command("/queue")
        mock_send.assert_called_once()
        assert "queued task" in mock_send.call_args[0][0]

    @patch("app.awake.send_telegram")
    def test_ls_alias_routes_to_list(self, mock_send, tmp_path):
        from app.awake import handle_command

        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n## Pending\n\n- ls task\n\n## In Progress\n\n## Done\n"
        )
        with patch("app.awake.KOAN_ROOT", tmp_path), \
             patch("app.awake.INSTANCE_DIR", tmp_path), \
             patch("app.awake.MISSIONS_FILE", missions_file):
            handle_command("/ls")
        mock_send.assert_called_once()
        assert "ls task" in mock_send.call_args[0][0]

    @patch("app.awake.send_telegram")
    def test_list_empty_queue(self, mock_send, tmp_path):
        from app.awake import handle_command

        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n"
        )
        with patch("app.awake.KOAN_ROOT", tmp_path), \
             patch("app.awake.INSTANCE_DIR", tmp_path), \
             patch("app.awake.MISSIONS_FILE", missions_file):
            handle_command("/list")
        mock_send.assert_called_once()
        assert "No missions" in mock_send.call_args[0][0]

    @patch("app.awake.send_telegram")
    def test_list_appears_in_help(self, mock_send, tmp_path):
        """Verify /list is included in /help output via skill discovery."""
        from app.awake import handle_command

        with patch("app.awake.KOAN_ROOT", tmp_path), \
             patch("app.awake.INSTANCE_DIR", tmp_path):
            handle_command("/help")
        mock_send.assert_called_once()
        help_text = mock_send.call_args[0][0]
        assert "/list" in help_text
        assert "missions" in help_text.lower()
