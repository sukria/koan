"""Tests for app/skills.py — SKILL.md parsing, registry, and skill execution."""

import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.skills import (
    Skill,
    SkillCommand,
    SkillContext,
    SkillRegistry,
    _parse_inline_list,
    _parse_yaml_lite,
    build_registry,
    execute_skill,
    get_default_skills_dir,
    parse_skill_md,
)


# ---------------------------------------------------------------------------
# _parse_inline_list
# ---------------------------------------------------------------------------

class TestParseInlineList:
    def test_empty_brackets(self):
        assert _parse_inline_list("[]") == []

    def test_single_item(self):
        assert _parse_inline_list("[foo]") == ["foo"]

    def test_multiple_items(self):
        assert _parse_inline_list("[a, b, c]") == ["a", "b", "c"]

    def test_quoted_items(self):
        assert _parse_inline_list('["a", "b"]') == ["a", "b"]

    def test_no_brackets(self):
        assert _parse_inline_list("a, b") == ["a", "b"]


# ---------------------------------------------------------------------------
# _parse_yaml_lite
# ---------------------------------------------------------------------------

class TestParseYamlLite:
    def test_simple_key_value(self):
        result = _parse_yaml_lite("name: test\ndescription: A test skill")
        assert result["name"] == "test"
        assert result["description"] == "A test skill"

    def test_inline_list(self):
        result = _parse_yaml_lite("aliases: [a, b, c]")
        assert result["aliases"] == ["a", "b", "c"]

    def test_commands_block(self):
        yaml = textwrap.dedent("""\
            name: status
            commands:
              - name: status
                description: Quick status
                aliases: [st]
              - name: ping
                description: Check liveness
        """)
        result = _parse_yaml_lite(yaml)
        assert result["name"] == "status"
        assert len(result["commands"]) == 2
        assert result["commands"][0]["name"] == "status"
        assert result["commands"][0]["description"] == "Quick status"
        assert result["commands"][0]["aliases"] == ["st"]
        assert result["commands"][1]["name"] == "ping"

    def test_empty_string(self):
        assert _parse_yaml_lite("") == {}

    def test_comments_ignored(self):
        result = _parse_yaml_lite("# comment\nname: test")
        assert result["name"] == "test"


# ---------------------------------------------------------------------------
# parse_skill_md
# ---------------------------------------------------------------------------

class TestParseSkillMd:
    def test_valid_skill(self, tmp_path):
        skill_dir = tmp_path / "koan" / "status"
        skill_dir.mkdir(parents=True)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(textwrap.dedent("""\
            ---
            name: status
            scope: koan
            description: Show status
            version: 1.0.0
            commands:
              - name: status
                description: Quick status
                aliases: [st]
              - name: ping
                description: Check liveness
            ---

            This is the prompt body.
        """))

        skill = parse_skill_md(skill_md)
        assert skill is not None
        assert skill.name == "status"
        assert skill.scope == "koan"
        assert skill.description == "Show status"
        assert skill.version == "1.0.0"
        assert len(skill.commands) == 2
        assert skill.commands[0].name == "status"
        assert skill.commands[0].aliases == ["st"]
        assert skill.commands[1].name == "ping"
        assert skill.prompt_body == "This is the prompt body."
        assert skill.qualified_name == "koan.status"

    def test_no_frontmatter(self, tmp_path):
        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("Just some text without frontmatter")
        assert parse_skill_md(skill_md) is None

    def test_no_name(self, tmp_path):
        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("---\ndescription: test\n---\nbody")
        assert parse_skill_md(skill_md) is None

    def test_nonexistent_file(self, tmp_path):
        assert parse_skill_md(tmp_path / "nonexistent.md") is None

    def test_handler_path_resolved(self, tmp_path):
        skill_dir = tmp_path / "koan" / "test"
        skill_dir.mkdir(parents=True)
        (skill_dir / "handler.py").write_text("def handle(ctx): return 'ok'")
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("---\nname: test\nhandler: handler.py\n---\nbody")

        skill = parse_skill_md(skill_md)
        assert skill is not None
        assert skill.has_handler()
        assert skill.handler_path == skill_dir / "handler.py"

    def test_handler_missing(self, tmp_path):
        skill_dir = tmp_path / "koan" / "test"
        skill_dir.mkdir(parents=True)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("---\nname: test\nhandler: handler.py\n---\nbody")

        skill = parse_skill_md(skill_md)
        assert skill is not None
        assert not skill.has_handler()

    def test_scope_inferred_from_parent(self, tmp_path):
        skill_dir = tmp_path / "myproject" / "myskill"
        skill_dir.mkdir(parents=True)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("---\nname: myskill\n---\nbody")

        skill = parse_skill_md(skill_md)
        assert skill is not None
        assert skill.scope == "myproject"


