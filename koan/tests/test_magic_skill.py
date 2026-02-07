"""Tests for the /magic core skill — creative project exploration."""

import importlib.util
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.skills import SkillContext, build_registry


# ---------------------------------------------------------------------------
# Import handler module
# ---------------------------------------------------------------------------

HANDLER_PATH = Path(__file__).parent.parent / "skills" / "core" / "magic" / "handler.py"


def _load_handler():
    spec = importlib.util.spec_from_file_location("magic_handler", str(HANDLER_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def handler():
    return _load_handler()


@pytest.fixture
def ctx(tmp_path):
    """Create a basic SkillContext for tests."""
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    return SkillContext(
        koan_root=tmp_path,
        instance_dir=instance_dir,
        command_name="magic",
        args="",
        send_message=MagicMock(),
    )


# ---------------------------------------------------------------------------
# Registry / routing
# ---------------------------------------------------------------------------

class TestMagicSkillRegistry:
    def test_magic_skill_is_worker(self):
        """Magic skill should have worker=true."""
        registry = build_registry()
        skill = registry.get("core", "magic")
        assert skill is not None
        assert skill.worker is True

    def test_magic_found_by_command(self):
        """Registry should find magic skill by /magic."""
        registry = build_registry()
        skill = registry.find_by_command("magic")
        assert skill is not None
        assert skill.name == "magic"

    def test_ai_alias_routes_to_magic(self):
        """Registry should find magic skill by /ai alias."""
        registry = build_registry()
        skill = registry.find_by_command("ai")
        assert skill is not None
        assert skill.name == "magic"

    @patch("app.command_handlers._run_in_worker_cb")
    def test_handle_command_dispatches_magic(self, mock_worker):
        """handle_command('/magic') should dispatch via worker thread."""
        from app.command_handlers import handle_command
        handle_command("/magic")
        mock_worker.assert_called_once()

    @patch("app.command_handlers._run_in_worker_cb")
    def test_handle_command_dispatches_ai(self, mock_worker):
        """handle_command('/ai') should dispatch via worker thread."""
        from app.command_handlers import handle_command
        handle_command("/ai")
        mock_worker.assert_called_once()


# ---------------------------------------------------------------------------
# _get_projects
# ---------------------------------------------------------------------------

class TestGetProjects:
    @patch("app.utils.get_known_projects")
    def test_returns_projects_from_yaml(self, mock_get, handler, ctx, tmp_path):
        """Should use get_known_projects() from projects.yaml."""
        mock_get.return_value = [("foo", str(tmp_path)), ("bar", "/nonexistent")]
        projects = handler._get_projects(ctx)
        # Only foo should pass (tmp_path exists, /nonexistent doesn't)
        assert len(projects) == 1
        assert projects[0][0] == "foo"

    @patch("app.utils.get_known_projects", side_effect=Exception("no yaml"))
    def test_fallback_to_project_path(self, mock_get, handler, ctx, tmp_path, monkeypatch):
        """Falls back to KOAN_PROJECT_PATH when projects.yaml unavailable."""
        monkeypatch.setenv("KOAN_PROJECT_PATH", str(tmp_path))
        projects = handler._get_projects(ctx)
        assert len(projects) == 1
        assert projects[0][0] == tmp_path.name

    @patch("app.utils.get_known_projects", side_effect=Exception("no yaml"))
    def test_empty_when_nothing_configured(self, mock_get, handler, ctx, monkeypatch):
        """Returns empty when no projects configured."""
        monkeypatch.setenv("KOAN_PROJECT_PATH", "")
        projects = handler._get_projects(ctx)
        assert projects == []


# ---------------------------------------------------------------------------
# _gather_git_activity
# ---------------------------------------------------------------------------

class TestGatherGitActivity:
    @patch("subprocess.run")
    def test_includes_recent_commits(self, mock_run, handler):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="abc1234 fix login\ndef5678 add tests",
        )
        result = handler._gather_git_activity("/tmp")
        assert "fix login" in result

    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 10))
    def test_handles_timeout(self, mock_run, handler):
        result = handler._gather_git_activity("/tmp")
        assert "unavailable" in result


