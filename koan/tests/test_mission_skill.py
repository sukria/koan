"""Tests for the /mission core skill — mission creation with --now flag."""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.skills import SkillContext


def _make_ctx(args, instance_dir):
    """Create a minimal SkillContext for testing."""
    ctx = MagicMock(spec=SkillContext)
    ctx.args = args
    ctx.command_name = "mission"
    ctx.instance_dir = instance_dir
    return ctx


# ---------------------------------------------------------------------------
# /mission handler — --now flag integration
# ---------------------------------------------------------------------------

class TestMissionHandlerNowFlag:
    """Test that --now flag is parsed and passed as urgent=True."""

    @patch("app.utils.get_known_projects", return_value=[("koan", "/path")])
    @patch("app.utils.detect_project_from_text", return_value=(None, "fix the bug"))
    def test_normal_mission_queued_at_bottom(self, _det, _proj, tmp_path):
        """Without --now, mission goes to bottom of queue."""
        missions = tmp_path / "missions.md"
        missions.write_text(
            "# Missions\n\n## Pending\n\n- existing task\n\n## In Progress\n\n## Done\n"
        )

        from skills.core.mission.handler import handle
        ctx = _make_ctx("fix the bug", tmp_path)
        result = handle(ctx)

        assert "Mission received" in result
        content = missions.read_text()
        lines = [l for l in content.splitlines() if l.startswith("- ")]
        assert lines[0] == "- existing task"
        assert lines[1] == "- fix the bug"

    @patch("app.utils.get_known_projects", return_value=[("koan", "/path")])
    @patch("app.utils.detect_project_from_text", return_value=(None, "fix the bug"))
    def test_now_flag_queues_at_top(self, _det, _proj, tmp_path):
        """With --now, mission goes to top of queue."""
        missions = tmp_path / "missions.md"
        missions.write_text(
            "# Missions\n\n## Pending\n\n- existing task\n\n## In Progress\n\n## Done\n"
        )

        from skills.core.mission.handler import handle
        ctx = _make_ctx("--now fix the bug", tmp_path)
        result = handle(ctx)

        assert "priority" in result
        content = missions.read_text()
        lines = [l for l in content.splitlines() if l.startswith("- ")]
        assert lines[0] == "- fix the bug"
        assert lines[1] == "- existing task"

    @patch("app.utils.get_known_projects", return_value=[("koan", "/path")])
    @patch("app.utils.detect_project_from_text", return_value=(None, "fix --now the bug"))
    def test_now_flag_in_middle_of_first_five(self, _det, _proj, tmp_path):
        """--now in first 5 words still works."""
        missions = tmp_path / "missions.md"
        missions.write_text(
            "# Missions\n\n## Pending\n\n- existing task\n\n## In Progress\n\n## Done\n"
        )

        from skills.core.mission.handler import handle
        ctx = _make_ctx("fix --now the bug", tmp_path)
        result = handle(ctx)

        assert "priority" in result
        content = missions.read_text()
        lines = [l for l in content.splitlines() if l.startswith("- ")]
        assert lines[0] == "- fix the bug"

    @patch("app.utils.get_known_projects", return_value=[("koan", "/path")])
    @patch("app.utils.detect_project_from_text", return_value=(None, "do something"))
    def test_now_flag_stripped_from_mission_text(self, _det, _proj, tmp_path):
        """--now should not appear in the mission entry."""
        missions = tmp_path / "missions.md"
        missions.write_text("# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n")

        from skills.core.mission.handler import handle
        ctx = _make_ctx("--now do something", tmp_path)
        result = handle(ctx)

        content = missions.read_text()
        assert "--now" not in content
        assert "- do something" in content

    def test_empty_args_shows_usage(self, tmp_path):
        from skills.core.mission.handler import handle
        ctx = _make_ctx("", tmp_path)
        result = handle(ctx)
        assert "Usage:" in result
        assert "--now" in result

    @patch("app.utils.get_known_projects", return_value=[("koan", "/path")])
    def test_now_with_project_tag(self, _proj, tmp_path):
        """--now works with explicit [project:name] tag."""
        missions = tmp_path / "missions.md"
        missions.write_text(
            "# Missions\n\n## Pending\n\n- old task\n\n## In Progress\n\n## Done\n"
        )

        from skills.core.mission.handler import handle
        ctx = _make_ctx("--now [project:koan] fix auth", tmp_path)
        result = handle(ctx)

        assert "priority" in result
        assert "project: koan" in result
        content = missions.read_text()
        lines = [l for l in content.splitlines() if l.startswith("- ")]
        assert lines[0] == "- [project:koan] fix auth"
        assert lines[1] == "- old task"

    @patch("app.utils.get_known_projects", return_value=[("koan", "/path")])
    @patch("app.utils.detect_project_from_text")
    def test_now_with_project_autodetect(self, mock_detect, _proj, tmp_path):
        """--now works with auto-detected project name."""
        # After --now is stripped, "koan fix auth" is passed to detect_project_from_text
        mock_detect.return_value = ("koan", "fix auth")

        missions = tmp_path / "missions.md"
        missions.write_text(
            "# Missions\n\n## Pending\n\n- old task\n\n## In Progress\n\n## Done\n"
        )

        from skills.core.mission.handler import handle
        ctx = _make_ctx("--now koan fix auth", tmp_path)
        result = handle(ctx)

        assert "priority" in result
        content = missions.read_text()
        lines = [l for l in content.splitlines() if l.startswith("- ")]
        assert lines[0] == "- [project:koan] fix auth"


