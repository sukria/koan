"""Tests for awake.py — message classification, mission handling, project parsing, handlers."""

import re
import subprocess
import time
from unittest.mock import patch, MagicMock, mock_open

import pytest

from app.awake import (
    is_mission,
    is_command,
    parse_project,
    handle_mission,
    handle_command,
    handle_chat,
    handle_resume,
    handle_message,
    flush_outbox,
    _format_outbox_message,
    _clean_chat_response,
    _build_status,
    _handle_help,
    _handle_language,
    _handle_log,
    _handle_usage,
    _handle_mission_command,
    _run_in_worker,
    get_updates,
    check_config,
    MISSIONS_FILE,
)


# ---------------------------------------------------------------------------
# is_mission
# ---------------------------------------------------------------------------

class TestIsMission:
    """Test mission detection heuristics."""

    def test_explicit_mission_prefix(self):
        assert is_mission("mission: audit the backend") is True

    def test_explicit_mission_prefix_with_space(self):
        assert is_mission("mission : fix the login bug") is True

    def test_imperative_verb_start(self):
        assert is_mission("implement dark mode") is True
        assert is_mission("fix the authentication bug") is True
        assert is_mission("audit the security layer") is True
        assert is_mission("create a new endpoint") is True
        assert is_mission("add tests for awake.py") is True
        assert is_mission("review the PR") is True
        assert is_mission("analyze the logs") is True
        assert is_mission("explore the codebase") is True
        assert is_mission("build the dashboard") is True
        assert is_mission("write a migration script") is True
        assert is_mission("run the test suite") is True
        assert is_mission("deploy to staging") is True
        assert is_mission("test the new feature") is True
        assert is_mission("refactor the auth module") is True

    def test_long_message_with_verb(self):
        long_text = "implement " + "x " * 150  # >200 chars
        assert is_mission(long_text) is True

    def test_short_question_not_mission(self):
        assert is_mission("how are you?") is False
        assert is_mission("what's the status?") is False

    def test_greeting_not_mission(self):
        assert is_mission("hello") is False
        assert is_mission("good morning") is False

    def test_case_insensitive(self):
        assert is_mission("Implement dark mode") is True
        assert is_mission("MISSION: do something") is True

    def test_empty_string(self):
        assert is_mission("") is False

    def test_long_message_with_verb_only_at_start(self):
        """Long messages should only match verbs at the start, not mid-text."""
        # Verb at start — IS a mission
        long_mission = "implement " + "x " * 150
        assert is_mission(long_mission) is True
        # Verb buried mid-text — NOT a mission
        long_chat = "I was thinking about how we should " + "x " * 100 + " and then implement something"
        assert is_mission(long_chat) is False

    def test_conversational_with_action_verb(self):
        """Short messages starting with action verbs but clearly conversational."""
        # These are correctly classified as missions by the current heuristic.
        # Users should use /chat to override when needed.
        assert is_mission("add me to the list") is True  # starts with "add"
        assert is_mission("run me through the pipeline") is True  # starts with "run"

    def test_long_conversational_not_mission(self):
        """Long conversational messages without leading verbs are not missions."""
        long_chat = "Hey, I wanted to discuss something with you. " + "blah " * 60
        assert is_mission(long_chat) is False


# ---------------------------------------------------------------------------
# /chat command (force chat mode)
# ---------------------------------------------------------------------------

class TestHandleChatCommand:
    """Test /chat prefix to force chat mode."""

    @patch("app.awake._run_in_worker")
    def test_chat_command_routes_to_chat(self, mock_worker):
        """'/chat fix the bug' should route to chat, not mission."""
        handle_command("/chat fix the bug")
        mock_worker.assert_called_once_with(handle_chat, "fix the bug")

    @patch("app.awake._run_in_worker")
    def test_chat_command_strips_prefix(self, mock_worker):
        """/chat should strip the prefix and pass the rest as chat text."""
        handle_command("/chat implement dark mode for the dashboard")
        mock_worker.assert_called_once_with(
            handle_chat, "implement dark mode for the dashboard"
        )

    @patch("app.awake.send_telegram")
    def test_chat_command_empty_shows_usage(self, mock_send):
        """/chat with no text should show usage help."""
        handle_command("/chat")
        msg = mock_send.call_args[0][0]
        assert "Usage" in msg
        assert "/chat" in msg

    @patch("app.awake.send_telegram")
    def test_chat_command_whitespace_only_shows_usage(self, mock_send):
        """/chat followed by only whitespace shows usage."""
        handle_command("/chat   ")
        msg = mock_send.call_args[0][0]
        assert "Usage" in msg

    @patch("app.awake._run_in_worker")
    def test_chat_via_handle_message(self, mock_worker):
        """/chat goes through handle_message -> handle_command -> chat."""
        handle_message("/chat add me to the list of testers")
        mock_worker.assert_called_once_with(handle_chat, "add me to the list of testers")


# ---------------------------------------------------------------------------
# is_command
# ---------------------------------------------------------------------------

class TestIsCommand:
    def test_slash_commands(self):
        assert is_command("/stop") is True
        assert is_command("/status") is True
        assert is_command("/resume") is True

    def test_non_commands(self):
        assert is_command("hello") is False
        assert is_command("fix the bug") is False
        assert is_command("") is False


# ---------------------------------------------------------------------------
# parse_project
# ---------------------------------------------------------------------------

class TestParseProject:
    def test_with_project_tag(self):
        project, text = parse_project("[project:anantys] fix the login")
        assert project == "anantys"
        assert text == "fix the login"

    def test_without_project_tag(self):
        project, text = parse_project("fix the login")
        assert project is None
        assert text == "fix the login"

    def test_project_tag_with_hyphen(self):
        project, text = parse_project("[project:anantys-back] deploy")
        assert project == "anantys-back"
        assert text == "deploy"

    def test_project_tag_with_underscore(self):
        project, text = parse_project("[project:my_project] test")
        assert project == "my_project"
        assert text == "test"

    def test_project_tag_in_middle(self):
        project, text = parse_project("fix [project:koan] the bug")
        assert project == "koan"
        assert text == "fix the bug"

    def test_french_projet_tag(self):
        project, text = parse_project("[projet:anantys] fix the login")
        assert project == "anantys"
        assert text == "fix the login"

    def test_french_projet_tag_in_middle(self):
        project, text = parse_project("fix [projet:koan] the bug")
        assert project == "koan"
        assert text == "fix the bug"


# ---------------------------------------------------------------------------
# handle_mission (integration with filesystem)
# ---------------------------------------------------------------------------

class TestHandleMission:
    @patch("app.awake.send_telegram")
    @patch("app.awake.MISSIONS_FILE")
    @patch("app.awake.INSTANCE_DIR")
    def test_mission_appended_to_pending(self, mock_inst, mock_file, mock_send, tmp_path):
        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n## En attente\n\n(aucune)\n\n## En cours\n\n## Terminées\n"
        )
        mock_file.__class__ = type(missions_file)
        # Directly test the file manipulation logic
        with patch("app.awake.MISSIONS_FILE", missions_file):
            handle_mission("mission: audit security")

        content = missions_file.read_text()
        assert "- audit security" in content
        mock_send.assert_called_once()

    @patch("app.awake.send_telegram")
    def test_mission_with_project_tag(self, mock_send, tmp_path):
        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n## En attente\n\n(aucune)\n\n## En cours\n\n"
        )
        with patch("app.awake.MISSIONS_FILE", missions_file):
            handle_mission("[project:koan] add tests")

        content = missions_file.read_text()
        assert "- [project:koan] add tests" in content


# ---------------------------------------------------------------------------
# _build_status
# ---------------------------------------------------------------------------

