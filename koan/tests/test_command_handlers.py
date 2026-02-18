"""Tests for app.command_handlers — Telegram bridge command handlers."""

import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def koan_root(tmp_path):
    """Create a minimal koan root with instance directory."""
    instance = tmp_path / "instance"
    instance.mkdir()
    missions = instance / "missions.md"
    missions.write_text(
        "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n"
    )
    return tmp_path


@pytest.fixture
def patch_bridge_state(koan_root):
    """Patch bridge_state module-level variables for command_handlers."""
    instance = koan_root / "instance"
    missions_file = instance / "missions.md"

    with patch("app.command_handlers.KOAN_ROOT", koan_root), \
         patch("app.command_handlers.INSTANCE_DIR", instance), \
         patch("app.command_handlers.MISSIONS_FILE", missions_file):
        yield koan_root


@pytest.fixture
def mock_send():
    """Mock send_telegram."""
    with patch("app.command_handlers.send_telegram") as m:
        yield m


@pytest.fixture
def mock_registry():
    """Mock skill registry."""
    registry = MagicMock()
    registry.find_by_command.return_value = None
    registry.resolve_scoped_command.return_value = None
    registry.list_all.return_value = []
    registry.list_by_scope.return_value = []
    with patch("app.command_handlers._get_registry", return_value=registry):
        yield registry


# ---------------------------------------------------------------------------
# Test: handle_command routing
# ---------------------------------------------------------------------------

class TestHandleCommandRouting:
    """Tests for the main handle_command() dispatch."""

    def test_stop_command_creates_stop_file(self, patch_bridge_state, mock_send):
        from app.command_handlers import handle_command
        handle_command("/stop")
        stop_file = patch_bridge_state / ".koan-stop"
        assert stop_file.exists()
        assert stop_file.read_text() == "STOP"
        mock_send.assert_called_once()
        assert "Stop requested" in mock_send.call_args[0][0]

    def test_pause_command_creates_pause_file(self, patch_bridge_state, mock_send):
        from app.command_handlers import handle_command
        handle_command("/pause")
        assert (patch_bridge_state / ".koan-pause").exists()
        mock_send.assert_called_once()
        assert "Paused" in mock_send.call_args[0][0]

    def test_sleep_alias_creates_pause_file(self, patch_bridge_state, mock_send):
        from app.command_handlers import handle_command
        handle_command("/sleep")
        assert (patch_bridge_state / ".koan-pause").exists()

    def test_pause_when_already_paused(self, patch_bridge_state, mock_send):
        from app.command_handlers import handle_command
        (patch_bridge_state / ".koan-pause").write_text("PAUSE")
        handle_command("/pause")
        mock_send.assert_called_once()
        assert "Already paused" in mock_send.call_args[0][0]

    @patch("app.command_handlers.handle_resume")
    def test_resume_command_calls_handle_resume(self, mock_resume, patch_bridge_state, mock_send):
        from app.command_handlers import handle_command
        handle_command("/resume")
        mock_resume.assert_called_once()

    @patch("app.command_handlers.handle_resume")
    def test_work_alias(self, mock_resume, patch_bridge_state, mock_send):
        from app.command_handlers import handle_command
        handle_command("/work")
        mock_resume.assert_called_once()

    @patch("app.command_handlers.handle_resume")
    def test_awake_alias(self, mock_resume, patch_bridge_state, mock_send):
        from app.command_handlers import handle_command
        handle_command("/awake")
        mock_resume.assert_called_once()

    @patch("app.command_handlers.handle_resume")
    def test_run_alias(self, mock_resume, patch_bridge_state, mock_send):
        from app.command_handlers import handle_command
        handle_command("/run")
        mock_resume.assert_called_once()

    def test_unknown_command_sends_error(
        self, patch_bridge_state, mock_send, mock_registry
    ):
        from app.command_handlers import handle_command
        handle_command("/nonexistent")
        mock_send.assert_called_once()
        assert "Unknown command" in mock_send.call_args[0][0]
        assert "/nonexistent" in mock_send.call_args[0][0]

    def test_help_command(self, patch_bridge_state, mock_send, mock_registry):
        from app.command_handlers import handle_command
        handle_command("/help")
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        assert "Commands" in msg
        assert "CORE" in msg

    def test_help_specific_command(self, patch_bridge_state, mock_send, mock_registry):
        """Test /help <command> shows detailed help for a skill."""
        from app.command_handlers import handle_command
        from app.skills import Skill, SkillCommand
        skill = MagicMock(spec=Skill)
        cmd = SkillCommand(
            name="status",
            description="Show agent status",
            aliases=["st"],
            usage="/status [project]",
        )
        skill.commands = [cmd]
        skill.description = "Show agent status"
        mock_registry.find_by_command.return_value = skill

        handle_command("/help status")
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        assert "/status" in msg
        assert "Show agent status" in msg