# ---------------------------------------------------------------------------
# awake.py — handle_mission with --now
# ---------------------------------------------------------------------------

class TestAwakeHandleMissionNowFlag:
    """Test handle_mission() in awake.py also respects --now."""

    @patch("app.command_handlers.send_telegram")
    @patch("app.command_handlers.MISSIONS_FILE")
    def test_normal_mission_bottom(self, mock_file, mock_send, tmp_path):
        missions = tmp_path / "missions.md"
        missions.write_text(
            "# Missions\n\n## Pending\n\n- existing\n\n## In Progress\n\n## Done\n"
        )
        mock_file.__fspath__ = lambda s: str(missions)
        # Patch MISSIONS_FILE to be the real path
        with patch("app.command_handlers.MISSIONS_FILE", missions):
            from app.command_handlers import handle_mission
            handle_mission("fix something")

        content = missions.read_text()
        lines = [l for l in content.splitlines() if l.startswith("- ")]
        assert lines[0] == "- existing"
        assert lines[1] == "- fix something"

    @patch("app.command_handlers.send_telegram")
    def test_now_flag_top(self, mock_send, tmp_path):
        missions = tmp_path / "missions.md"
        missions.write_text(
            "# Missions\n\n## Pending\n\n- existing\n\n## In Progress\n\n## Done\n"
        )
        with patch("app.command_handlers.MISSIONS_FILE", missions):
            from app.command_handlers import handle_mission
            handle_mission("--now fix something")

        content = missions.read_text()
        lines = [l for l in content.splitlines() if l.startswith("- ")]
        assert lines[0] == "- fix something"
        assert lines[1] == "- existing"

    @patch("app.command_handlers.send_telegram")
    def test_now_flag_stripped_from_text(self, mock_send, tmp_path):
        missions = tmp_path / "missions.md"
        missions.write_text("# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n")
        with patch("app.command_handlers.MISSIONS_FILE", missions):
            from app.command_handlers import handle_mission
            handle_mission("--now deploy hotfix")

        content = missions.read_text()
        assert "--now" not in content
        assert "- deploy hotfix" in content

    @patch("app.command_handlers.send_telegram")
    def test_ack_message_includes_priority(self, mock_send, tmp_path):
        missions = tmp_path / "missions.md"
        missions.write_text("# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n")
        with patch("app.command_handlers.MISSIONS_FILE", missions):
            from app.command_handlers import handle_mission
            handle_mission("--now urgent fix")

        ack = mock_send.call_args[0][0]
        assert "priority" in ack