class TestBuildStatus:
    @patch("app.awake.MISSIONS_FILE")
    def test_status_with_french_sections(self, mock_file, tmp_path):
        """_build_status handles French section names from missions.md."""
        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n"
            "## En attente\n\n"
            "- [project:koan] add tests\n"
            "- fix bug\n\n"
            "## En cours\n\n"
            "- [project:koan] doing stuff\n\n"
        )
        with patch("app.awake.MISSIONS_FILE", missions_file), \
             patch("app.awake.KOAN_ROOT", tmp_path):
            status = _build_status()

        assert "Kōan Status" in status
        # Missions split by project: koan gets 1 pending + 1 in_progress, default gets 1 pending
        assert "**koan**" in status
        assert "**default**" in status
        assert "In progress: 1" in status

    @patch("app.awake.MISSIONS_FILE")
    def test_status_shows_pending_titles(self, mock_file, tmp_path):
        """_build_status shows pending mission titles, not just count."""
        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n"
            "## En attente\n\n"
            "- [project:koan] add tests\n"
            "- [project:koan] fix dashboard\n\n"
            "## En cours\n\n"
        )
        with patch("app.awake.MISSIONS_FILE", missions_file), \
             patch("app.awake.KOAN_ROOT", tmp_path):
            status = _build_status()

        assert "Pending: 2" in status
        assert "add tests" in status
        assert "fix dashboard" in status
        # Project tags should be stripped from display
        assert "[project:koan]" not in status

    @patch("app.awake.MISSIONS_FILE")
    def test_status_empty(self, mock_file, tmp_path):
        missions_file = tmp_path / "missions.md"
        missions_file.write_text("# Missions\n\n## En attente\n\n## En cours\n\n")
        with patch("app.awake.MISSIONS_FILE", missions_file), \
             patch("app.awake.KOAN_ROOT", tmp_path):
            status = _build_status()

        assert "Kōan Status" in status

    @patch("app.awake.MISSIONS_FILE")
    def test_status_with_stop_file(self, mock_file, tmp_path):
        missions_file = tmp_path / "missions.md"
        missions_file.write_text("# Missions\n\n## En attente\n\n## En cours\n\n")
        (tmp_path / ".koan-stop").write_text("STOP")
        with patch("app.awake.MISSIONS_FILE", missions_file), \
             patch("app.awake.KOAN_ROOT", tmp_path):
            status = _build_status()

        assert "ARRÊT DEMANDÉ" in status or "stop" in status.lower()

    @patch("app.awake.MISSIONS_FILE")
    def test_status_with_loop_status(self, mock_file, tmp_path):
        missions_file = tmp_path / "missions.md"
        missions_file.write_text("# Missions\n\n## En attente\n\n## En cours\n\n")
        (tmp_path / ".koan-status").write_text("Run 3/20")
        with patch("app.awake.MISSIONS_FILE", missions_file), \
             patch("app.awake.KOAN_ROOT", tmp_path):
            status = _build_status()

        assert "Run 3/20" in status


# ---------------------------------------------------------------------------
# handle_command
# ---------------------------------------------------------------------------

class TestHandleCommand:
    @patch("app.awake.send_telegram")
    def test_stop_creates_file(self, mock_send, tmp_path):
        with patch("app.awake.KOAN_ROOT", tmp_path):
            handle_command("/stop")
        assert (tmp_path / ".koan-stop").exists()
        mock_send.assert_called_once()

    @patch("app.awake._build_status", return_value="📊 Kōan Status\nAll clear")
    @patch("app.awake.send_telegram")
    def test_status_calls_build_status(self, mock_send, mock_build):
        handle_command("/status")
        mock_build.assert_called_once()
        mock_send.assert_called_once_with("📊 Kōan Status\nAll clear")

    @patch("app.awake.handle_resume")
    def test_resume_delegates(self, mock_resume):
        handle_command("/resume")
        mock_resume.assert_called_once()

    @patch("app.awake.send_telegram")
    def test_verbose_creates_file(self, mock_send, tmp_path):
        with patch("app.awake.KOAN_ROOT", tmp_path):
            handle_command("/verbose")
        assert (tmp_path / ".koan-verbose").exists()
        mock_send.assert_called_once()
        assert "Verbose" in mock_send.call_args[0][0] or "verbose" in mock_send.call_args[0][0].lower()

    @patch("app.awake.send_telegram")
    def test_silent_removes_verbose_file(self, mock_send, tmp_path):
        verbose_file = tmp_path / ".koan-verbose"
        verbose_file.write_text("VERBOSE")
        with patch("app.awake.KOAN_ROOT", tmp_path):
            handle_command("/silent")
        assert not verbose_file.exists()
        mock_send.assert_called_once()

    @patch("app.awake.send_telegram")
    def test_silent_when_already_silent(self, mock_send, tmp_path):
        with patch("app.awake.KOAN_ROOT", tmp_path):
            handle_command("/silent")
        mock_send.assert_called_once()
        assert "silent" in mock_send.call_args[0][0].lower() or "Déjà" in mock_send.call_args[0][0]

    @patch("app.awake.handle_chat")
    def test_unknown_command_falls_to_chat(self, mock_chat):
        handle_command("/unknown")
        mock_chat.assert_called_once_with("/unknown")


# ---------------------------------------------------------------------------
# handle_resume
# ---------------------------------------------------------------------------

class TestHandleResume:
    @patch("app.awake.send_telegram")
    def test_no_quota_file(self, mock_send, tmp_path):
        with patch("app.awake.KOAN_ROOT", tmp_path):
            handle_resume()
        assert "No pause or quota hold" in mock_send.call_args[0][0]

    @patch("app.awake.send_telegram")
    def test_likely_reset(self, mock_send, tmp_path):
        quota_file = tmp_path / ".koan-quota-reset"
        old_ts = str(int(time.time()) - 3 * 3600)  # 3 hours ago
        quota_file.write_text(f"resets 7pm (Europe/Paris)\n{old_ts}")
        with patch("app.awake.KOAN_ROOT", tmp_path):
            handle_resume()
        assert not quota_file.exists()
        assert "Quota likely reset" in mock_send.call_args[0][0]

    @patch("app.awake.send_telegram")
    def test_not_yet_reset(self, mock_send, tmp_path):
        quota_file = tmp_path / ".koan-quota-reset"
        recent_ts = str(int(time.time()) - 30 * 60)  # 30 min ago
        quota_file.write_text(f"resets 7pm (Europe/Paris)\n{recent_ts}")
        with patch("app.awake.KOAN_ROOT", tmp_path):
            handle_resume()
        assert quota_file.exists()
        assert "not reset yet" in mock_send.call_args[0][0]

    @patch("app.awake.send_telegram")
    def test_corrupt_quota_file(self, mock_send, tmp_path):
        quota_file = tmp_path / ".koan-quota-reset"
        quota_file.write_text("garbage\nnot-a-number")
        with patch("app.awake.KOAN_ROOT", tmp_path):
            handle_resume()
        assert "Error" in mock_send.call_args[0][0]


# ---------------------------------------------------------------------------
# handle_chat
# ---------------------------------------------------------------------------

