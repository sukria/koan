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

    def test_worker_default_false(self):
        skill = Skill(name="test", scope="koan")
        assert not skill.worker

    def test_worker_explicit_true(self):
        skill = Skill(name="test", scope="koan", worker=True)
        assert skill.worker


# ---------------------------------------------------------------------------
# Worker field parsing
# ---------------------------------------------------------------------------

class TestWorkerField:
    """Tests for the 'worker: true' field in SKILL.md."""

    def test_worker_true_parsed(self, tmp_path):
        skill_dir = tmp_path / "core" / "blocking"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(textwrap.dedent("""\
            ---
            name: blocking
            scope: core
            description: A blocking skill
            worker: true
            commands:
              - name: blocking
                description: Does blocking work
            ---
        """))
        skill = parse_skill_md(skill_dir / "SKILL.md")
        assert skill is not None
        assert skill.worker is True

    def test_worker_false_parsed(self, tmp_path):
        skill_dir = tmp_path / "core" / "fast"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(textwrap.dedent("""\
            ---
            name: fast
            scope: core
            description: A fast skill
            worker: false
            commands:
              - name: fast
                description: Does fast work
            ---
        """))
        skill = parse_skill_md(skill_dir / "SKILL.md")
        assert skill is not None
        assert skill.worker is False

    def test_worker_absent_defaults_false(self, tmp_path):
        skill_dir = tmp_path / "core" / "normal"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(textwrap.dedent("""\
            ---
            name: normal
            scope: core
            description: A normal skill
            commands:
              - name: normal
                description: Does normal work
            ---
        """))
        skill = parse_skill_md(skill_dir / "SKILL.md")
        assert skill is not None
        assert skill.worker is False

    def test_sparring_skill_is_worker(self):
        """Sparring core skill should have worker=true."""
        registry = build_registry()
        skill = registry.get("core", "sparring")
        assert skill is not None
        assert skill.worker is True

    def test_pr_skill_is_worker(self):
        """PR core skill should have worker=true."""
        registry = build_registry()
        skill = registry.get("core", "pr")
        assert skill is not None
        assert skill.worker is True

    def test_status_skill_not_worker(self):
        """Status core skill should NOT be a worker (reads files only)."""
        registry = build_registry()
        skill = registry.get("core", "status")
        assert skill is not None
        assert skill.worker is False


# ---------------------------------------------------------------------------
# Scoped command resolution
# ---------------------------------------------------------------------------

class TestResolveScopedCommand:
    def _make_registry(self, tmp_path):
        """Create a registry with skills in multiple scopes."""
        # core/status
        status_dir = tmp_path / "core" / "status"
        status_dir.mkdir(parents=True)
        (status_dir / "SKILL.md").write_text(textwrap.dedent("""\
            ---
            name: status
            scope: core
            description: Show status
            commands:
              - name: status
                description: Quick status
              - name: ping
                description: Check liveness
            ---
        """))

        # myproject/review
        review_dir = tmp_path / "myproject" / "review"
        review_dir.mkdir(parents=True)
        (review_dir / "SKILL.md").write_text(textwrap.dedent("""\
            ---
            name: review
            scope: myproject
            description: Code review
            commands:
              - name: review
                description: Run code review
            ---
        """))

        return SkillRegistry(tmp_path)

    def test_resolve_scope_skill(self, tmp_path):
        registry = self._make_registry(tmp_path)
        result = registry.resolve_scoped_command("myproject.review")
        assert result is not None
        skill, cmd, args = result
        assert skill.name == "review"
        assert cmd == "review"
        assert args == ""

    def test_resolve_scope_skill_with_args(self, tmp_path):
        registry = self._make_registry(tmp_path)
        result = registry.resolve_scoped_command("myproject.review some args here")
        assert result is not None
        skill, cmd, args = result
        assert skill.name == "review"
        assert cmd == "review"
        assert args == "some args here"

    def test_resolve_scope_skill_subcommand(self, tmp_path):
        registry = self._make_registry(tmp_path)
        result = registry.resolve_scoped_command("core.status.ping")
        assert result is not None
        skill, cmd, args = result
        assert skill.name == "status"
        assert cmd == "ping"

    def test_resolve_nonexistent_scope(self, tmp_path):
        registry = self._make_registry(tmp_path)
        assert registry.resolve_scoped_command("unknown.review") is None

    def test_resolve_nonexistent_skill(self, tmp_path):
        registry = self._make_registry(tmp_path)
        assert registry.resolve_scoped_command("core.unknown") is None

    def test_resolve_single_segment_returns_none(self, tmp_path):
        registry = self._make_registry(tmp_path)
        assert registry.resolve_scoped_command("status") is None


# ---------------------------------------------------------------------------
# PR skill handler
# ---------------------------------------------------------------------------

class TestPrSkillHandler:
    """Tests for the /pr core skill handler."""

    def _load_handler(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "pr_handler",
            str(Path(__file__).parent.parent / "skills" / "core" / "pr" / "handler.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_no_args_returns_usage(self, tmp_path):
        mod = self._load_handler()
        ctx = SkillContext(koan_root=tmp_path, instance_dir=tmp_path, args="")
        result = mod.handle(ctx)
        assert "Usage" in result
        assert "/pr" in result

    def test_invalid_url_returns_error(self, tmp_path):
        mod = self._load_handler()
        ctx = SkillContext(koan_root=tmp_path, instance_dir=tmp_path, args="not-a-url")
        result = mod.handle(ctx)
        assert "No valid GitHub PR URL" in result

    def test_pr_skill_registered(self):
        """PR skill should be discoverable in the default registry."""
        registry = build_registry()
        assert "core.pr" in registry
        skill = registry.get("core", "pr")
        assert skill.worker is True

    def test_pr_command_findable(self):
        """The 'pr' command should be resolvable via find_by_command."""
        registry = build_registry()
        skill = registry.find_by_command("pr")
        assert skill is not None
        assert skill.name == "pr"


# ---------------------------------------------------------------------------
# Default registry includes all core skills
# ---------------------------------------------------------------------------

class TestCoreSkillsComplete:
    """Verify all expected core skills are registered."""

    def test_all_core_skills_present(self):
        registry = build_registry()
        expected = {"status", "journal", "sparring", "reflect",
                    "verbose", "chat", "mission", "language", "pr"}
        actual = {s.name for s in registry.list_by_scope("core")}
        assert expected.issubset(actual), f"Missing: {expected - actual}"

    def test_all_core_skills_have_handlers(self):
        """Every core skill should have a handler.py."""
        registry = build_registry()
        for skill in registry.list_by_scope("core"):
            assert skill.has_handler(), f"Skill {skill.name} missing handler.py"
