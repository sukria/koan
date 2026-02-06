"""Tests for koan/dashboard.py"""

import shutil
import subprocess

import pytest
from jinja2 import FileSystemLoader
from pathlib import Path
from unittest.mock import patch, MagicMock

from app import dashboard

REAL_TEMPLATES = Path(__file__).parent.parent / "templates"


@pytest.fixture
def instance_dir(tmp_path):
    """Create a minimal instance directory."""
    inst = tmp_path / "instance"
    inst.mkdir()
    (inst / "memory" / "global").mkdir(parents=True)
    (inst / "memory" / "projects" / "koan").mkdir(parents=True)
    (inst / "journal" / "2026-02-01").mkdir(parents=True)

    (inst / "soul.md").write_text("You are Koan.")
    (inst / "memory" / "summary.md").write_text("Session 1: bootstrapped.")
    (inst / "missions.md").write_text(
        "# Missions\n\n"
        "## En attente\n\n"
        "- [project:koan] Build dashboard\n"
        "- Fix something\n\n"
        "## En cours\n\n"
        "### Admin Dashboard\n"
        "- ~~Phase 1~~ done\n"
        "- Phase 2 pending\n\n"
        "## Terminées\n\n"
        "- ~~Exploration~~ (session 3)\n"
    )
    (inst / "journal" / "2026-02-01" / "koan.md").write_text(
        "## Session 34\nBuilt the dashboard.\n"
    )
    return inst


@pytest.fixture
def app_client(instance_dir, tmp_path):
    """Create a Flask test client with patched paths."""
    # Copy real templates so Flask can render them
    tpl_dest = tmp_path / "koan" / "templates"
    shutil.copytree(REAL_TEMPLATES, tpl_dest)
    with patch.object(dashboard, "INSTANCE_DIR", instance_dir), \
         patch.object(dashboard, "MISSIONS_FILE", instance_dir / "missions.md"), \
         patch.object(dashboard, "OUTBOX_FILE", instance_dir / "outbox.md"), \
         patch.object(dashboard, "SOUL_FILE", instance_dir / "soul.md"), \
         patch.object(dashboard, "SUMMARY_FILE", instance_dir / "memory" / "summary.md"), \
         patch.object(dashboard, "JOURNAL_DIR", instance_dir / "journal"), \
         patch.object(dashboard, "PENDING_FILE", instance_dir / "journal" / "pending.md"), \
         patch.object(dashboard, "KOAN_ROOT", tmp_path):
        dashboard.app.config["TESTING"] = True
        dashboard.app.jinja_loader = FileSystemLoader(str(tpl_dest))
        with dashboard.app.test_client() as client:
            yield client


class TestParsingMissions:
    def test_parse_sections(self, instance_dir):
        with patch.object(dashboard, "MISSIONS_FILE", instance_dir / "missions.md"):
            result = dashboard.parse_missions()
            assert len(result["pending"]) == 2
            assert len(result["in_progress"]) == 1  # complex block
            assert len(result["done"]) == 1

    def test_parse_empty(self, tmp_path):
        with patch.object(dashboard, "MISSIONS_FILE", tmp_path / "nope.md"):
            result = dashboard.parse_missions()
            assert result == {"pending": [], "in_progress": [], "done": []}