class TestHandleChat:
    @patch("app.awake.save_telegram_message")
    @patch("app.awake.load_recent_telegram_history", return_value=[])
    @patch("app.awake.format_conversation_history", return_value="")
    @patch("app.awake.get_tools_description", return_value="")
    @patch("app.awake.get_chat_tools", return_value="")
    @patch("app.awake.send_telegram", return_value=True)
    @patch("app.awake.subprocess.run")
    def test_successful_chat(self, mock_run, mock_send, mock_tools,
                             mock_tools_desc, mock_fmt, mock_hist, mock_save, tmp_path):
        mock_run.return_value = MagicMock(stdout="Hello back!", returncode=0)
        journal_dir = tmp_path / "journal" / "2026-02-01"
        journal_dir.mkdir(parents=True)
        with patch("app.awake.INSTANCE_DIR", tmp_path), \
             patch("app.awake.KOAN_ROOT", tmp_path), \
             patch("app.awake.PROJECT_PATH", ""), \
             patch("app.awake.TELEGRAM_HISTORY_FILE", tmp_path / "history.jsonl"), \
             patch("app.awake.SOUL", "test soul"), \
             patch("app.awake.SUMMARY", "test summary"):
            handle_chat("hello")
        mock_send.assert_called_once_with("Hello back!")
        # Saved both user and assistant messages
        assert mock_save.call_count == 2

    @patch("app.awake.save_telegram_message")
    @patch("app.awake.load_recent_telegram_history", return_value=[])
    @patch("app.awake.format_conversation_history", return_value="")
    @patch("app.awake.get_tools_description", return_value="")
    @patch("app.awake.get_chat_tools", return_value="")
    @patch("app.awake.send_telegram")
    @patch("app.awake.subprocess.run")
    def test_chat_timeout(self, mock_run, mock_send, mock_tools,
                          mock_tools_desc, mock_fmt, mock_hist, mock_save, tmp_path):
        mock_run.side_effect = subprocess.TimeoutExpired("claude", 180)
        with patch("app.awake.INSTANCE_DIR", tmp_path), \
             patch("app.awake.KOAN_ROOT", tmp_path), \
             patch("app.awake.PROJECT_PATH", ""), \
             patch("app.awake.TELEGRAM_HISTORY_FILE", tmp_path / "history.jsonl"), \
             patch("app.awake.SOUL", ""), \
             patch("app.awake.SUMMARY", ""), \
             patch("app.awake.CHAT_TIMEOUT", 180):
            handle_chat("complex question")
        mock_send.assert_called_once()
        assert "Timeout" in mock_send.call_args[0][0]

    @patch("app.awake.save_telegram_message")
    @patch("app.awake.load_recent_telegram_history", return_value=[])
    @patch("app.awake.format_conversation_history", return_value="")
    @patch("app.awake.get_tools_description", return_value="")
    @patch("app.awake.get_chat_tools", return_value="")
    @patch("app.awake.send_telegram")
    @patch("app.awake.subprocess.run")
    def test_chat_error_nonzero_exit(self, mock_run, mock_send, mock_tools,
                                     mock_tools_desc, mock_fmt, mock_hist, mock_save, tmp_path):
        mock_run.return_value = MagicMock(stdout="", returncode=1, stderr="API error")
        with patch("app.awake.INSTANCE_DIR", tmp_path), \
             patch("app.awake.KOAN_ROOT", tmp_path), \
             patch("app.awake.PROJECT_PATH", ""), \
             patch("app.awake.TELEGRAM_HISTORY_FILE", tmp_path / "history.jsonl"), \
             patch("app.awake.SOUL", ""), \
             patch("app.awake.SUMMARY", ""):
            handle_chat("hello")
        mock_send.assert_called_once()
        assert "couldn't formulate" in mock_send.call_args[0][0]

    @patch("app.awake.save_telegram_message")
    @patch("app.awake.load_recent_telegram_history", return_value=[])
    @patch("app.awake.format_conversation_history", return_value="")
    @patch("app.awake.get_tools_description", return_value="")
    @patch("app.awake.get_chat_tools", return_value="")
    @patch("app.awake.send_telegram", return_value=True)
    @patch("app.awake.subprocess.run")
    def test_chat_reads_journal_flat_fallback(self, mock_run, mock_send, mock_tools,
                                              mock_tools_desc, mock_fmt, mock_hist, mock_save, tmp_path):
        """Falls back to flat journal if nested dir doesn't exist."""
        mock_run.return_value = MagicMock(stdout="ok", returncode=0)
        # No nested journal dir — create flat file
        journal_dir = tmp_path / "journal"
        journal_dir.mkdir(parents=True)
        with patch("app.awake.INSTANCE_DIR", tmp_path), \
             patch("app.awake.KOAN_ROOT", tmp_path), \
             patch("app.awake.PROJECT_PATH", ""), \
             patch("app.awake.TELEGRAM_HISTORY_FILE", tmp_path / "history.jsonl"), \
             patch("app.awake.SOUL", ""), \
             patch("app.awake.SUMMARY", ""):
            handle_chat("hi")
        mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# flush_outbox
# ---------------------------------------------------------------------------

class TestFlushOutbox:
    @patch("app.awake._format_outbox_message", return_value="Formatted msg")
    @patch("app.awake.send_telegram", return_value=True)
    def test_flush_formats_and_sends(self, mock_send, mock_fmt, tmp_path):
        outbox = tmp_path / "outbox.md"
        outbox.write_text("Raw message here")
        with patch("app.awake.OUTBOX_FILE", outbox):
            flush_outbox()
        mock_fmt.assert_called_once_with("Raw message here")
        mock_send.assert_called_once_with("Formatted msg")
        assert outbox.read_text() == ""

    @patch("app.awake._format_outbox_message", return_value="Formatted msg")
    @patch("app.awake.send_telegram", return_value=False)
    def test_flush_keeps_on_send_failure(self, mock_send, mock_fmt, tmp_path):
        outbox = tmp_path / "outbox.md"
        outbox.write_text("Important message")
        with patch("app.awake.OUTBOX_FILE", outbox):
            flush_outbox()
        # Message preserved on send failure
        assert outbox.read_text() == "Important message"

    def test_flush_no_file(self, tmp_path):
        outbox = tmp_path / "outbox.md"
        with patch("app.awake.OUTBOX_FILE", outbox):
            flush_outbox()  # Should not raise

    @patch("app.awake._format_outbox_message", return_value="X")
    @patch("app.awake.send_telegram", return_value=True)
    def test_flush_empty_file(self, mock_send, mock_fmt, tmp_path):
        outbox = tmp_path / "outbox.md"
        outbox.write_text("")
        with patch("app.awake.OUTBOX_FILE", outbox):
            flush_outbox()
        mock_send.assert_not_called()

    @patch("app.awake._format_outbox_message", return_value="X")
    @patch("app.awake.send_telegram", return_value=True)
    def test_flush_whitespace_only(self, mock_send, mock_fmt, tmp_path):
        outbox = tmp_path / "outbox.md"
        outbox.write_text("   \n\n  ")
        with patch("app.awake.OUTBOX_FILE", outbox):
            flush_outbox()
        mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# _format_outbox_message
# ---------------------------------------------------------------------------

class TestFormatOutboxMessage:
    @patch("app.awake.format_for_telegram", return_value="Formatted")
    @patch("app.awake.load_memory_context", return_value="mem")
    @patch("app.awake.load_human_prefs", return_value="prefs")
    @patch("app.awake.load_soul", return_value="soul")
    def test_formats_with_context(self, mock_soul, mock_prefs, mock_mem, mock_fmt, tmp_path):
        with patch("app.awake.INSTANCE_DIR", tmp_path):
            result = _format_outbox_message("raw content")
        assert result == "Formatted"
        mock_fmt.assert_called_once_with("raw content", "soul", "prefs", "mem")

    @patch("app.awake.load_soul", side_effect=Exception("load error"))
    def test_fallback_on_error(self, mock_soul, tmp_path):
        with patch("app.awake.INSTANCE_DIR", tmp_path):
            result = _format_outbox_message("raw content")
        assert result == "raw content"


