"""Tests for the /projects core skill — list configured projects."""

from pathlib import Path
from unittest.mock import patch

import pytest

from app.skills import SkillContext


# ---------------------------------------------------------------------------
# Handler tests (direct handler invocation)
# ---------------------------------------------------------------------------

class TestProjectsHandler:
    """Test the projects skill handler directly."""

    def _make_ctx(self, tmp_path, args=""):
        """Create a SkillContext for /projects."""
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir(exist_ok=True)
        return SkillContext(
            koan_root=tmp_path,
            instance_dir=instance_dir,
            command_name="projects",
            args=args,
        )

    @patch("app.utils.get_known_projects", return_value=[])
    def test_no_projects(self, mock_projects, tmp_path):
        from skills.core.projects.handler import handle

        ctx = self._make_ctx(tmp_path)
        result = handle(ctx)
        assert "No projects configured" in result

    @patch(
        "app.utils.get_known_projects",
        return_value=[("koan", "/home/user/koan"), ("webapp", "/home/user/webapp")],
    )
    def test_multiple_projects(self, mock_projects, tmp_path):
        from skills.core.projects.handler import handle

        ctx = self._make_ctx(tmp_path)
        result = handle(ctx)
        assert "Configured projects:" in result
        assert "koan" in result
        assert "webapp" in result
        assert "/home/user/koan" in result
        assert "/home/user/webapp" in result

    @patch(
        "app.utils.get_known_projects",
        return_value=[("myproject", "/path/to/project")],
    )
    def test_single_project(self, mock_projects, tmp_path):
        from skills.core.projects.handler import handle

        ctx = self._make_ctx(tmp_path)
        result = handle(ctx)
        assert "Configured projects:" in result
        assert "myproject" in result
        assert "/path/to/project" in result

    @patch(
        "app.utils.get_known_projects",
        return_value=[("alpha", "/a"), ("beta", "/b"), ("gamma", "/g")],
    )
    def test_bullet_format(self, mock_projects, tmp_path):
        from skills.core.projects.handler import handle

        ctx = self._make_ctx(tmp_path)
        result = handle(ctx)
        lines = result.strip().split("\n")
        assert lines[0] == "Configured projects:"
        assert lines[1].strip().startswith("- alpha")
        assert lines[2].strip().startswith("- beta")
        assert lines[3].strip().startswith("- gamma")

    @patch(
        "app.utils.get_known_projects",
        return_value=[("koan", "/home/koan")],
    )
    def test_shows_path_in_parens(self, mock_projects, tmp_path):
        from skills.core.projects.handler import handle

        ctx = self._make_ctx(tmp_path)
        result = handle(ctx)
        assert "koan (/home/koan)" in result

    @patch("app.utils.get_known_projects", return_value=[])
    def test_args_ignored(self, mock_projects, tmp_path):
        """Extra args don't break the handler."""
        from skills.core.projects.handler import handle

        ctx = self._make_ctx(tmp_path, args="extra stuff")
        result = handle(ctx)
        assert "No projects configured" in result


# ---------------------------------------------------------------------------
# Integration: command routing via awake.py
# ---------------------------------------------------------------------------

class TestProjectsCommandRouting:
    """Test that /projects and /proj route to the skill via awake."""

    @patch("app.command_handlers.send_telegram")
    @patch(
        "app.utils.get_known_projects",
        return_value=[("koan", "/home/koan")],
    )
    def test_projects_routes_via_skill(self, mock_projects, mock_send, tmp_path):
        from app.command_handlers import handle_command

        with patch("app.command_handlers.KOAN_ROOT", tmp_path), \
             patch("app.command_handlers.INSTANCE_DIR", tmp_path):
            handle_command("/projects")
        mock_send.assert_called_once()
        output = mock_send.call_args[0][0]
        assert "koan" in output

    @patch("app.command_handlers.send_telegram")
    @patch(
        "app.utils.get_known_projects",
        return_value=[("koan", "/home/koan")],
    )
    def test_proj_alias_routes(self, mock_projects, mock_send, tmp_path):
        from app.command_handlers import handle_command

        with patch("app.command_handlers.KOAN_ROOT", tmp_path), \
             patch("app.command_handlers.INSTANCE_DIR", tmp_path):
            handle_command("/proj")
        mock_send.assert_called_once()
        output = mock_send.call_args[0][0]
        assert "koan" in output

    @patch("app.command_handlers.send_telegram")
    def test_projects_appears_in_help(self, mock_send, tmp_path):
        """Verify /projects is included in /help output via skill discovery."""
        from app.command_handlers import handle_command

        with patch("app.command_handlers.KOAN_ROOT", tmp_path), \
             patch("app.command_handlers.INSTANCE_DIR", tmp_path):
            handle_command("/help")
        mock_send.assert_called_once()
        help_text = mock_send.call_args[0][0]
        assert "/projects" in help_text
