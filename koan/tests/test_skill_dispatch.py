"""Tests for skill_dispatch.py — skill mission detection and CLI command building."""

import os
import pytest

from app.skill_dispatch import (
    is_skill_mission,
    parse_skill_mission,
    build_skill_command,
    dispatch_skill_mission,
)


# ---------------------------------------------------------------------------
# is_skill_mission
# ---------------------------------------------------------------------------

class TestIsSkillMission:
    def test_plan_command(self):
        assert is_skill_mission("/plan Add dark mode") is True

    def test_rebase_command(self):
        assert is_skill_mission("/rebase https://github.com/sukria/koan/pull/42") is True

    def test_scoped_command(self):
        assert is_skill_mission("/core.plan Add dark mode") is True

    def test_empty_string(self):
        assert is_skill_mission("") is False

    def test_regular_mission(self):
        assert is_skill_mission("Fix the login bug") is False

    def test_run_command_old_format(self):
        assert is_skill_mission("Plan: stuff — run: `cd /koan && python3 -m ...`") is False

    def test_slash_only(self):
        assert is_skill_mission("/") is False

    def test_whitespace_before_slash(self):
        assert is_skill_mission("  /plan test") is True

    def test_not_at_start(self):
        assert is_skill_mission("Fix /plan bug") is False


# ---------------------------------------------------------------------------
# parse_skill_mission
# ---------------------------------------------------------------------------

class TestParseSkillMission:
    def test_simple_plan(self):
        cmd, args = parse_skill_mission("/plan Add dark mode")
        assert cmd == "plan"
        assert args == "Add dark mode"

    def test_rebase_url(self):
        cmd, args = parse_skill_mission("/rebase https://github.com/sukria/koan/pull/42")
        assert cmd == "rebase"
        assert args == "https://github.com/sukria/koan/pull/42"

    def test_ai_no_args(self):
        cmd, args = parse_skill_mission("/ai")
        assert cmd == "ai"
        assert args == ""

    def test_ai_with_project(self):
        cmd, args = parse_skill_mission("/ai koan")
        assert cmd == "ai"
        assert args == "koan"

    def test_scoped_core(self):
        """core.plan should resolve to just 'plan'."""
        cmd, args = parse_skill_mission("/core.plan Add dark mode")
        assert cmd == "plan"
        assert args == "Add dark mode"

    def test_scoped_external(self):
        """External scoped skills keep full scope."""
        cmd, args = parse_skill_mission("/anantys.review Check code")
        assert cmd == "anantys.review"
        assert args == "Check code"

    def test_claude_md(self):
        cmd, args = parse_skill_mission("/claude.md koan")
        assert cmd == "claude.md"
        assert args == "koan"

    def test_no_slash(self):
        cmd, args = parse_skill_mission("Fix the bug")
        assert cmd == ""
        assert args == "Fix the bug"

    def test_check_with_url(self):
        cmd, args = parse_skill_mission("/check https://github.com/sukria/koan/issues/42")
        assert cmd == "check"
        assert args == "https://github.com/sukria/koan/issues/42"

    def test_recreate_with_url(self):
        cmd, args = parse_skill_mission("/recreate https://github.com/sukria/koan/pull/100")
        assert cmd == "recreate"
        assert args == "https://github.com/sukria/koan/pull/100"


# ---------------------------------------------------------------------------
# build_skill_command
# ---------------------------------------------------------------------------

