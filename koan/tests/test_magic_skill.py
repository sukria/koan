"""Tests for the /magic skill handler — creative project exploration."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.skills import SkillContext


def _make_ctx(koan_root, instance_dir, send_message=None):
    """Create a minimal SkillContext for testing."""
    ctx = MagicMock(spec=SkillContext)
    ctx.command_name = "magic"
    ctx.koan_root = koan_root
    ctx.instance_dir = instance_dir
    ctx.args = ""
    ctx.send_message = send_message
    return ctx


class TestGetProjects:
    """Tests for _get_projects helper."""

    def test_returns_empty_when_no_projects(self, tmp_path, monkeypatch):
        from skills.core.magic.handler import _get_projects

        monkeypatch.delenv("KOAN_PROJECTS", raising=False)

        ctx = _make_ctx(tmp_path, tmp_path)
        with patch("app.utils.get_known_projects", return_value=[]):
            result = _get_projects(ctx)

        assert result == []

    def test_returns_projects_from_get_known_projects(self, tmp_path, monkeypatch):
        from skills.core.magic.handler import _get_projects

        # Create a real directory to pass the is_dir check
        project_path = tmp_path / "myproject"
        project_path.mkdir()

        ctx = _make_ctx(tmp_path, tmp_path)
        with patch(
            "app.utils.get_known_projects",
            return_value=[("myproject", str(project_path))],
        ):
            result = _get_projects(ctx)

        assert len(result) == 1
        assert result[0][0] == "myproject"

    def test_filters_out_nonexistent_directories(self, tmp_path, monkeypatch):
        from skills.core.magic.handler import _get_projects

        ctx = _make_ctx(tmp_path, tmp_path)
        with patch(
            "app.utils.get_known_projects",
            return_value=[("fake", "/nonexistent/path")],
        ):
            result = _get_projects(ctx)

        assert result == []

    def test_returns_empty_on_exception(self, tmp_path, monkeypatch):
        """When get_known_projects() raises, returns empty list."""
        from skills.core.magic.handler import _get_projects

        ctx = _make_ctx(tmp_path, tmp_path)
        with patch(
            "app.utils.get_known_projects",
            side_effect=Exception("Failed"),
        ):
            result = _get_projects(ctx)

        assert result == []


class TestResolveProject:
    """Tests for _resolve_project helper."""

    def test_resolves_by_name(self):
        from skills.core.magic.handler import _resolve_project

        projects = [("koan", "/path/to/koan"), ("backend", "/path/to/backend")]
        name, path = _resolve_project(projects, "koan")
        assert name == "koan"
        assert path == "/path/to/koan"

    def test_resolves_case_insensitive(self):
        from skills.core.magic.handler import _resolve_project

        projects = [("Koan", "/path/to/koan")]
        name, path = _resolve_project(projects, "koan")
        assert name == "Koan"

    def test_returns_none_for_unknown(self):
        from skills.core.magic.handler import _resolve_project

        projects = [("koan", "/path/to/koan")]
        name, path = _resolve_project(projects, "unknown")
        assert name is None
        assert path is None


class TestGetMissionsContext:
    """Tests for _get_missions_context helper."""

    def test_returns_default_when_no_file(self, tmp_path):
        from skills.core.magic.handler import _get_missions_context

        result = _get_missions_context(tmp_path)
        assert "No active missions" in result

    def test_returns_pending_missions(self, tmp_path):
        from skills.core.magic.handler import _get_missions_context

        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            """# Missions

## Pending

- Task 1
- Task 2

## In Progress

## Done
"""
        )

        result = _get_missions_context(tmp_path)
        assert "Pending" in result
        assert "Task 1" in result

    def test_returns_in_progress_missions(self, tmp_path):
        from skills.core.magic.handler import _get_missions_context

        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            """# Missions

## Pending

## In Progress

- Working on feature

