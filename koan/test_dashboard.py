"""Tests for koan/dashboard.py"""

import pytest
from pathlib import Path
from unittest.mock import patch

import dashboard


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
    with patch.object(dashboard, "INSTANCE_DIR", instance_dir), \
         patch.object(dashboard, "MISSIONS_FILE", instance_dir / "missions.md"), \
         patch.object(dashboard, "OUTBOX_FILE", instance_dir / "outbox.md"), \
         patch.object(dashboard, "SOUL_FILE", instance_dir / "soul.md"), \
         patch.object(dashboard, "SUMMARY_FILE", instance_dir / "memory" / "summary.md"), \
         patch.object(dashboard, "JOURNAL_DIR", instance_dir / "journal"), \
         patch.object(dashboard, "KOAN_ROOT", tmp_path):
        dashboard.app.config["TESTING"] = True
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