# ---------------------------------------------------------------------------
# handle_message (dispatch)
# ---------------------------------------------------------------------------

class TestHandleMessage:
    @patch("app.awake.handle_command")
    def test_dispatches_command(self, mock_cmd):
        handle_message("/stop")
        mock_cmd.assert_called_once_with("/stop")

    @patch("app.awake.handle_mission")
    def test_dispatches_mission(self, mock_mission):
        handle_message("implement dark mode")
        mock_mission.assert_called_once_with("implement dark mode")

    @patch("app.awake._run_in_worker")
    def test_dispatches_chat(self, mock_worker):
        handle_message("how are you?")
        mock_worker.assert_called_once_with(handle_chat, "how are you?")

    @patch("app.awake.handle_command")
    @patch("app.awake.handle_mission")
    @patch("app.awake._run_in_worker")
    def test_empty_message_ignored(self, mock_worker, mock_mission, mock_cmd):
        handle_message("")
        mock_cmd.assert_not_called()
        mock_mission.assert_not_called()
        mock_worker.assert_not_called()

    @patch("app.awake.handle_command")
    @patch("app.awake.handle_mission")
    @patch("app.awake._run_in_worker")
    def test_whitespace_only_ignored(self, mock_worker, mock_mission, mock_cmd):
        handle_message("   \n  ")
        mock_cmd.assert_not_called()
        mock_mission.assert_not_called()
        mock_worker.assert_not_called()


# ---------------------------------------------------------------------------
# get_updates
# ---------------------------------------------------------------------------

class TestGetUpdates:
    @patch("app.awake.requests.get")
    def test_returns_results(self, mock_get):
        mock_get.return_value = MagicMock()
        mock_get.return_value.json.return_value = {"ok": True, "result": [{"update_id": 1}]}
        result = get_updates()
        assert len(result) == 1
        assert result[0]["update_id"] == 1

    @patch("app.awake.requests.get")
    def test_passes_offset(self, mock_get):
        mock_get.return_value = MagicMock()
        mock_get.return_value.json.return_value = {"ok": True, "result": []}
        get_updates(offset=42)
        _, kwargs = mock_get.call_args
        assert kwargs["params"]["offset"] == 42

    @patch("app.awake.requests.get")
    def test_handles_network_error(self, mock_get):
        import requests as req
        mock_get.side_effect = req.RequestException("timeout")
        result = get_updates()
        assert result == []

    @patch("app.awake.requests.get")
    def test_handles_json_error(self, mock_get):
        mock_get.return_value = MagicMock()
        mock_get.return_value.json.side_effect = ValueError("bad json")
        result = get_updates()
        assert result == []


# ---------------------------------------------------------------------------
# check_config
# ---------------------------------------------------------------------------