# ---------------------------------------------------------------------------
# Test: handle_resume
# ---------------------------------------------------------------------------

class TestHandleResume:
    """Tests for handle_resume — unpause from various states."""

    def test_resume_manual_pause(self, patch_bridge_state, mock_send):
        from app.command_handlers import handle_resume
        (patch_bridge_state / ".koan-pause").write_text("PAUSE")
        handle_resume()
        assert not (patch_bridge_state / ".koan-pause").exists()
        mock_send.assert_called_once()
        assert "Unpaused" in mock_send.call_args[0][0]

    def test_resume_max_runs_pause(self, patch_bridge_state, mock_send):
        from app.command_handlers import handle_resume
        (patch_bridge_state / ".koan-pause").write_text("PAUSE")
        (patch_bridge_state / ".koan-pause-reason").write_text("max_runs\n")
        handle_resume()
        assert not (patch_bridge_state / ".koan-pause").exists()
        assert not (patch_bridge_state / ".koan-pause-reason").exists()
        mock_send.assert_called_once()
        assert "max_runs" in mock_send.call_args[0][0]

    @patch("app.command_handlers._reset_session_counters")
    def test_resume_quota_pause_resets_counters(
        self, mock_reset, patch_bridge_state, mock_send
    ):
        from app.command_handlers import handle_resume
        (patch_bridge_state / ".koan-pause").write_text("PAUSE")
        # Quota reason with a far future timestamp
        future_ts = int(time.time()) + 7200
        (patch_bridge_state / ".koan-pause-reason").write_text(
            f"quota\n{future_ts}\nresets at 10am"
        )
        handle_resume()
        mock_reset.assert_called_once()
        assert not (patch_bridge_state / ".koan-pause").exists()

    @patch("app.command_handlers._reset_session_counters")
    def test_resume_quota_with_expired_reset(
        self, mock_reset, patch_bridge_state, mock_send
    ):
        from app.command_handlers import handle_resume
        (patch_bridge_state / ".koan-pause").write_text("PAUSE")
        # Quota reason with past timestamp (expired)
        past_ts = int(time.time()) - 3600
        (patch_bridge_state / ".koan-pause-reason").write_text(
            f"quota\n{past_ts}\nalready reset"
        )
        handle_resume()
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        assert "Quota should be reset" in msg

    def test_resume_when_not_paused(self, patch_bridge_state, mock_send):
        from app.command_handlers import handle_resume
        handle_resume()
        mock_send.assert_called_once()
        assert "No pause" in mock_send.call_args[0][0]


# ---------------------------------------------------------------------------
# Test: _handle_start
# ---------------------------------------------------------------------------

class TestHandleStart:
    """Tests for /start command."""

    @patch("app.command_handlers.handle_resume")
    @patch("app.pid_manager.check_pidfile", return_value=1234)
    def test_start_when_paused_calls_resume(
        self, mock_pid, mock_resume, patch_bridge_state, mock_send
    ):
        from app.command_handlers import _handle_start
        (patch_bridge_state / ".koan-pause").write_text("PAUSE")
        _handle_start()
        mock_resume.assert_called_once()

    @patch("app.pid_manager.check_pidfile", return_value=5678)
    def test_start_when_running_says_already_running(
        self, mock_pid, patch_bridge_state, mock_send
    ):
        from app.command_handlers import _handle_start
        _handle_start()
        mock_send.assert_called_once()
        assert "already running" in mock_send.call_args[0][0]

    @patch("app.pid_manager.start_runner", return_value=(True, "Runner started (PID 9999)"))
    @patch("app.pid_manager.check_pidfile", return_value=None)
    def test_start_when_stopped_launches_runner(
        self, mock_pid, mock_start, patch_bridge_state, mock_send
    ):
        from app.command_handlers import _handle_start
        _handle_start()
        mock_start.assert_called_once()
        # Should send two messages: "Starting..." and success
        assert mock_send.call_count == 2

    @patch("app.pid_manager.start_runner", return_value=(False, "Failed to start"))
    @patch("app.pid_manager.check_pidfile", return_value=None)
    def test_start_failure(
        self, mock_pid, mock_start, patch_bridge_state, mock_send
    ):
        from app.command_handlers import _handle_start
        _handle_start()
        calls = [c[0][0] for c in mock_send.call_args_list]
        assert any("❌" in c for c in calls)