# ---------------------------------------------------------------------------
# _gather_project_structure
# ---------------------------------------------------------------------------

class TestGatherProjectStructure:
    def test_lists_dirs_and_files(self, handler, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()
        (tmp_path / "README.md").write_text("hello")
        (tmp_path / ".hidden").write_text("skip")

        result = handler._gather_project_structure(str(tmp_path))
        assert "src/" in result
        assert "tests/" in result
        assert "README.md" in result
        assert ".hidden" not in result

    def test_handles_nonexistent_path(self, handler):
        result = handler._gather_project_structure("/nonexistent/path")
        assert "unavailable" in result.lower()


# ---------------------------------------------------------------------------
# _get_missions_context
# ---------------------------------------------------------------------------

class TestGetMissionsContext:
    def test_returns_in_progress_and_pending(self, handler, tmp_path):
        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n## En attente\n\n- pending task\n\n"
            "## En cours\n\n- active task\n\n## Terminées\n"
        )
        result = handler._get_missions_context(tmp_path)
        assert "active task" in result
        assert "pending task" in result

    def test_returns_no_active_when_empty(self, handler, tmp_path):
        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n## En attente\n\n## En cours\n\n## Terminées\n"
        )
        result = handler._get_missions_context(tmp_path)
        assert "No active" in result

    def test_handles_missing_file(self, handler, tmp_path):
        result = handler._get_missions_context(tmp_path)
        assert "No active" in result


# ---------------------------------------------------------------------------
# handle() — main entry point
# ---------------------------------------------------------------------------

class TestHandle:
    @patch("app.utils.get_known_projects", return_value=[])
    def test_no_projects_returns_message(self, mock_get, handler, ctx, monkeypatch):
        monkeypatch.setenv("KOAN_PROJECT_PATH", "")
        result = handler.handle(ctx)
        assert "No projects" in result

    @patch("subprocess.run")
    @patch("app.utils.get_known_projects")
    def test_calls_claude_with_project(self, mock_get, mock_run, handler, ctx, tmp_path):
        mock_get.return_value = [("test", str(tmp_path))]
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Ideas:\n- Idea 1\n- Idea 2",
        )

        # Create soul.md in instance dir
        (ctx.instance_dir / "soul.md").write_text("test soul")

        result = handler.handle(ctx)
        # Should have sent "Exploring test..."
        ctx.send_message.assert_called_once()
        assert "Exploring test" in ctx.send_message.call_args[0][0]
        # Should return cleaned Claude output
        assert "Idea 1" in result

    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired("claude", 90))
    @patch("app.utils.get_known_projects")
    def test_timeout_returns_message(self, mock_get, mock_run, handler, ctx, tmp_path):
        mock_get.return_value = [("test", str(tmp_path))]
        result = handler.handle(ctx)
        assert "Timeout" in result

    @patch("subprocess.run")
    @patch("app.utils.get_known_projects")
    def test_claude_failure_returns_fallback(self, mock_get, mock_run, handler, ctx, tmp_path):
        mock_get.return_value = [("test", str(tmp_path))]
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="error",
        )
        result = handler.handle(ctx)
        assert "Couldn't generate" in result


# ---------------------------------------------------------------------------
# _clean_response
# ---------------------------------------------------------------------------

class TestCleanResponse:
    def test_strips_markdown(self, handler):
        raw = "## Header\n**bold** and ~~strike~~\n```code```"
        cleaned = handler._clean_response(raw)
        assert "**" not in cleaned
        assert "```" not in cleaned
        assert "##" not in cleaned

    def test_truncates_long_text(self, handler):
        raw = "x" * 3000
        cleaned = handler._clean_response(raw)
        assert len(cleaned) <= 2000
        assert cleaned.endswith("...")


# ---------------------------------------------------------------------------
# /help integration
# ---------------------------------------------------------------------------

class TestHelpIntegration:
    @patch("app.command_handlers.send_telegram")
    def test_help_mentions_magic(self, mock_send):
        from app.command_handlers import _handle_help
        _handle_help()
        msg = mock_send.call_args[0][0]
        assert "/magic" in msg or "magic" in msg.lower()
