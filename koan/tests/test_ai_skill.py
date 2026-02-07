"""Tests for the /ai core skill — async AI exploration via mission queue."""

import importlib.util
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.skills import SkillContext, build_registry


# ---------------------------------------------------------------------------
# Import handler module
# ---------------------------------------------------------------------------

HANDLER_PATH = Path(__file__).parent.parent / "skills" / "core" / "ai" / "handler.py"


def _load_handler():
    spec = importlib.util.spec_from_file_location("ai_handler", str(HANDLER_PATH))
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
    # Create minimal missions.md
    missions_md = instance_dir / "missions.md"
    missions_md.write_text("## En attente\n\n## En cours\n\n## Terminées\n")
    return SkillContext(
        koan_root=tmp_path,
        instance_dir=instance_dir,
        command_name="ai",
        args="",
        send_message=MagicMock(),
    )


# ---------------------------------------------------------------------------
# Registry / routing
# ---------------------------------------------------------------------------

class TestAiSkillRegistry:
    def test_ai_skill_not_worker(self):
        """AI skill should NOT have worker=true (it queues, not runs)."""
        registry = build_registry()
        skill = registry.get("core", "ai")
        assert skill is not None
        assert skill.worker is False

    def test_ai_found_by_command(self):
        """Registry should find ai skill by /ai."""
        registry = build_registry()
        skill = registry.find_by_command("ai")
        assert skill is not None
        assert skill.name == "ai"

    def test_ia_alias_routes_to_ai(self):
        """Registry should find ai skill by /ia alias."""
        registry = build_registry()
        skill = registry.find_by_command("ia")
        assert skill is not None
        assert skill.name == "ai"

    def test_magic_no_longer_has_ai_alias(self):
        """The /ai alias should not route to /magic anymore."""
        registry = build_registry()
        magic = registry.get("core", "magic")
        assert magic is not None
        # /ai should NOT be in magic's aliases
        for cmd in magic.commands:
            assert "ai" not in cmd.aliases

    def test_ai_and_magic_are_separate_skills(self):
        """AI and magic should be distinct skills."""
        registry = build_registry()
        ai = registry.find_by_command("ai")
        magic = registry.find_by_command("magic")
        assert ai is not None
        assert magic is not None
        assert ai.name == "ai"
        assert magic.name == "magic"
        assert ai.name != magic.name


# ---------------------------------------------------------------------------
# SKILL.md — structure validation
# ---------------------------------------------------------------------------

class TestSkillMd:
    def test_skill_md_parses(self):
        from app.skills import parse_skill_md
        skill = parse_skill_md(
            Path(__file__).parent.parent / "skills" / "core" / "ai" / "SKILL.md"
        )
        assert skill is not None
        assert skill.name == "ai"
        assert skill.scope == "core"
        assert skill.worker is False

    def test_skill_has_ia_alias(self):
        from app.skills import parse_skill_md
        skill = parse_skill_md(
            Path(__file__).parent.parent / "skills" / "core" / "ai" / "SKILL.md"
        )
        assert "ia" in skill.commands[0].aliases

    def test_skill_handler_exists(self):
        assert HANDLER_PATH.exists()

    def test_prompt_file_exists(self):
        prompt_path = (
            Path(__file__).parent.parent
            / "skills" / "core" / "ai" / "prompts" / "ai-explore.md"
        )
        assert prompt_path.exists()


# ---------------------------------------------------------------------------
# _get_projects
# ---------------------------------------------------------------------------