# ---------------------------------------------------------------------------
# Test: handle_mission
# ---------------------------------------------------------------------------

class TestHandleMission:
    """Tests for handle_mission — mission queuing from Telegram."""

    @patch("app.command_handlers.insert_pending_mission")
    def test_simple_mission(self, mock_insert, patch_bridge_state, mock_send):
        from app.command_handlers import handle_mission
        handle_mission("fix the login bug")
        mock_insert.assert_called_once()
        entry = mock_insert.call_args[0][1]
        assert "fix the login bug" in entry
        mock_send.assert_called_once()
        assert "Mission received" in mock_send.call_args[0][0]

    @patch("app.command_handlers.insert_pending_mission")
    def test_mission_with_project_tag(self, mock_insert, patch_bridge_state, mock_send):
        from app.command_handlers import handle_mission
        handle_mission("[project:koan] fix the login bug")
        entry = mock_insert.call_args[0][1]
        assert "[project:koan]" in entry
        assert "fix the login bug" in entry

    @patch("app.command_handlers.insert_pending_mission")
    def test_mission_strips_mission_prefix(self, mock_insert, patch_bridge_state, mock_send):
        from app.command_handlers import handle_mission
        handle_mission("mission: do something cool")
        entry = mock_insert.call_args[0][1]
        assert "do something cool" in entry
        assert "mission:" not in entry.lower()

    @patch("app.command_handlers.insert_pending_mission")
    def test_mission_with_mission_colon_space(self, mock_insert, patch_bridge_state, mock_send):
        from app.command_handlers import handle_mission
        handle_mission("mission : do something")
        entry = mock_insert.call_args[0][1]
        assert "do something" in entry

    @patch("app.command_handlers.insert_pending_mission")
    def test_urgent_mission(self, mock_insert, patch_bridge_state, mock_send):
        from app.command_handlers import handle_mission
        handle_mission("--now fix urgent bug")
        mock_insert.assert_called_once()
        assert mock_insert.call_args[1].get("urgent") or mock_insert.call_args[0][2] is True
        assert "priority" in mock_send.call_args[0][0]


# ---------------------------------------------------------------------------
# Test: _dispatch_skill
# ---------------------------------------------------------------------------

class TestDispatchSkill:
    """Tests for skill dispatch via handle_command."""

    def test_known_skill_dispatched(self, patch_bridge_state, mock_send, mock_registry):
        from app.command_handlers import handle_command
        from app.skills import Skill

        skill = MagicMock(spec=Skill)
        skill.worker = False
        mock_registry.find_by_command.return_value = skill

        with patch("app.command_handlers.execute_skill", return_value="done") as mock_exec:
            handle_command("/status")
            mock_exec.assert_called_once()
            mock_send.assert_called_once_with("done")

    def test_worker_skill_uses_worker_callback(
        self, patch_bridge_state, mock_send, mock_registry
    ):
        from app.command_handlers import handle_command, set_callbacks
        from app.skills import Skill

        skill = MagicMock(spec=Skill)
        skill.worker = True
        mock_registry.find_by_command.return_value = skill

        worker_fn = MagicMock()
        set_callbacks(handle_chat=MagicMock(), run_in_worker=worker_fn)

        with patch("app.command_handlers.execute_skill"):
            handle_command("/review")
            worker_fn.assert_called_once()

    def test_skill_returning_none_does_not_send(
        self, patch_bridge_state, mock_send, mock_registry
    ):
        from app.command_handlers import handle_command
        from app.skills import Skill

        skill = MagicMock(spec=Skill)
        skill.worker = False
        mock_registry.find_by_command.return_value = skill

        with patch("app.command_handlers.execute_skill", return_value=None):
            handle_command("/focus")
            mock_send.assert_not_called()

    def test_scoped_command_dispatch(self, patch_bridge_state, mock_send, mock_registry):
        """Test /<scope>.<name> dispatch path."""
        from app.command_handlers import handle_command
        from app.skills import Skill

        skill = MagicMock(spec=Skill)
        skill.worker = False
        mock_registry.find_by_command.return_value = None
        mock_registry.resolve_scoped_command.return_value = (skill, "review", "")

        with patch("app.command_handlers.execute_skill", return_value="reviewed") as mock_exec:
            handle_command("/anantys.review")
            mock_exec.assert_called_once()


# ---------------------------------------------------------------------------
# Test: _handle_help
# ---------------------------------------------------------------------------