# ---------------------------------------------------------------------------
# SkillRegistry
# ---------------------------------------------------------------------------

class TestSkillRegistry:
    def _make_skill_tree(self, tmp_path):
        """Create a skills directory with 2 scopes and 3 skills."""
        # koan/status
        status_dir = tmp_path / "koan" / "status"
        status_dir.mkdir(parents=True)
        (status_dir / "SKILL.md").write_text(textwrap.dedent("""\
            ---
            name: status
            scope: koan
            description: Show status
            commands:
              - name: status
                description: Quick status
                aliases: [st]
              - name: ping
                description: Check liveness
            ---
        """))

        # koan/verbose
        verbose_dir = tmp_path / "koan" / "verbose"
        verbose_dir.mkdir(parents=True)
        (verbose_dir / "SKILL.md").write_text(textwrap.dedent("""\
            ---
            name: verbose
            scope: koan
            description: Toggle verbose mode
            commands:
              - name: verbose
                description: Enable verbose
              - name: silent
                description: Disable verbose
            ---
        """))

        # myproject/deploy
        deploy_dir = tmp_path / "myproject" / "deploy"
        deploy_dir.mkdir(parents=True)
        (deploy_dir / "SKILL.md").write_text(textwrap.dedent("""\
            ---
            name: deploy
            scope: myproject
            description: Deploy to staging
            commands:
              - name: deploy
                description: Deploy
            ---
        """))

        return tmp_path

    def test_discover_skills(self, tmp_path):
        skills_dir = self._make_skill_tree(tmp_path)
        registry = SkillRegistry(skills_dir)

        assert len(registry) == 3
        assert "koan.status" in registry
        assert "koan.verbose" in registry
        assert "myproject.deploy" in registry

    def test_get_skill(self, tmp_path):
        registry = SkillRegistry(self._make_skill_tree(tmp_path))
        skill = registry.get("koan", "status")
        assert skill is not None
        assert skill.name == "status"

    def test_get_nonexistent(self, tmp_path):
        registry = SkillRegistry(self._make_skill_tree(tmp_path))
        assert registry.get("koan", "nonexistent") is None

    def test_find_by_command(self, tmp_path):
        registry = SkillRegistry(self._make_skill_tree(tmp_path))
        skill = registry.find_by_command("ping")
        assert skill is not None
        assert skill.name == "status"

    def test_find_by_alias(self, tmp_path):
        registry = SkillRegistry(self._make_skill_tree(tmp_path))
        skill = registry.find_by_command("st")
        assert skill is not None
        assert skill.name == "status"

    def test_find_unknown_command(self, tmp_path):
        registry = SkillRegistry(self._make_skill_tree(tmp_path))
        assert registry.find_by_command("unknown") is None

    def test_list_all(self, tmp_path):
        registry = SkillRegistry(self._make_skill_tree(tmp_path))
        skills = registry.list_all()
        assert len(skills) == 3

    def test_list_by_scope(self, tmp_path):
        registry = SkillRegistry(self._make_skill_tree(tmp_path))
        koan_skills = registry.list_by_scope("koan")
        assert len(koan_skills) == 2
        names = {s.name for s in koan_skills}
        assert names == {"status", "verbose"}

    def test_scopes(self, tmp_path):
        registry = SkillRegistry(self._make_skill_tree(tmp_path))
        assert registry.scopes() == ["koan", "myproject"]

    def test_empty_dir(self, tmp_path):
        registry = SkillRegistry(tmp_path)
        assert len(registry) == 0

    def test_none_dir(self):
        registry = SkillRegistry(None)
        assert len(registry) == 0

    def test_get_by_qualified_name(self, tmp_path):
        registry = SkillRegistry(self._make_skill_tree(tmp_path))
        skill = registry.get_by_qualified_name("koan.verbose")
        assert skill is not None
        assert skill.name == "verbose"


# ---------------------------------------------------------------------------
# Skill execution
# ---------------------------------------------------------------------------