class TestGetProjects:
    @patch("app.utils.get_known_projects")
    def test_returns_projects_from_yaml(self, mock_get, handler, ctx, tmp_path):
        mock_get.return_value = [("foo", str(tmp_path)), ("bar", "/nonexistent")]
        projects = handler._get_projects(ctx)
        assert len(projects) == 1
        assert projects[0][0] == "foo"

    @patch("app.utils.get_known_projects", side_effect=Exception("no yaml"))
    def test_fallback_to_project_path(self, mock_get, handler, ctx, tmp_path, monkeypatch):
        monkeypatch.setenv("KOAN_PROJECT_PATH", str(tmp_path))
        projects = handler._get_projects(ctx)
        assert len(projects) == 1
        assert projects[0][0] == tmp_path.name

    @patch("app.utils.get_known_projects", side_effect=Exception("no yaml"))
    def test_empty_when_nothing_configured(self, mock_get, handler, ctx, monkeypatch):
        monkeypatch.setenv("KOAN_PROJECT_PATH", "")
        projects = handler._get_projects(ctx)
        assert projects == []


# ---------------------------------------------------------------------------
# _resolve_project
# ---------------------------------------------------------------------------

class TestResolveProject:
    def test_no_target_picks_random(self, handler):
        projects = [("koan", "/koan"), ("web", "/web")]
        name, path = handler._resolve_project(projects, "")
        assert name in ("koan", "web")

    def test_target_matches_exact(self, handler):
        projects = [("koan", "/koan"), ("web", "/web")]
        name, path = handler._resolve_project(projects, "koan")
        assert name == "koan"
        assert path == "/koan"

    def test_target_case_insensitive(self, handler):
        projects = [("Koan", "/koan")]
        name, path = handler._resolve_project(projects, "koan")
        assert name == "Koan"

    def test_target_not_found(self, handler):
        projects = [("koan", "/koan")]
        name, path = handler._resolve_project(projects, "unknown")
        assert name is None
        assert path is None


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

    @patch("app.utils.get_known_projects")
    @patch("app.utils.insert_pending_mission")
    def test_queues_mission_for_random_project(
        self, mock_insert, mock_get, handler, ctx, tmp_path
    ):
        mock_get.return_value = [("test", str(tmp_path))]
        result = handler.handle(ctx)
        assert "queued" in result.lower()
        assert "test" in result
        mock_insert.assert_called_once()

    @patch("app.utils.get_known_projects")
    @patch("app.utils.insert_pending_mission")
    def test_queues_mission_for_specific_project(
        self, mock_insert, mock_get, handler, ctx, tmp_path
    ):
        mock_get.return_value = [("koan", str(tmp_path)), ("web", str(tmp_path))]
        ctx.args = "koan"
        result = handler.handle(ctx)
        assert "queued" in result.lower()
        assert "koan" in result
        mock_insert.assert_called_once()
        mission_entry = mock_insert.call_args[0][1]
        assert "[project:koan]" in mission_entry

    @patch("app.utils.get_known_projects")
    def test_unknown_project_returns_error(self, mock_get, handler, ctx, tmp_path):
        mock_get.return_value = [("koan", str(tmp_path))]
        ctx.args = "unknown"
        result = handler.handle(ctx)
        assert "Unknown project" in result
        assert "koan" in result

    @patch("app.utils.get_known_projects")
    @patch("app.utils.insert_pending_mission")
    def test_mission_entry_has_project_tag(
        self, mock_insert, mock_get, handler, ctx, tmp_path
    ):
        mock_get.return_value = [("myproject", str(tmp_path))]
        handler.handle(ctx)
        entry = mock_insert.call_args[0][1]
        assert entry.startswith("[project:myproject]")

    @patch("app.utils.get_known_projects")
    @patch("app.utils.insert_pending_mission")
    def test_mission_entry_contains_prompt(
        self, mock_insert, mock_get, handler, ctx, tmp_path
    ):
        """Mission text should contain the AI exploration prompt."""
        mock_get.return_value = [("test", str(tmp_path))]
        handler.handle(ctx)
        entry = mock_insert.call_args[0][1]
        # The prompt should be embedded in the mission text
        assert "AI exploration" in entry
        assert "test" in entry

    @patch("app.utils.get_known_projects")
    @patch("app.utils.insert_pending_mission")
    def test_missions_path_uses_instance_dir(
        self, mock_insert, mock_get, handler, ctx, tmp_path
    ):
        mock_get.return_value = [("test", str(tmp_path))]
        handler.handle(ctx)
        missions_path = mock_insert.call_args[0][0]
        assert missions_path == ctx.instance_dir / "missions.md"

    @patch("app.utils.get_known_projects")
    @patch("app.utils.insert_pending_mission")
    def test_ia_alias_works(self, mock_insert, mock_get, handler, ctx, tmp_path):
        """The /ia alias should work the same as /ai."""
        mock_get.return_value = [("test", str(tmp_path))]
        ctx.command_name = "ia"
        result = handler.handle(ctx)
        assert "queued" in result.lower()
        mock_insert.assert_called_once()

    @patch("app.utils.get_known_projects")
    @patch("app.utils.insert_pending_mission")
    def test_prompt_includes_project_name(
        self, mock_insert, mock_get, handler, ctx, tmp_path
    ):
        """The queued mission prompt should mention the project name."""
        mock_get.return_value = [("myapp", str(tmp_path))]
        handler.handle(ctx)
        entry = mock_insert.call_args[0][1]
        assert "myapp" in entry

    @patch("app.utils.get_known_projects")
    @patch("app.utils.insert_pending_mission")
    def test_prompt_includes_exploration_instructions(
        self, mock_insert, mock_get, handler, ctx, tmp_path
    ):
        """The queued mission should include exploration instructions from the prompt."""
        mock_get.return_value = [("test", str(tmp_path))]
        handler.handle(ctx)
        entry = mock_insert.call_args[0][1]
        # Key phrases from the prompt template
        assert "3-5" in entry or "improvement" in entry.lower() or "suggest" in entry.lower()

    @patch("app.utils.get_known_projects")
    @patch("app.utils.insert_pending_mission")
    def test_does_not_call_claude_directly(
        self, mock_insert, mock_get, handler, ctx, tmp_path
    ):
        """The handler should queue, not call Claude subprocess."""
        mock_get.return_value = [("test", str(tmp_path))]
        with patch("subprocess.run") as mock_subprocess:
            handler.handle(ctx)
            # subprocess.run may be called for git activity gathering, but NOT for claude
            for call in mock_subprocess.call_args_list:
                args = call[0][0] if call[0] else call[1].get("args", [])
                assert "claude" not in str(args)