class TestCheckConfig:
    def test_exits_without_token(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KOAN_TELEGRAM_TOKEN", "")
        with patch("app.awake.BOT_TOKEN", ""), \
             patch("app.awake.CHAT_ID", "123"), \
             pytest.raises(SystemExit):
            check_config()

    def test_exits_without_chat_id(self, monkeypatch, tmp_path):
        with patch("app.awake.BOT_TOKEN", "token"), \
             patch("app.awake.CHAT_ID", ""), \
             pytest.raises(SystemExit):
            check_config()

    def test_exits_without_instance_dir(self, tmp_path):
        with patch("app.awake.BOT_TOKEN", "token"), \
             patch("app.awake.CHAT_ID", "123"), \
             patch("app.awake.INSTANCE_DIR", tmp_path / "nonexistent"), \
             pytest.raises(SystemExit):
            check_config()

    def test_passes_with_valid_config(self, tmp_path):
        inst = tmp_path / "instance"
        inst.mkdir()
        with patch("app.awake.BOT_TOKEN", "token"), \
             patch("app.awake.CHAT_ID", "123"), \
             patch("app.awake.INSTANCE_DIR", inst):
            check_config()  # Should not raise


# ---------------------------------------------------------------------------
# main() loop
# ---------------------------------------------------------------------------

class TestMainLoop:
    """Test the main polling loop behavior."""

    TEST_CHAT_ID = "123456789"

    @patch("app.awake.write_heartbeat")
    @patch("app.awake.flush_outbox")
    @patch("app.awake.handle_message")
    @patch("app.awake.get_updates")
    @patch("app.awake.check_config")
    @patch("app.awake.CHAT_ID", TEST_CHAT_ID)
    @patch("app.awake.time.sleep", side_effect=StopIteration)  # Break after first iteration
    def test_main_processes_updates(self, mock_sleep, mock_config, mock_updates,
                                    mock_handle, mock_flush, mock_heartbeat):
        """main() fetches updates, dispatches messages, flushes outbox, writes heartbeat."""
        from app.awake import main
        mock_updates.return_value = [
            {"update_id": 100, "message": {"text": "hello", "chat": {"id": int(self.TEST_CHAT_ID)}}}
        ]
        with pytest.raises(StopIteration):
            main()
        mock_config.assert_called_once()
        mock_updates.assert_called_once_with(None)
        mock_handle.assert_called_once_with("hello")
        mock_flush.assert_called_once()
        mock_heartbeat.assert_called()

    @patch("app.awake.write_heartbeat")
    @patch("app.awake.flush_outbox")
    @patch("app.awake.handle_message")
    @patch("app.awake.get_updates")
    @patch("app.awake.check_config")
    @patch("app.awake.time.sleep", side_effect=StopIteration)
    def test_main_ignores_wrong_chat_id(self, mock_sleep, mock_config, mock_updates,
                                         mock_handle, mock_flush, mock_heartbeat):
        """Messages from other chat IDs are ignored."""
        from app.awake import main
        mock_updates.return_value = [
            {"update_id": 100, "message": {"text": "hello", "chat": {"id": 999999}}}
        ]
        with pytest.raises(StopIteration):
            main()
        mock_handle.assert_not_called()

    @patch("app.awake.write_heartbeat")
    @patch("app.awake.flush_outbox")
    @patch("app.awake.handle_message")
    @patch("app.awake.get_updates")
    @patch("app.awake.check_config")
    @patch("app.awake.CHAT_ID", TEST_CHAT_ID)
    @patch("app.awake.time.sleep")
    def test_main_updates_offset(self, mock_sleep, mock_config, mock_updates,
                                  mock_handle, mock_flush, mock_heartbeat):
        """Offset advances to update_id + 1 after processing."""
        from app.awake import main
        test_chat_id = self.TEST_CHAT_ID
        call_count = [0]
        def side_effect(offset=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return [{"update_id": 42, "message": {"text": "hi", "chat": {"id": int(test_chat_id)}}}]
            raise StopIteration  # Stop on second get_updates call
        mock_updates.side_effect = side_effect

        with pytest.raises(StopIteration):
            main()
        assert mock_updates.call_count == 2
        mock_updates.assert_called_with(43)

    @patch("app.awake.write_heartbeat")
    @patch("app.awake.flush_outbox")
    @patch("app.awake.handle_message")
    @patch("app.awake.get_updates", return_value=[])
    @patch("app.awake.check_config")
    @patch("app.awake.time.sleep", side_effect=StopIteration)
    def test_main_empty_updates_still_flushes(self, mock_sleep, mock_config, mock_updates,
                                               mock_handle, mock_flush, mock_heartbeat):
        """Even with no updates, outbox is flushed and heartbeat written."""
        from app.awake import main
        with pytest.raises(StopIteration):
            main()
        mock_handle.assert_not_called()
        mock_flush.assert_called_once()
        mock_heartbeat.assert_called()

    @patch("app.awake.write_heartbeat")
    @patch("app.awake.flush_outbox")
    @patch("app.awake.handle_message")
    @patch("app.awake.get_updates")
    @patch("app.awake.check_config")
    @patch("app.awake.CHAT_ID", TEST_CHAT_ID)
    @patch("app.awake.time.sleep", side_effect=StopIteration)
    def test_main_skips_updates_without_text(self, mock_sleep, mock_config, mock_updates,
                                              mock_handle, mock_flush, mock_heartbeat):
        """Updates without text field (e.g., photo, sticker) are ignored."""
        from app.awake import main
        mock_updates.return_value = [
            {"update_id": 100, "message": {"chat": {"id": int(self.TEST_CHAT_ID)}}}  # no text
        ]
        with pytest.raises(StopIteration):
            main()
        mock_handle.assert_not_called()

    @patch("app.awake.write_heartbeat")
    @patch("app.awake.flush_outbox")
    @patch("app.awake.handle_message")
    @patch("app.awake.get_updates", side_effect=KeyboardInterrupt)
    @patch("app.awake.check_config")
    def test_main_ctrl_c_exits_gracefully(self, mock_config, mock_updates,
                                           mock_handle, mock_flush, mock_heartbeat,
                                           capsys):
        """CTRL-C (KeyboardInterrupt) exits cleanly without traceback."""
        from app.awake import main
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "Shutting down" in captured.out

    @patch("app.awake.write_heartbeat")
    @patch("app.awake.flush_outbox")
    @patch("app.awake.handle_message")
    @patch("app.awake.get_updates", return_value=[])
    @patch("app.awake.check_config")
    @patch("app.awake.CHAT_ID", TEST_CHAT_ID)
    @patch("app.awake.time.sleep", side_effect=KeyboardInterrupt)
    def test_main_ctrl_c_during_sleep_exits_gracefully(self, mock_sleep, mock_config,
                                                        mock_updates, mock_handle,
                                                        mock_flush, mock_heartbeat,
                                                        capsys):
        """CTRL-C during sleep between polls also exits cleanly."""
        from app.awake import main
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "Shutting down" in captured.out


# ---------------------------------------------------------------------------
# /pause command
# ---------------------------------------------------------------------------

class TestPauseCommand:
    @patch("app.awake.send_telegram")
    def test_pause_creates_file(self, mock_send, tmp_path):
        with patch("app.awake.KOAN_ROOT", tmp_path):
            handle_command("/pause")
        assert (tmp_path / ".koan-pause").exists()
        mock_send.assert_called_once()
        assert "paused" in mock_send.call_args[0][0].lower()

    @patch("app.awake.send_telegram")
    def test_pause_already_paused(self, mock_send, tmp_path):
        (tmp_path / ".koan-pause").write_text("PAUSE")
        with patch("app.awake.KOAN_ROOT", tmp_path):
            handle_command("/pause")
        assert "already paused" in mock_send.call_args[0][0].lower()

    @patch("app.awake.send_telegram")
    def test_resume_clears_pause(self, mock_send, tmp_path):
        (tmp_path / ".koan-pause").write_text("PAUSE")
        with patch("app.awake.KOAN_ROOT", tmp_path):
            handle_resume()
        assert not (tmp_path / ".koan-pause").exists()
        assert "unpaused" in mock_send.call_args[0][0].lower()

    @patch("app.awake.send_telegram")
    def test_resume_pause_takes_priority_over_quota(self, mock_send, tmp_path):
        """If both pause and quota files exist, /resume clears pause first."""
        (tmp_path / ".koan-pause").write_text("PAUSE")
        (tmp_path / ".koan-quota-reset").write_text("resets 7pm\n0")
        with patch("app.awake.KOAN_ROOT", tmp_path):
            handle_resume()
        assert not (tmp_path / ".koan-pause").exists()
        assert (tmp_path / ".koan-quota-reset").exists()
        assert "unpaused" in mock_send.call_args[0][0].lower()

    @patch("app.awake.send_telegram")
    def test_resume_with_quota_reason(self, mock_send, tmp_path):
        """Resume cleans up both pause and pause-reason files, reports quota reason."""
        (tmp_path / ".koan-pause").write_text("PAUSE")
        (tmp_path / ".koan-pause-reason").write_text("quota\n1234567890")
        with patch("app.awake.KOAN_ROOT", tmp_path):
            handle_resume()
        assert not (tmp_path / ".koan-pause").exists()
        assert not (tmp_path / ".koan-pause-reason").exists()
        assert "quota" in mock_send.call_args[0][0].lower()

    @patch("app.awake.send_telegram")
    def test_resume_with_max_runs_reason(self, mock_send, tmp_path):
        """Resume cleans up both files and reports max_runs reason."""
        (tmp_path / ".koan-pause").write_text("PAUSE")
        (tmp_path / ".koan-pause-reason").write_text("max_runs\n1234567890")
        with patch("app.awake.KOAN_ROOT", tmp_path):
            handle_resume()
        assert not (tmp_path / ".koan-pause").exists()
        assert not (tmp_path / ".koan-pause-reason").exists()
        assert "max_runs" in mock_send.call_args[0][0].lower()

    @patch("app.awake.send_telegram")
    def test_resume_pause_without_reason(self, mock_send, tmp_path):
        """Resume with pause file but no reason file (manual /pause)."""
        (tmp_path / ".koan-pause").write_text("PAUSE")
        with patch("app.awake.KOAN_ROOT", tmp_path):
            handle_resume()
        assert not (tmp_path / ".koan-pause").exists()
        # Should say "unpaused" without specific reason
        assert "unpaused" in mock_send.call_args[0][0].lower()

    @patch("app.awake.send_telegram")
    def test_status_shows_pause(self, mock_send, tmp_path):
        (tmp_path / ".koan-pause").write_text("PAUSE")
        instance = tmp_path / "instance"
        instance.mkdir()
        missions = instance / "missions.md"
        missions.write_text("# Missions\n\n## En attente\n\n## En cours\n\n## Terminées\n")
        with patch("app.awake.KOAN_ROOT", tmp_path), \
             patch("app.awake.MISSIONS_FILE", missions):
            status = _build_status()
        assert "PAUSE" in status or "pause" in status.lower()


# ---------------------------------------------------------------------------
# handle_chat — lite retry error distinction
# ---------------------------------------------------------------------------

class TestChatLiteRetryErrors:
    @patch("app.awake.save_telegram_message")
    @patch("app.awake.load_recent_telegram_history", return_value=[])
    @patch("app.awake.format_conversation_history", return_value="")
    @patch("app.awake.get_tools_description", return_value="")
    @patch("app.awake.get_chat_tools", return_value="")
    @patch("app.awake.send_telegram")
    @patch("app.awake.subprocess.run")
    def test_lite_retry_non_timeout_error(self, mock_run, mock_send, mock_tools,
                                           mock_tools_desc, mock_fmt, mock_hist, mock_save, tmp_path):
        """Non-timeout error on lite retry should say 'something went wrong', not 'timeout'."""
        mock_run.side_effect = [
            subprocess.TimeoutExpired("claude", 180),
            OSError("connection refused"),
        ]
        with patch("app.awake.INSTANCE_DIR", tmp_path), \
             patch("app.awake.KOAN_ROOT", tmp_path), \
             patch("app.awake.PROJECT_PATH", ""), \
             patch("app.awake.TELEGRAM_HISTORY_FILE", tmp_path / "history.jsonl"), \
             patch("app.awake.SOUL", ""), \
             patch("app.awake.SUMMARY", ""), \
             patch("app.awake.CHAT_TIMEOUT", 180):
            handle_chat("complex question")
        assert "went wrong" in mock_send.call_args[0][0].lower()

    @patch("app.awake.save_telegram_message")
    @patch("app.awake.load_recent_telegram_history", return_value=[])
    @patch("app.awake.format_conversation_history", return_value="")
    @patch("app.awake.get_tools_description", return_value="")
    @patch("app.awake.get_chat_tools", return_value="")
    @patch("app.awake.send_telegram")
    @patch("app.awake.subprocess.run")
    def test_lite_retry_timeout_says_timeout(self, mock_run, mock_send, mock_tools,
                                              mock_tools_desc, mock_fmt, mock_hist, mock_save, tmp_path):
        """Timeout on lite retry should still say 'timeout'."""
        mock_run.side_effect = [
            subprocess.TimeoutExpired("claude", 180),
            subprocess.TimeoutExpired("claude", 180),
        ]
        with patch("app.awake.INSTANCE_DIR", tmp_path), \
             patch("app.awake.KOAN_ROOT", tmp_path), \
             patch("app.awake.PROJECT_PATH", ""), \
             patch("app.awake.TELEGRAM_HISTORY_FILE", tmp_path / "history.jsonl"), \
             patch("app.awake.SOUL", ""), \
             patch("app.awake.SUMMARY", ""), \
             patch("app.awake.CHAT_TIMEOUT", 180):
            handle_chat("complex question")
        assert "timeout" in mock_send.call_args[0][0].lower()


class TestCleanChatResponse:
    """Test _clean_chat_response strips errors, markdown, and truncates."""

    def test_strips_max_turns_error(self):
        text = "Some reply\nError: Reached max turns (1)\nMore text"
        assert "max turns" not in _clean_chat_response(text)
        assert "Some reply" in _clean_chat_response(text)

    def test_strips_markdown(self):
        text = "**Bold** and ```code``` and __underline__ and ~~strike~~"
        result = _clean_chat_response(text)
        assert "**" not in result
        assert "```" not in result
        assert "__" not in result
        assert "~~" not in result

    def test_strips_headings(self):
        text = "## Heading\nContent"
        result = _clean_chat_response(text)
        assert "##" not in result
        assert "Content" in result

    def test_truncates_long_messages(self):
        text = "x" * 2500
        result = _clean_chat_response(text)
        assert len(result) <= 2000
        assert result.endswith("...")

    def test_empty_after_cleanup(self):
        text = "Error: Reached max turns (1)"
        assert _clean_chat_response(text) == ""

    def test_preserves_normal_text(self):
        text = "Tout va bien, j'ai fini le travail."
        assert _clean_chat_response(text) == text


# ---------------------------------------------------------------------------
# /help
# ---------------------------------------------------------------------------

class TestHandleHelp:
    @patch("app.awake.send_telegram")
    def test_help_sends_command_list(self, mock_send):
        _handle_help()
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        assert "/help" in msg
        assert "/status" in msg
        assert "/usage" in msg
        assert "/stop" in msg
        assert "/pause" in msg
        assert "/resume" in msg

    @patch("app.awake.send_telegram")
    def test_help_mentions_mission_syntax(self, mock_send):
        _handle_help()
        msg = mock_send.call_args[0][0]
        assert "mission" in msg.lower()

    @patch("app.awake.send_telegram")
    def test_help_mentions_chat_command(self, mock_send):
        _handle_help()
        msg = mock_send.call_args[0][0]
        assert "/chat" in msg

    @patch("app.awake._handle_help")
    def test_handle_command_routes_help(self, mock_help):
        handle_command("/help")
        mock_help.assert_called_once()


# ---------------------------------------------------------------------------
# /usage
# ---------------------------------------------------------------------------

class TestHandleUsage:
    @patch("app.awake.send_telegram")
    @patch("subprocess.run")
    def test_usage_calls_claude_with_context(self, mock_run, mock_send, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="Quota 60%, 3 missions en cours.")
        instance = tmp_path / "instance"
        instance.mkdir()
        usage_file = instance / "usage.md"
        usage_file.write_text("Session (5hr) : 60% (reset in 2h)\nWeekly (7 day) : 40% (Resets in 3d)")
        missions_file = instance / "missions.md"
        missions_file.write_text("## En attente\n\n- fix bug\n\n## En cours\n\n## Terminées\n")
        journal_dir = instance / "journal"
        journal_dir.mkdir()

        with patch("app.awake.INSTANCE_DIR", instance), \
             patch("app.awake.MISSIONS_FILE", missions_file), \
             patch("app.awake.SOUL", "test soul"):
            _handle_usage()

        mock_run.assert_called_once()
        prompt_arg = mock_run.call_args[0][0][2]  # ["claude", "-p", prompt, ...]
        assert "60%" in prompt_arg
        mock_send.assert_called_once_with("Quota 60%, 3 missions en cours.")

    @patch("app.awake.send_telegram")
    @patch("subprocess.run")
    def test_usage_fallback_on_claude_failure(self, mock_run, mock_send, tmp_path):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        instance = tmp_path / "instance"
        instance.mkdir()
        (instance / "usage.md").write_text("Session : 30%")
        missions_file = instance / "missions.md"
        missions_file.write_text("## En attente\n\n## En cours\n\n## Terminées\n")
        journal_dir = instance / "journal"
        journal_dir.mkdir()

        with patch("app.awake.INSTANCE_DIR", instance), \
             patch("app.awake.MISSIONS_FILE", missions_file), \
             patch("app.awake.SOUL", ""):
            _handle_usage()

        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        assert "30%" in msg

    @patch("app.awake.send_telegram")
    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired("claude", 60))
    def test_usage_timeout(self, mock_run, mock_send, tmp_path):
        instance = tmp_path / "instance"
        instance.mkdir()
        journal_dir = instance / "journal"
        journal_dir.mkdir()

        with patch("app.awake.INSTANCE_DIR", instance), \
             patch("app.awake.MISSIONS_FILE", instance / "missions.md"), \
             patch("app.awake.SOUL", ""):
            _handle_usage()

        mock_send.assert_called_once()
        assert "Timeout" in mock_send.call_args[0][0]

    @patch("app.awake._run_in_worker")
    def test_handle_command_routes_usage(self, mock_worker):
        handle_command("/usage")
        mock_worker.assert_called_once_with(_handle_usage)

    @patch("app.awake.send_telegram")
    @patch("subprocess.run")
    def test_usage_includes_pending(self, mock_run, mock_send, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="Run en cours: fix auth")
        instance = tmp_path / "instance"
        instance.mkdir()
        journal_dir = instance / "journal"
        journal_dir.mkdir()
        pending = journal_dir / "pending.md"
        pending.write_text("# Mission: fix auth\n22:00 — Started\n22:01 — Reading code")

        with patch("app.awake.INSTANCE_DIR", instance), \
             patch("app.awake.MISSIONS_FILE", instance / "missions.md"), \
             patch("app.awake.SOUL", ""):
            _handle_usage()

        prompt_arg = mock_run.call_args[0][0][2]
        assert "fix auth" in prompt_arg


# ---------------------------------------------------------------------------
# Pause awareness in chat and status
# ---------------------------------------------------------------------------

class TestPauseAwareness:
    """Tests for pause state visibility in chat and status."""

    @patch("app.awake.MISSIONS_FILE")
    def test_status_shows_pause_at_top(self, mock_file, tmp_path):
        """When paused, status shows pause FIRST, not at the bottom."""
        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n## En attente\n\n- fix bug\n\n## En cours\n\n"
        )
        (tmp_path / ".koan-pause").write_text("PAUSE")

        with patch("app.awake.MISSIONS_FILE", missions_file), \
             patch("app.awake.KOAN_ROOT", tmp_path):
            status = _build_status()

        lines = status.split("\n")
        # Find where pause is mentioned
        pause_line_idx = next(i for i, l in enumerate(lines) if "PAUSE" in l or "pause" in l.lower())
        # Find where missions are mentioned
        mission_line_idx = next((i for i, l in enumerate(lines) if "fix bug" in l), len(lines))
        # Pause should come BEFORE missions
        assert pause_line_idx < mission_line_idx, "Pause status should appear before mission details"

    @patch("app.awake.MISSIONS_FILE")
    def test_status_shows_active_when_running(self, mock_file, tmp_path):
        """When not paused, status shows ACTIF/RUNNING."""
        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n## En attente\n\n## En cours\n\n"
        )
        # No .koan-pause file

        with patch("app.awake.MISSIONS_FILE", missions_file), \
             patch("app.awake.KOAN_ROOT", tmp_path):
            status = _build_status()

        assert "ACTIVE" in status

    @patch("app.awake.save_telegram_message")
    @patch("app.awake.load_recent_telegram_history", return_value=[])
    @patch("app.awake.format_conversation_history", return_value="")
    @patch("app.awake.get_tools_description", return_value="")
    @patch("app.awake.get_chat_tools", return_value="")
    @patch("app.awake.send_telegram", return_value=True)
    @patch("app.awake.subprocess.run")
    def test_chat_prompt_includes_pause_status_when_paused(
        self, mock_run, mock_send, mock_tools, mock_tools_desc, mock_fmt,
        mock_hist, mock_save, tmp_path
    ):
        """Chat prompt should include PAUSED status when .koan-pause exists."""
        from app.awake import _build_chat_prompt

        (tmp_path / ".koan-pause").write_text("PAUSE")
        missions_file = tmp_path / "missions.md"
        missions_file.write_text("# Missions\n\n## En attente\n\n- fix bug\n\n## En cours\n\n")

        with patch("app.awake.INSTANCE_DIR", tmp_path), \
             patch("app.awake.KOAN_ROOT", tmp_path), \
             patch("app.awake.MISSIONS_FILE", missions_file), \
             patch("app.awake.SOUL", "test soul"), \
             patch("app.awake.SUMMARY", ""):
            prompt = _build_chat_prompt("what are you doing?")

        # Prompt should mention pause status
        assert "PAUSED" in prompt or "⏸️" in prompt

    @patch("app.awake.save_telegram_message")
    @patch("app.awake.load_recent_telegram_history", return_value=[])
    @patch("app.awake.format_conversation_history", return_value="")
    @patch("app.awake.get_tools_description", return_value="")
    @patch("app.awake.get_chat_tools", return_value="")
    @patch("app.awake.send_telegram", return_value=True)
    @patch("app.awake.subprocess.run")
    def test_chat_prompt_includes_running_status_when_active(
        self, mock_run, mock_send, mock_tools, mock_tools_desc, mock_fmt,
        mock_hist, mock_save, tmp_path
    ):
        """Chat prompt should include RUNNING status when not paused."""
        from app.awake import _build_chat_prompt

        # No .koan-pause file
        missions_file = tmp_path / "missions.md"
        missions_file.write_text("# Missions\n\n## En attente\n\n- fix bug\n\n## En cours\n\n")

        with patch("app.awake.INSTANCE_DIR", tmp_path), \
             patch("app.awake.KOAN_ROOT", tmp_path), \
             patch("app.awake.MISSIONS_FILE", missions_file), \
             patch("app.awake.SOUL", "test soul"), \
             patch("app.awake.SUMMARY", ""):
            prompt = _build_chat_prompt("what are you doing?")

        # Prompt should mention running status
        assert "RUNNING" in prompt or "▶️" in prompt


