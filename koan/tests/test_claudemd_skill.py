"""Tests for the /claude.md core skill — handler, SKILL.md, and registry integration."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.skills import SkillContext


# ---------------------------------------------------------------------------
# Import handler
# ---------------------------------------------------------------------------

HANDLER_PATH = Path(__file__).parent.parent / "skills" / "core" / "claudemd" / "handler.py"


def _load_handler():
    spec = importlib.util.spec_from_file_location("claudemd_handler", str(HANDLER_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def handler():
    return _load_handler()


@pytest.fixture
def ctx(tmp_path):
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    missions_md = instance_dir / "missions.md"
    missions_md.write_text("## En attente\n\n## En cours\n\n## Terminées\n")
    return SkillContext(
        koan_root=tmp_path,
        instance_dir=instance_dir,
        command_name="claude.md",
        args="",
        send_message=MagicMock(),
    )


# ---------------------------------------------------------------------------
# handle() — usage / routing
# ---------------------------------------------------------------------------

class TestHandleRouting:
    def test_no_args_returns_usage(self, handler, ctx):
        result = handler.handle(ctx)
        assert "Usage:" in result
        assert "/claude.md" in result

    def test_usage_mentions_project_name(self, handler, ctx):
        result = handler.handle(ctx)
        assert "project-name" in result.lower() or "project" in result.lower()

    def test_unknown_project_returns_error(self, handler, ctx):
        ctx.args = "unknown_project"
        with patch("app.utils.get_known_projects", return_value=[("koan", "/path")]):
            result = handler.handle(ctx)
            assert "not found" in result.lower()
            assert "koan" in result

    def test_empty_projects_list(self, handler, ctx):
        ctx.args = "anything"
        with patch("app.utils.get_known_projects", return_value=[]):
            result = handler.handle(ctx)
            assert "not found" in result.lower()
            assert "none" in result.lower()


# ---------------------------------------------------------------------------
# handle() — mission queuing
# ---------------------------------------------------------------------------

class TestMissionQueuing:
    def test_valid_project_queues_mission(self, handler, ctx):
        ctx.args = "koan"
        with patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            result = handler.handle(ctx)
            assert "queued" in result.lower()
            assert "koan" in result
            mock_insert.assert_called_once()

    def test_mission_entry_format(self, handler, ctx):
        ctx.args = "koan"
        with patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            handler.handle(ctx)
            entry = mock_insert.call_args[0][1]
            assert entry.startswith("- [project:koan]")
            assert "Refresh CLAUDE.md" in entry
            assert "python3 -m app.claudemd_refresh" in entry
            assert "/home/koan" in entry
            assert "--project-name koan" in entry

    def test_case_insensitive_project_match(self, handler, ctx):
        ctx.args = "KOAN"
        with patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            result = handler.handle(ctx)
            assert "queued" in result.lower()
            entry = mock_insert.call_args[0][1]
            assert "[project:koan]" in entry

    def test_extra_args_after_project_name_ignored(self, handler, ctx):
        ctx.args = "koan some extra text"
        with patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            result = handler.handle(ctx)
            assert "queued" in result.lower()
            mock_insert.assert_called_once()

    def test_missions_path_uses_instance_dir(self, handler, ctx):
        ctx.args = "koan"
        with patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            handler.handle(ctx)
            missions_path = mock_insert.call_args[0][0]
            assert missions_path == ctx.instance_dir / "missions.md"

    def test_koan_root_in_cli_command(self, handler, ctx):
        ctx.args = "koan"
        with patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            handler.handle(ctx)
            entry = mock_insert.call_args[0][1]
            # CLI command should reference koan_root for the venv
            assert str(ctx.koan_root) in entry

    def test_multiple_projects_selects_correct_one(self, handler, ctx):
        ctx.args = "web"
        projects = [("koan", "/home/koan"), ("web", "/home/web"), ("api", "/home/api")]
        with patch("app.utils.get_known_projects", return_value=projects), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            result = handler.handle(ctx)
            assert "queued" in result.lower()
            entry = mock_insert.call_args[0][1]
            assert "[project:web]" in entry
            assert "/home/web" in entry


# ---------------------------------------------------------------------------
# SKILL.md — structure validation
# ---------------------------------------------------------------------------

class TestSkillMd:
    def test_skill_md_parses(self):
        from app.skills import parse_skill_md
        skill_md = Path(__file__).parent.parent / "skills" / "core" / "claudemd" / "SKILL.md"
        skill = parse_skill_md(skill_md)
        assert skill is not None
        assert skill.name == "claudemd"
        assert skill.scope == "core"
        assert skill.worker is False

    def test_skill_has_command(self):
        from app.skills import parse_skill_md
        skill_md = Path(__file__).parent.parent / "skills" / "core" / "claudemd" / "SKILL.md"
        skill = parse_skill_md(skill_md)
        assert len(skill.commands) == 1
        assert skill.commands[0].name == "claude.md"

    def test_skill_has_alias(self):
        from app.skills import parse_skill_md
        skill_md = Path(__file__).parent.parent / "skills" / "core" / "claudemd" / "SKILL.md"
        skill = parse_skill_md(skill_md)
        assert "claudemd" in skill.commands[0].aliases

    def test_skill_registered_in_registry(self):
        from app.skills import build_registry
        registry = build_registry()
        skill = registry.find_by_command("claude.md")
        assert skill is not None
        assert skill.name == "claudemd"

    def test_alias_registered_in_registry(self):
        from app.skills import build_registry
        registry = build_registry()
        skill = registry.find_by_command("claudemd")
        assert skill is not None
        assert skill.name == "claudemd"

    def test_claude_alias_in_skill(self):
        from app.skills import parse_skill_md
        skill_md = Path(__file__).parent.parent / "skills" / "core" / "claudemd" / "SKILL.md"
        skill = parse_skill_md(skill_md)
        assert "claude" in skill.commands[0].aliases

    def test_claude_alias_registered_in_registry(self):
        from app.skills import build_registry
        registry = build_registry()
        skill = registry.find_by_command("claude")
        assert skill is not None
        assert skill.name == "claudemd"

    def test_skill_handler_exists(self):
        assert HANDLER_PATH.exists()

    def test_prompt_template_exists(self):
        prompt_path = (
            Path(__file__).parent.parent
            / "skills" / "core" / "claudemd" / "prompts" / "refresh-claude-md.md"
        )
        assert prompt_path.exists()
        content = prompt_path.read_text()
        assert "{MODE}" in content
        assert "{PROJECT_PATH}" in content
        assert "{GIT_CONTEXT}" in content