## Done
"""
        )

        result = _get_missions_context(tmp_path)
        assert "In progress" in result
        assert "Working on feature" in result


class TestGatherGitActivity:
    """Tests for _gather_git_activity helper."""

    def test_returns_message_for_non_git_directory(self, tmp_path):
        from skills.core.magic.handler import _gather_git_activity

        result = _gather_git_activity(str(tmp_path))
        # Either returns empty or an error message
        assert isinstance(result, str)

    def test_handles_timeout_gracefully(self, tmp_path):
        from skills.core.magic.handler import _gather_git_activity

        with patch(
            "skills.core.magic.handler.subprocess.run",
            side_effect=TimeoutError("Timed out"),
        ):
            result = _gather_git_activity(str(tmp_path))

        # Should not raise, returns a string
        assert isinstance(result, str)


class TestGatherProjectStructure:
    """Tests for _gather_project_structure helper."""

    def test_returns_directories_and_files(self, tmp_path):
        from skills.core.magic.handler import _gather_project_structure

        # Create some structure
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()
        (tmp_path / "README.md").touch()
        (tmp_path / "setup.py").touch()
        (tmp_path / ".hidden").touch()  # Should be excluded

        result = _gather_project_structure(str(tmp_path))

        assert "src/" in result
        assert "tests/" in result
        assert "README.md" in result
        assert "setup.py" in result
        assert ".hidden" not in result

    def test_handles_empty_directory(self, tmp_path):
        from skills.core.magic.handler import _gather_project_structure

        result = _gather_project_structure(str(tmp_path))
        # Empty but valid
        assert isinstance(result, str)


class TestCleanResponse:
    """Tests for _clean_response helper."""

    def test_removes_markdown_formatting(self):
        from skills.core.magic.handler import _clean_response

        text = "# Header\n**bold** and `code`"
        result = _clean_response(text)

        assert "**" not in result
        assert result.startswith("Header") or "Header" in result

    def test_removes_code_blocks(self):
        from skills.core.magic.handler import _clean_response

        text = "```python\nprint('hello')\n```"
        result = _clean_response(text)

        assert "```" not in result
        assert "print('hello')" in result

    def test_truncates_long_responses(self):
        from skills.core.magic.handler import _clean_response

        text = "x" * 3000
        result = _clean_response(text)

        assert len(result) <= 2000
        assert result.endswith("...")

    def test_removes_max_turns_error(self):
        from skills.core.magic.handler import _clean_response

        text = "Error: max turns reached\nActual content here"
        result = _clean_response(text)

        assert "max turns" not in result
        assert "Actual content here" in result


class TestHandle:
    """Tests for the main handle function."""

    def test_returns_message_when_no_projects(self, tmp_path, monkeypatch):
        from skills.core.magic.handler import handle

        monkeypatch.delenv("KOAN_PROJECTS", raising=False)

        ctx = _make_ctx(tmp_path, tmp_path)
        with patch("app.utils.get_known_projects", return_value=[]):
            result = handle(ctx)

        assert "No projects" in result

    def test_sends_exploring_message(self, tmp_path, monkeypatch):
        from skills.core.magic.handler import handle
        import subprocess as sp

        project_path = tmp_path / "testproj"
        project_path.mkdir()
        (tmp_path / "soul.md").write_text("Test soul")

        send_fn = MagicMock()
        ctx = _make_ctx(tmp_path, tmp_path, send_message=send_fn)

        with patch(
            "app.utils.get_known_projects",
            return_value=[("testproj", str(project_path))],
        ):
            with patch.object(sp, "run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout="Great idea!", stderr=""
                )
                handle(ctx)

        send_fn.assert_called_once()
        assert "testproj" in send_fn.call_args[0][0]

    def test_handles_claude_timeout(self, tmp_path, monkeypatch):
        from skills.core.magic.handler import handle
        import subprocess as sp

        project_path = tmp_path / "testproj"
        project_path.mkdir()
        (tmp_path / "soul.md").write_text("Test soul")

        ctx = _make_ctx(tmp_path, tmp_path)

        with patch(
            "app.utils.get_known_projects",
            return_value=[("testproj", str(project_path))],
        ):
            with patch.object(
                sp, "run",
                side_effect=sp.TimeoutExpired(cmd="claude", timeout=90),
            ):
                result = handle(ctx)

        assert "Timeout" in result or "Try again" in result

    def test_handles_claude_error(self, tmp_path, monkeypatch):
        from skills.core.magic.handler import handle
        import subprocess as sp

        project_path = tmp_path / "testproj"
        project_path.mkdir()
        (tmp_path / "soul.md").write_text("Test soul")

        ctx = _make_ctx(tmp_path, tmp_path)

        with patch(
            "app.utils.get_known_projects",
            return_value=[("testproj", str(project_path))],
        ):
            with patch.object(
                sp, "run",
                side_effect=Exception("Something went wrong"),
            ):
                result = handle(ctx)

        assert "Error" in result or "Try again" in result

    def test_targets_specific_project(self, tmp_path, monkeypatch):
        """When /magic <project> is called, explore that project, not random."""
        from skills.core.magic.handler import handle
        import subprocess as sp

        koan_dir = tmp_path / "koan"
        koan_dir.mkdir()
        backend_dir = tmp_path / "backend"
        backend_dir.mkdir()
        (tmp_path / "soul.md").write_text("Test soul")

        send_fn = MagicMock()
        ctx = _make_ctx(tmp_path, tmp_path, send_message=send_fn)
        ctx.args = "backend"

        with patch(
            "app.utils.get_known_projects",
            return_value=[
                ("koan", str(koan_dir)),
                ("backend", str(backend_dir)),
            ],
        ):
            with patch.object(sp, "run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout="Backend ideas!", stderr=""
                )
                handle(ctx)

        # Should have sent "Exploring backend..."
        send_fn.assert_called_once()
        assert "backend" in send_fn.call_args[0][0]

    def test_unknown_project_returns_error(self, tmp_path, monkeypatch):
        """When /magic <unknown> is called, return error with known list."""
        from skills.core.magic.handler import handle

        project_path = tmp_path / "koan"
        project_path.mkdir()

        ctx = _make_ctx(tmp_path, tmp_path)
        ctx.args = "nonexistent"

        with patch(
            "app.utils.get_known_projects",
            return_value=[("koan", str(project_path))],
        ):
            result = handle(ctx)

        assert "Unknown project" in result
        assert "nonexistent" in result
        assert "koan" in result

    def test_no_args_picks_random(self, tmp_path, monkeypatch):
        """When /magic is called without args, random project is picked."""
        from skills.core.magic.handler import handle
        import subprocess as sp

        project_path = tmp_path / "testproj"
        project_path.mkdir()
        (tmp_path / "soul.md").write_text("Test soul")

        ctx = _make_ctx(tmp_path, tmp_path)
        ctx.args = ""  # no args

        with patch(
            "app.utils.get_known_projects",
            return_value=[("testproj", str(project_path))],
        ):
            with patch.object(sp, "run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout="Ideas!", stderr=""
                )
                result = handle(ctx)

        assert isinstance(result, str)