class TestChatToolsSecurity:
    """Tests verifying chat security (restricted tools)."""

    def test_chat_tools_excludes_bash_by_default(self):
        """Chat tools should NOT include Bash by default (prompt injection protection)."""
        from app.utils import get_chat_tools
        with patch("app.utils.load_config", return_value={}):
            tools = get_chat_tools()
        assert "Bash" not in tools
        assert "Edit" not in tools
        assert "Write" not in tools

    def test_mission_tools_includes_bash(self):
        """Mission tools should include Bash for code execution."""
        from app.utils import get_mission_tools
        with patch("app.utils.load_config", return_value={}):
            tools = get_mission_tools()
        assert "Bash" in tools

    @patch("app.awake.save_telegram_message")
    @patch("app.awake.load_recent_telegram_history", return_value=[])
    @patch("app.awake.format_conversation_history", return_value="")
    @patch("app.awake.get_tools_description", return_value="")
    @patch("app.awake.get_chat_tools", return_value="Read,Glob,Grep")  # Restricted!
    @patch("app.awake.send_telegram", return_value=True)
    @patch("app.awake.subprocess.run")
    def test_handle_chat_uses_chat_tools_not_mission_tools(
        self, mock_run, mock_send, mock_tools, mock_tools_desc, mock_fmt,
        mock_hist, mock_save, tmp_path
    ):
        """handle_chat() should use get_chat_tools(), not get_mission_tools()."""
        mock_run.return_value = MagicMock(stdout="Response", returncode=0)

        with patch("app.awake.INSTANCE_DIR", tmp_path), \
             patch("app.awake.KOAN_ROOT", tmp_path), \
             patch("app.awake.PROJECT_PATH", ""), \
             patch("app.awake.TELEGRAM_HISTORY_FILE", tmp_path / "history.jsonl"), \
             patch("app.awake.SOUL", ""), \
             patch("app.awake.SUMMARY", ""):
            handle_chat("test message")

        # Verify the claude call uses the restricted tools
        call_args = mock_run.call_args[0][0]
        allowed_idx = call_args.index("--allowedTools")
        tools_arg = call_args[allowed_idx + 1]
        assert tools_arg == "Read,Glob,Grep"
        assert "Bash" not in tools_arg