class TestBuildSkillCommand:
    KOAN_ROOT = "/home/user/koan"
    INSTANCE = "/home/user/koan/instance"
    PROJECT = "myproject"
    PROJECT_PATH = "/home/user/workspace/myproject"

    def _build(self, command, args):
        return build_skill_command(
            command=command,
            args=args,
            project_name=self.PROJECT,
            project_path=self.PROJECT_PATH,
            koan_root=self.KOAN_ROOT,
            instance_dir=self.INSTANCE,
        )

    def test_plan_with_idea(self):
        cmd = self._build("plan", "Add dark mode to the dashboard")
        assert cmd is not None
        assert "-m" in cmd
        assert "app.plan_runner" in cmd
        assert "--project-path" in cmd
        assert self.PROJECT_PATH in cmd
        assert "--idea" in cmd
        assert "Add dark mode to the dashboard" in cmd

    def test_plan_with_issue_url(self):
        url = "https://github.com/sukria/koan/issues/42"
        cmd = self._build("plan", url)
        assert cmd is not None
        assert "--issue-url" in cmd
        assert url in cmd

    def test_rebase(self):
        url = "https://github.com/sukria/koan/pull/42"
        cmd = self._build("rebase", url)
        assert cmd is not None
        assert "app.rebase_pr" in cmd
        assert url in cmd
        assert "--project-path" in cmd

    def test_rebase_no_url(self):
        cmd = self._build("rebase", "just some text")
        assert cmd is None

    def test_recreate(self):
        url = "https://github.com/sukria/koan/pull/100"
        cmd = self._build("recreate", url)
        assert cmd is not None
        assert "app.recreate_pr" in cmd
        assert url in cmd

    def test_recreate_no_url(self):
        cmd = self._build("recreate", "no url here")
        assert cmd is None

    def test_ai(self):
        cmd = self._build("ai", "koan")
        assert cmd is not None
        assert "app.ai_runner" in cmd
        assert "--project-path" in cmd
        assert "--project-name" in cmd
        assert "--instance-dir" in cmd
        assert self.PROJECT_PATH in cmd
        assert self.PROJECT in cmd
        assert self.INSTANCE in cmd

    def test_check_pr(self):
        url = "https://github.com/sukria/koan/pull/85"
        cmd = self._build("check", url)
        assert cmd is not None
        assert "app.check_runner" in cmd
        assert url in cmd
        assert "--instance-dir" in cmd
        assert "--koan-root" in cmd

    def test_check_issue(self):
        url = "https://github.com/sukria/koan/issues/42"
        cmd = self._build("check", url)
        assert cmd is not None
        assert "app.check_runner" in cmd
        assert url in cmd

    def test_check_no_url(self):
        cmd = self._build("check", "no url here")
        assert cmd is None

    def test_claudemd(self):
        cmd = self._build("claude.md", "koan")
        assert cmd is not None
        assert "app.claudemd_refresh" in cmd
        assert self.PROJECT_PATH in cmd
        assert "--project-name" in cmd

    def test_claudemd_alias(self):
        cmd = self._build("claudemd", "koan")
        assert cmd is not None
        assert "app.claudemd_refresh" in cmd

    def test_claude_alias(self):
        cmd = self._build("claude", "koan")
        assert cmd is not None
        assert "app.claudemd_refresh" in cmd

    def test_unknown_skill(self):
        cmd = self._build("unknown_skill", "args")
        assert cmd is None

    def test_python_path(self):
        """Commands should use the venv python."""
        cmd = self._build("plan", "test idea")
        python_path = os.path.join(self.KOAN_ROOT, ".venv", "bin", "python3")
        assert cmd[0] == python_path


# ---------------------------------------------------------------------------
# dispatch_skill_mission (high-level integration)
# ---------------------------------------------------------------------------

class TestDispatchSkillMission:
    KOAN_ROOT = "/home/user/koan"
    INSTANCE = "/home/user/koan/instance"

    def _dispatch(self, mission_text, project_name="koan", project_path="/home/user/workspace/koan"):
        return dispatch_skill_mission(
            mission_text=mission_text,
            project_name=project_name,
            project_path=project_path,
            koan_root=self.KOAN_ROOT,
            instance_dir=self.INSTANCE,
        )

    def test_plan_dispatch(self):
        cmd = self._dispatch("/plan Add dark mode")
        assert cmd is not None
        assert "app.plan_runner" in cmd

    def test_rebase_dispatch(self):
        cmd = self._dispatch("/rebase https://github.com/sukria/koan/pull/42")
        assert cmd is not None
        assert "app.rebase_pr" in cmd

    def test_recreate_dispatch(self):
        cmd = self._dispatch("/recreate https://github.com/sukria/koan/pull/42")
        assert cmd is not None
        assert "app.recreate_pr" in cmd

    def test_ai_dispatch(self):
        cmd = self._dispatch("/ai koan")
        assert cmd is not None
        assert "app.ai_runner" in cmd

    def test_check_dispatch(self):
        cmd = self._dispatch("/check https://github.com/sukria/koan/issues/42")
        assert cmd is not None
        assert "app.check_runner" in cmd

    def test_claudemd_dispatch(self):
        cmd = self._dispatch("/claude.md koan")
        assert cmd is not None
        assert "app.claudemd_refresh" in cmd

    def test_regular_mission_returns_none(self):
        cmd = self._dispatch("Fix the login bug")
        assert cmd is None

    def test_old_run_format_returns_none(self):
        cmd = self._dispatch("Plan: stuff — run: `cd /koan && python3 -m app.plan_runner ...`")
        assert cmd is None

    def test_scoped_core_dispatch(self):
        cmd = self._dispatch("/core.plan Fix the bug")
        assert cmd is not None
        assert "app.plan_runner" in cmd

    def test_unknown_skill_returns_none(self):
        cmd = self._dispatch("/nonexistent do things")
        assert cmd is None

    def test_empty_returns_none(self):
        cmd = self._dispatch("")
        assert cmd is None


# ---------------------------------------------------------------------------
# Handler integration tests — verify handlers produce clean format
# ---------------------------------------------------------------------------