class TestHandleHelp:
    """Tests for /help output."""

    def test_help_lists_core_section(self, patch_bridge_state, mock_send, mock_registry):
        from app.command_handlers import _handle_help
        _handle_help()
        msg = mock_send.call_args[0][0]
        assert "CORE" in msg
        assert "/pause" in msg
        assert "/resume" in msg
        assert "/stop" in msg
        assert "/help" in msg

    def test_help_lists_tips(self, patch_bridge_state, mock_send, mock_registry):
        from app.command_handlers import _handle_help
        _handle_help()
        msg = mock_send.call_args[0][0]
        assert "TIPS" in msg

    def test_help_lists_non_core_skills(self, patch_bridge_state, mock_send, mock_registry):
        from app.command_handlers import _handle_help
        from app.skills import Skill, SkillCommand

        non_core_skill = MagicMock(spec=Skill)
        non_core_skill.scope = "anantys"
        non_core_skill.description = "Custom review"
        cmd = MagicMock(spec=SkillCommand)
        cmd.name = "review"
        cmd.description = "Custom review"
        cmd.aliases = []
        non_core_skill.commands = [cmd]
        mock_registry.list_all.return_value = [non_core_skill]

        _handle_help()
        msg = mock_send.call_args[0][0]
        assert "SKILLS" in msg


# ---------------------------------------------------------------------------
# Test: _handle_skill_command
# ---------------------------------------------------------------------------

class TestHandleSkillCommand:
    """Tests for /skill subcommands."""

    def test_skill_list_empty(self, patch_bridge_state, mock_send, mock_registry):
        from app.command_handlers import _handle_skill_command
        _handle_skill_command("")
        msg = mock_send.call_args[0][0]
        assert "No extra skills loaded" in msg

    def test_skill_list_with_non_core(self, patch_bridge_state, mock_send, mock_registry):
        from app.command_handlers import _handle_skill_command
        from app.skills import Skill, SkillCommand

        skill = MagicMock(spec=Skill)
        skill.scope = "custom"
        skill.description = "Custom skill"
        cmd = MagicMock(spec=SkillCommand)
        cmd.name = "deploy"
        cmd.description = "Deploy project"
        skill.commands = [cmd]
        mock_registry.list_all.return_value = [skill]
        mock_registry.list_by_scope.return_value = [skill]

        _handle_skill_command("")
        msg = mock_send.call_args[0][0]
        assert "custom" in msg

    @patch("app.skill_manager.install_skill_source", return_value=(True, "Installed!"))
    def test_skill_install(self, mock_install, patch_bridge_state, mock_send, mock_registry):
        from app.command_handlers import _handle_skill_command
        _handle_skill_command("install https://github.com/team/skills.git ops")
        mock_install.assert_called_once()
        msg = mock_send.call_args[0][0]
        assert "✅" in msg

    def test_skill_install_no_args(self, patch_bridge_state, mock_send, mock_registry):
        from app.command_handlers import _handle_skill_command
        _handle_skill_command("install")
        msg = mock_send.call_args[0][0]
        assert "Usage" in msg

    @patch("app.skill_manager.update_all_sources", return_value=(True, "Updated!"))
    def test_skill_update_all(self, mock_update, patch_bridge_state, mock_send, mock_registry):
        from app.command_handlers import _handle_skill_command
        _handle_skill_command("update")
        mock_update.assert_called_once()

    @patch("app.skill_manager.update_skill_source", return_value=(True, "Updated!"))
    def test_skill_update_specific(self, mock_update, patch_bridge_state, mock_send, mock_registry):
        from app.command_handlers import _handle_skill_command
        _handle_skill_command("update ops")
        mock_update.assert_called_once()

    @patch("app.skill_manager.remove_skill_source", return_value=(True, "Removed!"))
    def test_skill_remove(self, mock_remove, patch_bridge_state, mock_send, mock_registry):
        from app.command_handlers import _handle_skill_command
        _handle_skill_command("remove ops")
        mock_remove.assert_called_once()

    def test_skill_remove_no_args(self, patch_bridge_state, mock_send, mock_registry):
        from app.command_handlers import _handle_skill_command
        _handle_skill_command("remove")
        msg = mock_send.call_args[0][0]
        assert "Usage" in msg

    @patch("app.skill_manager.list_sources", return_value="No sources installed.")
    def test_skill_sources(self, mock_list, patch_bridge_state, mock_send, mock_registry):
        from app.command_handlers import _handle_skill_command
        _handle_skill_command("sources")
        mock_list.assert_called_once()

    def test_skill_scope_list(self, patch_bridge_state, mock_send, mock_registry):
        """Test /skill core lists skills in core scope."""
        from app.command_handlers import _handle_skill_command
        from app.skills import Skill, SkillCommand

        skill = MagicMock(spec=Skill)
        skill.scope = "core"
        cmd = MagicMock(spec=SkillCommand)
        cmd.name = "status"
        cmd.description = "Show status"
        skill.commands = [cmd]
        mock_registry.list_by_scope.return_value = [skill]

        _handle_skill_command("core")
        msg = mock_send.call_args[0][0]
        assert "core" in msg
        assert "/status" in msg