# ---------------------------------------------------------------------------
# /mission command
# ---------------------------------------------------------------------------

class TestHandleMissionCommand:
    """Test /mission command — parity with 'mission:' keyword."""

    @patch("app.awake.send_telegram")
    def test_bare_mission_shows_usage(self, mock_send):
        _handle_mission_command("/mission")
        msg = mock_send.call_args[0][0]
        assert "Usage" in msg

    @patch("app.awake.handle_mission")
    @patch("app.awake.get_known_projects", return_value=["koan"])
    def test_single_project_auto_proceeds(self, mock_projects, mock_mission):
        _handle_mission_command("/mission fix the login bug")
        mock_mission.assert_called_once_with("fix the login bug")

    @patch("app.awake.send_telegram")
    @patch("app.awake.get_known_projects", return_value=["koan", "webapp"])
    def test_multi_project_asks_user(self, mock_projects, mock_send):
        _handle_mission_command("/mission fix the login bug")
        msg = mock_send.call_args[0][0]
        assert "Which project" in msg
        assert "koan" in msg
        assert "webapp" in msg

    @patch("app.awake.handle_mission")
    @patch("app.awake.get_known_projects", return_value=["koan", "webapp"])
    def test_project_tag_bypasses_ask(self, mock_projects, mock_mission):
        _handle_mission_command("/mission [project:koan] fix the login bug")
        mock_mission.assert_called_once_with("[project:koan] fix the login bug")

    @patch("app.awake.handle_mission")
    def test_mission_colon_format(self, mock_mission):
        _handle_mission_command("/mission: fix the login bug")
        mock_mission.assert_called_once_with("fix the login bug")

    @patch("app.awake._handle_mission_command")
    def test_handle_command_routes_mission(self, mock_handler):
        handle_command("/mission fix the bug")
        mock_handler.assert_called_once_with("/mission fix the bug")

    @patch("app.awake.send_telegram")
    def test_whitespace_only_shows_usage(self, mock_send):
        _handle_mission_command("/mission   ")
        msg = mock_send.call_args[0][0]
        assert "Usage" in msg

    @patch("app.awake.handle_mission")
    @patch("app.awake.get_known_projects", return_value=[])
    def test_no_known_projects_proceeds(self, mock_projects, mock_mission):
        _handle_mission_command("/mission fix the login bug")
        mock_mission.assert_called_once_with("fix the login bug")