# ---------------------------------------------------------------------------
# Dispatch behavior via awake.py
# ---------------------------------------------------------------------------

class TestDispatch:
    @patch("app.awake._run_in_worker")
    @patch("app.awake.send_telegram")
    def test_ai_not_dispatched_via_worker(self, mock_send, mock_worker):
        """handle_command('/ai') should NOT use worker thread (not a worker skill)."""
        from app.awake import handle_command
        handle_command("/ai")
        mock_worker.assert_not_called()
        # Should have sent the result directly (or via send_telegram)
        mock_send.assert_called()

    @patch("app.awake._run_in_worker")
    @patch("app.awake.send_telegram")
    def test_ia_not_dispatched_via_worker(self, mock_send, mock_worker):
        """handle_command('/ia') should NOT use worker thread."""
        from app.awake import handle_command
        handle_command("/ia")
        mock_worker.assert_not_called()
        mock_send.assert_called()

    @patch("app.awake._run_in_worker")
    def test_magic_still_uses_worker(self, mock_worker):
        """handle_command('/magic') should still use worker thread."""
        from app.awake import handle_command
        handle_command("/magic")
        mock_worker.assert_called_once()


# ---------------------------------------------------------------------------
# /help integration
# ---------------------------------------------------------------------------

class TestHelpIntegration:
    @patch("app.awake.send_telegram")
    def test_help_mentions_ai(self, mock_send):
        from app.awake import _handle_help
        _handle_help()
        msg = mock_send.call_args[0][0]
        assert "/ai" in msg or "ai" in msg.lower()