class TestHandlerCleanFormat:
    """Verify that updated handlers produce /skill format missions."""

    def _make_ctx(self, args="", instance_dir=None, koan_root="/koan"):
        from unittest.mock import MagicMock
        from pathlib import Path

        ctx = MagicMock()
        ctx.args = args
        ctx.koan_root = Path(koan_root)
        ctx.instance_dir = instance_dir or Path("/tmp/test-instance")
        return ctx

    def test_plan_handler_clean_format(self, tmp_path, monkeypatch):
        """Plan handler should produce /plan format, not run: format."""
        missions_file = tmp_path / "missions.md"
        missions_file.write_text("# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n")

        monkeypatch.setattr(
            "app.utils.get_known_projects",
            lambda: [("koan", "/workspace/koan")],
        )

        from skills.core.plan.handler import handle
        ctx = self._make_ctx(args="Add dark mode", instance_dir=tmp_path)
        result = handle(ctx)

        assert "queued" in result.lower() or "Plan queued" in result
        content = missions_file.read_text()
        assert "/plan Add dark mode" in content
        assert "run:" not in content
        assert "python3 -m" not in content

    def test_rebase_handler_clean_format(self, tmp_path, monkeypatch):
        """Rebase handler should produce /rebase format."""
        missions_file = tmp_path / "missions.md"
        missions_file.write_text("# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n")

        monkeypatch.setattr(
            "app.utils.get_known_projects",
            lambda: [("koan", "/workspace/koan")],
        )
        monkeypatch.setattr(
            "app.utils.resolve_project_path",
            lambda repo: "/workspace/koan",
        )
        monkeypatch.setattr(
            "app.pr_review.parse_pr_url",
            lambda url: ("sukria", "koan", "42"),
        )

        from skills.core.rebase.handler import handle
        ctx = self._make_ctx(
            args="https://github.com/sukria/koan/pull/42",
            instance_dir=tmp_path,
        )
        result = handle(ctx)

        assert "queued" in result.lower()
        content = missions_file.read_text()
        assert "/rebase https://github.com/sukria/koan/pull/42" in content
        assert "run:" not in content

    def test_ai_handler_clean_format(self, tmp_path, monkeypatch):
        """AI handler should produce /ai format."""
        missions_file = tmp_path / "missions.md"
        missions_file.write_text("# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n")

        monkeypatch.setattr(
            "app.utils.get_known_projects",
            lambda: [("koan", str(tmp_path))],
        )

        from skills.core.ai.handler import handle
        ctx = self._make_ctx(args="koan", instance_dir=tmp_path)
        result = handle(ctx)

        assert "queued" in result.lower()
        content = missions_file.read_text()
        assert "/ai koan" in content
        assert "run:" not in content

    def test_check_handler_clean_format(self, tmp_path, monkeypatch):
        """Check handler should produce /check format."""
        missions_file = tmp_path / "missions.md"
        missions_file.write_text("# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n")

        monkeypatch.setattr(
            "app.utils.get_known_projects",
            lambda: [("koan", "/workspace/koan")],
        )

        from skills.core.check.handler import handle
        ctx = self._make_ctx(
            args="https://github.com/sukria/koan/pull/85",
            instance_dir=tmp_path,
        )
        result = handle(ctx)

        assert "queued" in result.lower()
        content = missions_file.read_text()
        assert "/check https://github.com/sukria/koan/pull/85" in content
        assert "run:" not in content

    def test_claudemd_handler_clean_format(self, tmp_path, monkeypatch):
        """Claudemd handler should produce /claude.md format."""
        missions_file = tmp_path / "missions.md"
        missions_file.write_text("# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n")

        monkeypatch.setattr(
            "app.utils.get_known_projects",
            lambda: [("koan", "/workspace/koan")],
        )

        from skills.core.claudemd.handler import handle
        ctx = self._make_ctx(args="koan", instance_dir=tmp_path)
        result = handle(ctx)

        assert "queued" in result.lower()
        content = missions_file.read_text()
        assert "/claude.md koan" in content
        assert "run:" not in content

    def test_recreate_handler_clean_format(self, tmp_path, monkeypatch):
        """Recreate handler should produce /recreate format."""
        missions_file = tmp_path / "missions.md"
        missions_file.write_text("# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n")

        monkeypatch.setattr(
            "app.utils.get_known_projects",
            lambda: [("koan", "/workspace/koan")],
        )
        monkeypatch.setattr(
            "app.utils.resolve_project_path",
            lambda repo: "/workspace/koan",
        )
        monkeypatch.setattr(
            "app.pr_review.parse_pr_url",
            lambda url: ("sukria", "koan", "42"),
        )

        from skills.core.recreate.handler import handle
        ctx = self._make_ctx(
            args="https://github.com/sukria/koan/pull/42",
            instance_dir=tmp_path,
        )
        result = handle(ctx)

        assert "queued" in result.lower()
        content = missions_file.read_text()
        assert "/recreate https://github.com/sukria/koan/pull/42" in content
        assert "run:" not in content