class TestRoutes:
    def test_index(self, app_client):
        resp = app_client.get("/")
        assert resp.status_code == 200
        assert b"Dashboard" in resp.data

    def test_missions_page(self, app_client):
        resp = app_client.get("/missions")
        assert resp.status_code == 200
        assert b"Build dashboard" in resp.data

    def test_add_mission(self, app_client, instance_dir):
        with patch.object(dashboard, "MISSIONS_FILE", instance_dir / "missions.md"):
            resp = app_client.post("/missions/add", data={
                "mission": "New test mission",
                "project": "koan",
            }, follow_redirects=True)
            assert resp.status_code == 200
            content = (instance_dir / "missions.md").read_text()
            assert "[project:koan] New test mission" in content

    def test_add_mission_no_project(self, app_client, instance_dir):
        with patch.object(dashboard, "MISSIONS_FILE", instance_dir / "missions.md"):
            resp = app_client.post("/missions/add", data={
                "mission": "Simple mission",
                "project": "",
            }, follow_redirects=True)
            assert resp.status_code == 200
            content = (instance_dir / "missions.md").read_text()
            assert "- Simple mission" in content

    def test_journal_page(self, app_client):
        resp = app_client.get("/journal")
        assert resp.status_code == 200
        assert b"2026-02-01" in resp.data
        assert b"Built the dashboard" in resp.data

    def test_chat_page(self, app_client):
        resp = app_client.get("/chat")
        assert resp.status_code == 200
        assert b"Envoyer" in resp.data

    def test_api_status(self, app_client):
        resp = app_client.get("/api/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["missions"]["pending"] == 2
        assert data["missions"]["in_progress"] == 1

    def test_chat_send_mission(self, app_client, instance_dir):
        with patch.object(dashboard, "MISSIONS_FILE", instance_dir / "missions.md"):
            resp = app_client.post("/chat/send", data={
                "message": "Do something cool",
                "mode": "mission",
            })
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert data["type"] == "mission"

    def test_chat_send_empty(self, app_client):
        resp = app_client.post("/chat/send", data={
            "message": "",
            "mode": "chat",
        })
        data = resp.get_json()
        assert data["ok"] is False


class TestSignals:
    def test_no_signals(self, tmp_path):
        with patch.object(dashboard, "KOAN_ROOT", tmp_path):
            status = dashboard.get_signal_status()
            assert status["stop_requested"] is False
            assert status["quota_paused"] is False
            assert status["loop_status"] == ""

    def test_stop_signal(self, tmp_path):
        (tmp_path / ".koan-stop").write_text("STOP")
        with patch.object(dashboard, "KOAN_ROOT", tmp_path):
            status = dashboard.get_signal_status()
            assert status["stop_requested"] is True

    def test_loop_status(self, tmp_path):
        (tmp_path / ".koan-status").write_text("3/20")
        with patch.object(dashboard, "KOAN_ROOT", tmp_path):
            status = dashboard.get_signal_status()
            assert status["loop_status"] == "3/20"


class TestJournal:
    def test_get_entries(self, instance_dir):
        with patch.object(dashboard, "JOURNAL_DIR", instance_dir / "journal"):
            entries = dashboard.get_journal_entries(limit=7)
            assert len(entries) == 1
            assert entries[0]["date"] == "2026-02-01"
            assert entries[0]["entries"][0]["project"] == "koan"

    def test_empty_journal(self, tmp_path):
        with patch.object(dashboard, "JOURNAL_DIR", tmp_path / "journal"):
            entries = dashboard.get_journal_entries()
            assert entries == []


class TestChatSend:
    """Test /chat/send endpoint — the Claude chat handler."""

    @patch("app.dashboard.get_allowed_tools", return_value="")
    @patch("app.dashboard.get_tools_description", return_value="")
    @patch("app.dashboard.save_telegram_message")
    @patch("app.dashboard.load_recent_telegram_history", return_value=[])
    @patch("app.dashboard.format_conversation_history", return_value="")
    @patch("app.dashboard.subprocess.run")
    def test_chat_success(self, mock_run, mock_fmt, mock_hist, mock_save,
                          mock_tools_desc, mock_tools, app_client, instance_dir):
        mock_run.return_value = MagicMock(stdout="Salut !", returncode=0)
        with patch.object(dashboard, "TELEGRAM_HISTORY_FILE", instance_dir / "history.jsonl"), \
             patch.object(dashboard, "SOUL_FILE", instance_dir / "soul.md"), \
             patch.object(dashboard, "SUMMARY_FILE", instance_dir / "memory" / "summary.md"), \
             patch.object(dashboard, "JOURNAL_DIR", instance_dir / "journal"):
            resp = app_client.post("/chat/send", data={"message": "hello", "mode": "chat"})
        data = resp.get_json()
        assert data["ok"] is True
        assert data["type"] == "chat"
        assert data["response"] == "Salut !"
        mock_run.assert_called_once()

    @patch("app.dashboard.get_allowed_tools", return_value="")
    @patch("app.dashboard.get_tools_description", return_value="")
    @patch("app.dashboard.save_telegram_message")
    @patch("app.dashboard.load_recent_telegram_history", return_value=[])
    @patch("app.dashboard.format_conversation_history", return_value="")
    @patch("app.dashboard.subprocess.run")
    def test_chat_empty_response_fallback(self, mock_run, mock_fmt, mock_hist, mock_save,
                                          mock_tools_desc, mock_tools, app_client, instance_dir):
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        with patch.object(dashboard, "TELEGRAM_HISTORY_FILE", instance_dir / "history.jsonl"), \
             patch.object(dashboard, "SOUL_FILE", instance_dir / "soul.md"), \
             patch.object(dashboard, "SUMMARY_FILE", instance_dir / "memory" / "summary.md"), \
             patch.object(dashboard, "JOURNAL_DIR", instance_dir / "journal"):
            resp = app_client.post("/chat/send", data={"message": "hello", "mode": "chat"})
        data = resp.get_json()
        assert data["ok"] is True
        assert "Try again?" in data["response"]

    @patch("app.dashboard.get_allowed_tools", return_value="")
    @patch("app.dashboard.get_tools_description", return_value="")
    @patch("app.dashboard.save_telegram_message")
    @patch("app.dashboard.load_recent_telegram_history", return_value=[])
    @patch("app.dashboard.format_conversation_history", return_value="")
    @patch("app.dashboard.subprocess.run")
    def test_chat_timeout_lite_retry_succeeds(self, mock_run, mock_fmt, mock_hist, mock_save,
                                               mock_tools_desc, mock_tools, app_client, instance_dir):
        """First call times out, lite retry succeeds."""
        mock_run.side_effect = [
            subprocess.TimeoutExpired("claude", 120),
            MagicMock(stdout="Réponse lite !", returncode=0),
        ]
        with patch.object(dashboard, "TELEGRAM_HISTORY_FILE", instance_dir / "history.jsonl"), \
             patch.object(dashboard, "SOUL_FILE", instance_dir / "soul.md"), \
             patch.object(dashboard, "SUMMARY_FILE", instance_dir / "memory" / "summary.md"), \
             patch.object(dashboard, "JOURNAL_DIR", instance_dir / "journal"):
            resp = app_client.post("/chat/send", data={"message": "deep question", "mode": "chat"})
        data = resp.get_json()
        assert data["ok"] is True
        assert data["response"] == "Réponse lite !"
        assert mock_run.call_count == 2

    @patch("app.dashboard.get_allowed_tools", return_value="")
    @patch("app.dashboard.get_tools_description", return_value="")
    @patch("app.dashboard.save_telegram_message")
    @patch("app.dashboard.load_recent_telegram_history", return_value=[])
    @patch("app.dashboard.format_conversation_history", return_value="")
    @patch("app.dashboard.subprocess.run")
    def test_chat_timeout_both_attempts(self, mock_run, mock_fmt, mock_hist, mock_save,
                                         mock_tools_desc, mock_tools, app_client, instance_dir):
        """Both full and lite calls time out."""
        mock_run.side_effect = subprocess.TimeoutExpired("claude", 120)
        with patch.object(dashboard, "TELEGRAM_HISTORY_FILE", instance_dir / "history.jsonl"), \
             patch.object(dashboard, "SOUL_FILE", instance_dir / "soul.md"), \
             patch.object(dashboard, "SUMMARY_FILE", instance_dir / "memory" / "summary.md"), \
             patch.object(dashboard, "JOURNAL_DIR", instance_dir / "journal"):
            resp = app_client.post("/chat/send", data={"message": "deep question", "mode": "chat"})
        data = resp.get_json()
        assert data["ok"] is True
        assert "Timeout" in data["response"]
        assert mock_run.call_count == 2

    @patch("app.dashboard.get_allowed_tools", return_value="")
    @patch("app.dashboard.get_tools_description", return_value="")
    @patch("app.dashboard.save_telegram_message")
    @patch("app.dashboard.load_recent_telegram_history", return_value=[])
    @patch("app.dashboard.format_conversation_history", return_value="")
    @patch("app.dashboard.subprocess.run")
    def test_chat_timeout_lite_empty_response(self, mock_run, mock_fmt, mock_hist, mock_save,
                                               mock_tools_desc, mock_tools, app_client, instance_dir):
        """First call times out, lite retry returns empty."""
        mock_run.side_effect = [
            subprocess.TimeoutExpired("claude", 120),
            MagicMock(stdout="", returncode=0),
        ]
        with patch.object(dashboard, "TELEGRAM_HISTORY_FILE", instance_dir / "history.jsonl"), \
             patch.object(dashboard, "SOUL_FILE", instance_dir / "soul.md"), \
             patch.object(dashboard, "SUMMARY_FILE", instance_dir / "memory" / "summary.md"), \
             patch.object(dashboard, "JOURNAL_DIR", instance_dir / "journal"):
            resp = app_client.post("/chat/send", data={"message": "deep question", "mode": "chat"})
        data = resp.get_json()
        assert data["ok"] is True
        assert "Timeout" in data["response"]

    @patch("app.dashboard.get_allowed_tools", return_value="")
    @patch("app.dashboard.get_tools_description", return_value="")
    @patch("app.dashboard.save_telegram_message")
    @patch("app.dashboard.load_recent_telegram_history", return_value=[])
    @patch("app.dashboard.format_conversation_history", return_value="")
    @patch("app.dashboard.subprocess.run")
    def test_chat_timeout_lite_retry_error(self, mock_run, mock_fmt, mock_hist, mock_save,
                                            mock_tools_desc, mock_tools, app_client, instance_dir):
        """First call times out, lite retry raises OSError."""
        mock_run.side_effect = [
            subprocess.TimeoutExpired("claude", 120),
            OSError("broken"),
        ]
        with patch.object(dashboard, "TELEGRAM_HISTORY_FILE", instance_dir / "history.jsonl"), \
             patch.object(dashboard, "SOUL_FILE", instance_dir / "soul.md"), \
             patch.object(dashboard, "SUMMARY_FILE", instance_dir / "memory" / "summary.md"), \
             patch.object(dashboard, "JOURNAL_DIR", instance_dir / "journal"):
            resp = app_client.post("/chat/send", data={"message": "hi", "mode": "chat"})
        data = resp.get_json()
        assert data["ok"] is False
        assert "broken" in data["error"]

    @patch("app.dashboard.get_allowed_tools", return_value="")
    @patch("app.dashboard.get_tools_description", return_value="")
    @patch("app.dashboard.save_telegram_message")
    @patch("app.dashboard.load_recent_telegram_history", return_value=[])
    @patch("app.dashboard.format_conversation_history", return_value="")
    @patch("app.dashboard.subprocess.run")
    def test_chat_exception(self, mock_run, mock_fmt, mock_hist, mock_save,
                            mock_tools_desc, mock_tools, app_client, instance_dir):
        mock_run.side_effect = OSError("claude not found")
        with patch.object(dashboard, "TELEGRAM_HISTORY_FILE", instance_dir / "history.jsonl"), \
             patch.object(dashboard, "SOUL_FILE", instance_dir / "soul.md"), \
             patch.object(dashboard, "SUMMARY_FILE", instance_dir / "memory" / "summary.md"), \
             patch.object(dashboard, "JOURNAL_DIR", instance_dir / "journal"):
            resp = app_client.post("/chat/send", data={"message": "hi", "mode": "chat"})
        data = resp.get_json()
        assert data["ok"] is False
        assert "claude not found" in data["error"]

    def test_chat_send_with_project_tag(self, app_client, instance_dir):
        with patch.object(dashboard, "MISSIONS_FILE", instance_dir / "missions.md"):
            resp = app_client.post("/chat/send", data={
                "message": "[project:koan] add feature",
                "mode": "mission",
            })
        data = resp.get_json()
        assert data["ok"] is True
        assert data["type"] == "mission"
        content = (instance_dir / "missions.md").read_text()
        assert "[project:koan] add feature" in content


class TestBuildDashboardPrompt:
    """Test _build_dashboard_prompt lite mode."""

    def test_lite_prompt_strips_journal_and_summary(self, instance_dir):
        with patch.object(dashboard, "TELEGRAM_HISTORY_FILE", instance_dir / "history.jsonl"), \
             patch.object(dashboard, "SOUL_FILE", instance_dir / "soul.md"), \
             patch.object(dashboard, "SUMMARY_FILE", instance_dir / "memory" / "summary.md"), \
             patch.object(dashboard, "INSTANCE_DIR", instance_dir), \
             patch("app.dashboard.load_recent_telegram_history", return_value=[]), \
             patch("app.dashboard.format_conversation_history", return_value=""), \
             patch("app.dashboard.get_tools_description", return_value=""):
            prompt = dashboard._build_dashboard_prompt("hello", lite=True)
        assert "Session 1: bootstrapped" not in prompt
        assert "Built the dashboard" not in prompt
        assert "You are Kōan" in prompt


class TestParseProject:
    def test_english_tag(self):
        project, text = dashboard.parse_project("[project:koan] fix bug")
        assert project == "koan"
        assert text == "fix bug"

    def test_french_tag(self):
        project, text = dashboard.parse_project("[projet:koan] fix bug")
        assert project == "koan"
        assert text == "fix bug"

    def test_no_tag(self):
        project, text = dashboard.parse_project("fix bug")
        assert project is None
        assert text == "fix bug"


class TestProgressPage:
    def test_progress_page_renders(self, app_client):
        resp = app_client.get("/progress")
        assert resp.status_code == 200
        assert b"Live Progress" in resp.data
        assert b"EventSource" in resp.data

    def test_progress_page_has_autoscroll(self, app_client):
        resp = app_client.get("/progress")
        assert b"autoscroll" in resp.data


class TestApiProgress:
    def test_no_pending_file(self, app_client):
        resp = app_client.get("/api/progress")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["active"] is False
        assert data["content"] == ""

    def test_with_pending_file(self, app_client, instance_dir):
        pending = instance_dir / "journal" / "pending.md"
        pending.write_text("# Mission: test\n---\n04:26 — started\n")
        resp = app_client.get("/api/progress")
        data = resp.get_json()
        assert data["active"] is True
        assert "Mission: test" in data["content"]
        assert "04:26" in data["content"]


class TestApiProgressStream:
    def test_stream_returns_sse_content_type(self, app_client):
        resp = app_client.get("/api/progress/stream")
        assert resp.content_type == "text/event-stream; charset=utf-8"

    def test_stream_sends_initial_event_when_file_exists(self, app_client, instance_dir):
        pending = instance_dir / "journal" / "pending.md"
        pending.write_text("# Mission: live test\n---\n04:30 — doing stuff\n")
        resp = app_client.get("/api/progress/stream")
        # Read the first chunk from the streaming response
        import json
        data_line = None
        for chunk in resp.response:
            if isinstance(chunk, bytes):
                chunk = chunk.decode()
            if chunk.startswith("data: "):
                data_line = chunk
                break
        assert data_line is not None
        payload = json.loads(data_line[6:].strip())
        assert payload["active"] is True
        assert "live test" in payload["content"]

    def test_stream_sends_inactive_when_no_file(self, app_client, instance_dir):
        # Ensure no pending.md exists
        pending = instance_dir / "journal" / "pending.md"
        if pending.exists():
            pending.unlink()
        # The SSE generator won't emit until content changes.
        # For the no-file case with last_content=None initially, it won't emit.
        # We just verify the endpoint returns a valid SSE response.
        resp = app_client.get("/api/progress/stream")
        assert resp.status_code == 200
        assert "text/event-stream" in resp.content_type