class TestExecuteSkill:
    def test_handler_based_skill(self, tmp_path):
        handler_dir = tmp_path / "koan" / "test"
        handler_dir.mkdir(parents=True)
        (handler_dir / "handler.py").write_text(
            "def handle(ctx): return f'Hello {ctx.args}'"
        )

        skill = Skill(
            name="test",
            scope="koan",
            handler_path=handler_dir / "handler.py",
            skill_dir=handler_dir,
        )

        ctx = SkillContext(
            koan_root=tmp_path,
            instance_dir=tmp_path,
            args="world",
        )

        result = execute_skill(skill, ctx)
        assert result == "Hello world"

    def test_prompt_based_skill(self, tmp_path):
        skill = Skill(
            name="test",
            scope="koan",
            prompt_body="This is the prompt for Claude",
        )

        ctx = SkillContext(
            koan_root=tmp_path,
            instance_dir=tmp_path,
        )

        result = execute_skill(skill, ctx)
        assert result == "This is the prompt for Claude"

    def test_handler_error_returns_message(self, tmp_path):
        handler_dir = tmp_path / "koan" / "broken"
        handler_dir.mkdir(parents=True)
        (handler_dir / "handler.py").write_text(
            "def handle(ctx): raise ValueError('boom')"
        )

        skill = Skill(
            name="broken",
            scope="koan",
            handler_path=handler_dir / "handler.py",
            skill_dir=handler_dir,
        )

        ctx = SkillContext(koan_root=tmp_path, instance_dir=tmp_path)
        result = execute_skill(skill, ctx)
        assert "boom" in result

    def test_no_handler_no_prompt(self, tmp_path):
        skill = Skill(name="empty", scope="koan")
        ctx = SkillContext(koan_root=tmp_path, instance_dir=tmp_path)
        assert execute_skill(skill, ctx) is None

    def test_handler_missing_handle_function(self, tmp_path):
        handler_dir = tmp_path / "koan" / "nohandle"
        handler_dir.mkdir(parents=True)
        (handler_dir / "handler.py").write_text("x = 42")

        skill = Skill(
            name="nohandle",
            scope="koan",
            handler_path=handler_dir / "handler.py",
            skill_dir=handler_dir,
        )

        ctx = SkillContext(koan_root=tmp_path, instance_dir=tmp_path)
        assert execute_skill(skill, ctx) is None


# ---------------------------------------------------------------------------
# Default skills directory
# ---------------------------------------------------------------------------

class TestDefaultSkillsDir:
    def test_default_dir_exists(self):
        skills_dir = get_default_skills_dir()
        assert skills_dir.exists()
        assert skills_dir.is_dir()

    def test_core_scope_exists(self):
        skills_dir = get_default_skills_dir()
        assert (skills_dir / "core").is_dir()


# ---------------------------------------------------------------------------
# build_registry
# ---------------------------------------------------------------------------

class TestBuildRegistry:
    def test_loads_default_skills(self):
        registry = build_registry()
        assert len(registry) > 0
        assert "core.status" in registry

    def test_with_extra_dirs(self, tmp_path):
        # Create extra skill in a custom dir
        extra_dir = tmp_path / "custom" / "myskill"
        extra_dir.mkdir(parents=True)
        (extra_dir / "SKILL.md").write_text(textwrap.dedent("""\
            ---
            name: myskill
            scope: custom
            description: A custom skill
            commands:
              - name: myskill
                description: Do something
            ---
        """))

        registry = build_registry(extra_dirs=[tmp_path])
        assert "custom.myskill" in registry

    def test_extra_nonexistent_dir(self, tmp_path):
        # Should not crash on nonexistent dirs
        registry = build_registry(extra_dirs=[tmp_path / "nonexistent"])
        assert len(registry) > 0  # Still has defaults


# ---------------------------------------------------------------------------
# SkillContext
# ---------------------------------------------------------------------------

class TestSkillContext:
    def test_defaults(self, tmp_path):
        ctx = SkillContext(koan_root=tmp_path, instance_dir=tmp_path)
        assert ctx.command_name == ""
        assert ctx.args == ""
        assert ctx.send_message is None

    def test_with_send_message(self, tmp_path):
        mock_send = MagicMock()
        ctx = SkillContext(
            koan_root=tmp_path,
            instance_dir=tmp_path,
            send_message=mock_send,
        )
        ctx.send_message("test")
        mock_send.assert_called_once_with("test")


# ---------------------------------------------------------------------------
# Skill dataclass
# ---------------------------------------------------------------------------

class TestSkill:
    def test_qualified_name(self):
        skill = Skill(name="status", scope="koan")
        assert skill.qualified_name == "koan.status"

    def test_has_handler_no_path(self):
        skill = Skill(name="test", scope="koan")
        assert not skill.has_handler()

    def test_has_handler_nonexistent_path(self, tmp_path):
        skill = Skill(name="test", scope="koan", handler_path=tmp_path / "nope.py")
        assert not skill.has_handler()

    def test_has_handler_exists(self, tmp_path):
        handler = tmp_path / "handler.py"
        handler.write_text("def handle(ctx): pass")
        skill = Skill(name="test", scope="koan", handler_path=handler)
        assert skill.has_handler()