# ---------------------------------------------------------------------------
# Test: set_callbacks
# ---------------------------------------------------------------------------

class TestSetCallbacks:
    """Tests for callback injection."""

    def test_set_callbacks_stores_functions(self):
        from app.command_handlers import set_callbacks
        import app.command_handlers as mod
        chat_fn = MagicMock()
        worker_fn = MagicMock()
        set_callbacks(chat_fn, worker_fn)
        assert mod._handle_chat_cb is chat_fn
        assert mod._run_in_worker_cb is worker_fn


# ---------------------------------------------------------------------------
# Test: _reset_session_counters
# ---------------------------------------------------------------------------

class TestResetSessionCounters:
    """Tests for _reset_session_counters."""

    @patch("app.usage_estimator.cmd_reset_session")
    def test_reset_calls_cmd_reset_session(self, mock_reset, patch_bridge_state):
        from app.command_handlers import _reset_session_counters
        _reset_session_counters()
        mock_reset.assert_called_once()

    @patch("app.usage_estimator.cmd_reset_session", side_effect=Exception("boom"))
    def test_reset_handles_exception(self, mock_reset, patch_bridge_state):
        from app.command_handlers import _reset_session_counters
        # Should not raise
        _reset_session_counters()


# ---------------------------------------------------------------------------
# Test: /pause uses pause_manager (not raw file write)
# ---------------------------------------------------------------------------

class TestPauseUsesPauseManager:
    """Tests that /pause creates proper pause state via pause_manager."""

    def test_pause_creates_reason_file(self, patch_bridge_state, mock_send):
        """Verify /pause creates .koan-pause-reason with 'manual' reason."""
        from app.command_handlers import handle_command
        handle_command("/pause")
        reason_file = patch_bridge_state / ".koan-pause-reason"
        assert reason_file.exists(), ".koan-pause-reason should exist"
        content = reason_file.read_text()
        assert "manual" in content

    def test_pause_reason_contains_display_info(self, patch_bridge_state, mock_send):
        """Verify the reason file contains human-readable display info."""
        from app.command_handlers import handle_command
        handle_command("/pause")
        reason_file = patch_bridge_state / ".koan-pause-reason"
        content = reason_file.read_text()
        assert "Telegram" in content or "paused" in content

    def test_pause_reason_has_timestamp(self, patch_bridge_state, mock_send):
        """Verify the reason file contains a valid UNIX timestamp."""
        from app.command_handlers import handle_command
        handle_command("/pause")
        reason_file = patch_bridge_state / ".koan-pause-reason"
        lines = reason_file.read_text().strip().splitlines()
        assert len(lines) >= 2
        timestamp = int(lines[1].strip())
        assert timestamp > 0

    def test_sleep_alias_creates_reason_file(self, patch_bridge_state, mock_send):
        """/sleep should also create .koan-pause-reason like /pause."""
        from app.command_handlers import handle_command
        handle_command("/sleep")
        assert (patch_bridge_state / ".koan-pause-reason").exists()

    def test_pause_survives_check_and_resume(self, patch_bridge_state, mock_send):
        """Pause created via /pause should NOT be cleaned as orphan."""
        from app.command_handlers import handle_command
        from app.pause_manager import check_and_resume, is_paused
        handle_command("/pause")
        # check_and_resume should NOT remove a manual pause (no auto-resume)
        result = check_and_resume(str(patch_bridge_state))
        assert result is None, "Manual pause should not auto-resume"
        assert is_paused(str(patch_bridge_state)), "Should still be paused"

    def test_already_paused_with_reason_file(self, patch_bridge_state, mock_send):
        """When already paused (with reason file), should report already paused."""
        from app.command_handlers import handle_command
        from app.pause_manager import create_pause
        create_pause(str(patch_bridge_state), "manual")
        handle_command("/pause")
        mock_send.assert_called_once()
        assert "Already paused" in mock_send.call_args[0][0]
