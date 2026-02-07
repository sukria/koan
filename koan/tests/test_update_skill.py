"""Tests for the /update and /restart skill handler."""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.skills import SkillContext


def _make_ctx(tmp_path, command_name="update", args=""):
    """Create a SkillContext for testing."""
    return SkillContext(
        koan_root=tmp_path,
        instance_dir=tmp_path / "instance",
        command_name=command_name,
        args=args,
        send_message=MagicMock(),
        handle_chat=MagicMock(),
    )


# Lazy imports inside handler functions → patch at source module
_P_REQUEST = "app.restart_manager.request_restart"
_P_REMOVE = "app.pause_manager.remove_pause"
_P_PULL = "app.update_manager.pull_upstream"


class TestRestartCommand:
    """Tests for /restart via the update skill handler."""

    @patch(_P_REQUEST)
    @patch(_P_REMOVE)
    def test_restart_clears_pause(self, mock_remove, mock_request, tmp_path):
        from skills.core.update.handler import handle
        ctx = _make_ctx(tmp_path, command_name="restart")
        handle(ctx)
        mock_remove.assert_called_once_with(str(tmp_path))

    @patch(_P_REQUEST)
    @patch(_P_REMOVE)
    def test_restart_creates_signal(self, mock_remove, mock_request, tmp_path):
        from skills.core.update.handler import handle
        ctx = _make_ctx(tmp_path, command_name="restart")
        handle(ctx)
        mock_request.assert_called_once_with(tmp_path)

    @patch(_P_REQUEST)
    @patch(_P_REMOVE)
    def test_restart_returns_message(self, mock_remove, mock_request, tmp_path):
        from skills.core.update.handler import handle
        ctx = _make_ctx(tmp_path, command_name="restart")
        result = handle(ctx)
        assert "Restart" in result
        assert "🔄" in result


class TestUpdateCommand:
    """Tests for /update via the update skill handler."""

    @patch(_P_REQUEST)
    @patch(_P_REMOVE)
    @patch(_P_PULL)
    def test_update_success_with_changes(self, mock_pull, mock_remove, mock_request, tmp_path):
        from skills.core.update.handler import handle
        from app.update_manager import UpdateResult

        mock_pull.return_value = UpdateResult(
            success=True, old_commit="abc", new_commit="def",
            commits_pulled=3, stashed=False,
        )
        ctx = _make_ctx(tmp_path, command_name="update")
        result = handle(ctx)

        mock_pull.assert_called_once_with(tmp_path)
        mock_remove.assert_called_once()
        mock_request.assert_called_once_with(tmp_path)
        assert "3 new commits" in result
        assert "Restarting" in result

    @patch(_P_REQUEST)
    @patch(_P_REMOVE)
    @patch(_P_PULL)
    def test_update_no_changes(self, mock_pull, mock_remove, mock_request, tmp_path):
        from skills.core.update.handler import handle
        from app.update_manager import UpdateResult

        mock_pull.return_value = UpdateResult(
            success=True, old_commit="abc", new_commit="abc",
            commits_pulled=0,
        )
        ctx = _make_ctx(tmp_path, command_name="update")
        result = handle(ctx)

        # Should NOT restart when no changes
        mock_request.assert_not_called()
        assert "up to date" in result

    @patch(_P_PULL)
    def test_update_failure(self, mock_pull, tmp_path):
        from skills.core.update.handler import handle
        from app.update_manager import UpdateResult

        mock_pull.return_value = UpdateResult(
            success=False, old_commit="abc", new_commit="abc",
            commits_pulled=0, error="network timeout",
        )
        ctx = _make_ctx(tmp_path, command_name="update")
        result = handle(ctx)

        assert "failed" in result.lower()
        assert "network timeout" in result

    @patch(_P_REQUEST)
    @patch(_P_REMOVE)
    @patch(_P_PULL)
    def test_update_stashed_warning(self, mock_pull, mock_remove, mock_request, tmp_path):
        from skills.core.update.handler import handle
        from app.update_manager import UpdateResult

        mock_pull.return_value = UpdateResult(
            success=True, old_commit="abc", new_commit="def",
            commits_pulled=1, stashed=True,
        )
        ctx = _make_ctx(tmp_path, command_name="update")
        result = handle(ctx)

        assert "stashed" in result.lower()

    @patch(_P_REQUEST)
    @patch(_P_REMOVE)
    @patch(_P_PULL)
    def test_update_single_commit_grammar(self, mock_pull, mock_remove, mock_request, tmp_path):
        from skills.core.update.handler import handle
        from app.update_manager import UpdateResult

        mock_pull.return_value = UpdateResult(
            success=True, old_commit="abc", new_commit="def",
            commits_pulled=1,
        )
        ctx = _make_ctx(tmp_path, command_name="update")
        result = handle(ctx)

        assert "1 new commit)" in result
        assert "commits)" not in result


class TestHandleDispatch:
    """Tests for handle() dispatch logic."""

    @patch("skills.core.update.handler._handle_restart")
    def test_restart_command_dispatches(self, mock_restart, tmp_path):
        from skills.core.update.handler import handle
        mock_restart.return_value = "ok"
        ctx = _make_ctx(tmp_path, command_name="restart")
        handle(ctx)
        mock_restart.assert_called_once_with(ctx)

    @patch("skills.core.update.handler._handle_update")
    def test_update_command_dispatches(self, mock_update, tmp_path):
        from skills.core.update.handler import handle
        mock_update.return_value = "ok"
        ctx = _make_ctx(tmp_path, command_name="update")
        handle(ctx)
        mock_update.assert_called_once_with(ctx)

    @patch("skills.core.update.handler._handle_update")
    def test_upgrade_alias_dispatches_to_update(self, mock_update, tmp_path):
        from skills.core.update.handler import handle
        mock_update.return_value = "ok"
        ctx = _make_ctx(tmp_path, command_name="upgrade")
        handle(ctx)
        mock_update.assert_called_once_with(ctx)


class TestSkillRegistration:
    """Tests that the skill is properly registered."""

    def test_skill_md_exists(self):
        skill_md = Path(__file__).parent.parent / "skills" / "core" / "update" / "SKILL.md"
        assert skill_md.exists()

    def test_handler_exists(self):
        handler = Path(__file__).parent.parent / "skills" / "core" / "update" / "handler.py"
        assert handler.exists()

    def test_skill_discoverable(self):
        """The skill registry should find /update and /restart."""
        from app.skills import build_registry
        registry = build_registry()
        update_skill = registry.find_by_command("update")
        assert update_skill is not None

        restart_skill = registry.find_by_command("restart")
        assert restart_skill is not None

        # Both should be the same skill
        assert update_skill.name == restart_skill.name == "update"

    def test_upgrade_alias(self):
        """The /upgrade alias should resolve to the update skill."""
        from app.skills import build_registry
        registry = build_registry()
        skill = registry.find_by_command("upgrade")
        assert skill is not None
        assert skill.name == "update"