class TestHandleHelpIncludesMission:
    @patch("app.awake.send_telegram")
    def test_help_mentions_mission_command(self, mock_send):
        _handle_help()
        msg = mock_send.call_args[0][0]
        assert "/mission" in msg
# /log command
# ---------------------------------------------------------------------------

class TestHandleLog:
    """Test /log and /journal command handler."""

    @patch("app.awake.send_telegram")
    def test_log_project_today(self, mock_send, tmp_path):
        """'/log koan' shows today's journal for koan."""
        from datetime import date
        d = tmp_path / "journal" / date.today().strftime("%Y-%m-%d")
        d.mkdir(parents=True)
        (d / "koan.md").write_text("## Session 29\nDid work on /log command.")
        with patch("app.awake.INSTANCE_DIR", tmp_path):
            _handle_log("koan")
        msg = mock_send.call_args[0][0]
        assert "koan" in msg
        assert "Did work" in msg

    @patch("app.awake.send_telegram")
    def test_log_no_args_all_projects(self, mock_send, tmp_path):
        """'/log' shows today's journal for all projects."""
        from datetime import date
        d = tmp_path / "journal" / date.today().strftime("%Y-%m-%d")
        d.mkdir(parents=True)
        (d / "koan.md").write_text("koan stuff")
        (d / "web-app.md").write_text("web-app stuff")
        with patch("app.awake.INSTANCE_DIR", tmp_path):
            _handle_log("")
        msg = mock_send.call_args[0][0]
        assert "koan" in msg
        assert "web-app" in msg

    @patch("app.awake.send_telegram")
    def test_log_yesterday(self, mock_send, tmp_path):
        """'/log koan yesterday' shows yesterday's journal."""
        from datetime import date, timedelta
        yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        d = tmp_path / "journal" / yesterday
        d.mkdir(parents=True)
        (d / "koan.md").write_text("Yesterday's work.")
        with patch("app.awake.INSTANCE_DIR", tmp_path):
            _handle_log("koan yesterday")
        msg = mock_send.call_args[0][0]
        assert "Yesterday's work" in msg
        assert yesterday in msg

    @patch("app.awake.send_telegram")
    def test_log_specific_date(self, mock_send, tmp_path):
        """'/log koan 2026-01-15' shows that date's journal."""
        d = tmp_path / "journal" / "2026-01-15"
        d.mkdir(parents=True)
        (d / "koan.md").write_text("Old entry from Jan.")
        with patch("app.awake.INSTANCE_DIR", tmp_path):
            _handle_log("koan 2026-01-15")
        msg = mock_send.call_args[0][0]
        assert "Old entry" in msg
        assert "2026-01-15" in msg

    @patch("app.awake.send_telegram")
    def test_log_no_journal_found(self, mock_send, tmp_path):
        """Shows 'no journal' when nothing exists."""
        (tmp_path / "journal").mkdir()
        with patch("app.awake.INSTANCE_DIR", tmp_path):
            _handle_log("koan")
        msg = mock_send.call_args[0][0]
        assert "No journal" in msg

    @patch("app.awake.send_telegram")
    def test_log_date_only_no_project(self, mock_send, tmp_path):
        """'/log 2026-01-15' shows all projects for that date."""
        d = tmp_path / "journal" / "2026-01-15"
        d.mkdir(parents=True)
        (d / "koan.md").write_text("koan stuff")
        with patch("app.awake.INSTANCE_DIR", tmp_path):
            _handle_log("2026-01-15")
        msg = mock_send.call_args[0][0]
        assert "koan" in msg
        assert "2026-01-15" in msg

    @patch("app.awake.send_telegram")
    def test_log_yesterday_no_project(self, mock_send, tmp_path):
        """'/log yesterday' shows all projects for yesterday."""
        from datetime import date, timedelta
        yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        d = tmp_path / "journal" / yesterday
        d.mkdir(parents=True)
        (d / "koan.md").write_text("yesterday stuff")
        with patch("app.awake.INSTANCE_DIR", tmp_path):
            _handle_log("yesterday")
        msg = mock_send.call_args[0][0]
        assert "yesterday stuff" in msg

    @patch("app.awake._handle_log")
    def test_handle_command_routes_log(self, mock_log):
        """handle_command routes /log to _handle_log."""
        handle_command("/log koan")
        mock_log.assert_called_once_with("koan")

    @patch("app.awake._handle_log")
    def test_handle_command_routes_journal(self, mock_log):
        """handle_command routes /journal to _handle_log."""
        handle_command("/journal koan")
        mock_log.assert_called_once_with("koan")

    @patch("app.awake._handle_log")
    def test_handle_command_routes_log_bare(self, mock_log):
        """handle_command routes bare /log to _handle_log."""
        handle_command("/log")
        mock_log.assert_called_once_with("")

    @patch("app.awake.send_telegram")
    def test_help_mentions_log(self, mock_send):
        """/help output includes /log."""
        _handle_help()
        msg = mock_send.call_args[0][0]
        assert "/log" in msg


# ---------------------------------------------------------------------------
# /language
# ---------------------------------------------------------------------------

class TestHandleLanguage:
    """Tests for /language command."""

    @patch("app.awake._handle_language")
    def test_handle_command_routes_language(self, mock_lang):
        """handle_command routes /language to _handle_language."""
        handle_command("/language english")
        mock_lang.assert_called_once_with("english")

    @patch("app.awake._handle_language")
    def test_handle_command_routes_language_bare(self, mock_lang):
        """handle_command routes bare /language to _handle_language."""
        handle_command("/language")
        mock_lang.assert_called_once_with("")

    @patch("app.awake.send_telegram")
    @patch("app.awake.get_language", return_value="")
    def test_bare_language_shows_usage(self, mock_get, mock_send):
        """Bare /language shows current state and usage."""
        _handle_language("")
        msg = mock_send.call_args[0][0]
        assert "No language override" in msg
        assert "Usage" in msg

    @patch("app.awake.send_telegram")
    @patch("app.awake.get_language", return_value="french")
    def test_bare_language_shows_current(self, mock_get, mock_send):
        """Bare /language shows current language when set."""
        _handle_language("")
        msg = mock_send.call_args[0][0]
        assert "french" in msg

    @patch("app.awake.send_telegram")
    @patch("app.awake.set_language")
    def test_set_language(self, mock_set, mock_send):
        """Setting a language calls set_language."""
        _handle_language("english")
        mock_set.assert_called_once_with("english")
        assert "english" in mock_send.call_args[0][0]

    @patch("app.awake.send_telegram")
    @patch("app.awake.reset_language")
    def test_reset_language(self, mock_reset, mock_send):
        """'reset' arg calls reset_language."""
        _handle_language("reset")
        mock_reset.assert_called_once()
        assert "reset" in mock_send.call_args[0][0].lower()

    @patch("app.awake.send_telegram")
    def test_help_mentions_language(self, mock_send):
        """/help output includes /language."""
        _handle_help()
        msg = mock_send.call_args[0][0]
        assert "/language" in msg
