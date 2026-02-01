"""Tests for koan/utils.py — shared utilities."""
import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Ensure test env vars don't leak."""
    for key in list(os.environ):
        if key.startswith("KOAN_"):
            monkeypatch.delenv(key, raising=False)


class TestLoadDotenv:
    def test_loads_env_file(self, tmp_path, monkeypatch):
        from utils import load_dotenv, KOAN_ROOT

        env_file = tmp_path / ".env"
        env_file.write_text('FOO_TEST=bar\nBAZ_TEST="quoted"\n')

        import utils
        monkeypatch.setattr(utils, "KOAN_ROOT", tmp_path)

        load_dotenv()
        assert os.environ.get("FOO_TEST") == "bar"
        assert os.environ.get("BAZ_TEST") == "quoted"

    def test_skips_comments_and_blanks(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text('# comment\n\nKEY_TEST=val\n')

        import utils
        monkeypatch.setattr(utils, "KOAN_ROOT", tmp_path)

        from utils import load_dotenv
        load_dotenv()
        assert os.environ.get("KEY_TEST") == "val"

    def test_does_not_overwrite_existing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EXISTING_TEST", "original")
        env_file = tmp_path / ".env"
        env_file.write_text("EXISTING_TEST=overwritten\n")

        import utils
        monkeypatch.setattr(utils, "KOAN_ROOT", tmp_path)

        from utils import load_dotenv
        load_dotenv()
        assert os.environ["EXISTING_TEST"] == "original"

    def test_missing_env_file(self, tmp_path, monkeypatch):
        import utils
        monkeypatch.setattr(utils, "KOAN_ROOT", tmp_path)
        # Should not raise
        from utils import load_dotenv
        load_dotenv()


class TestParseProject:
    def test_extracts_project_tag(self):
        from utils import parse_project
        project, text = parse_project("[project:koan] Fix bug")
        assert project == "koan"
        assert text == "Fix bug"

    def test_extracts_projet_tag(self):
        from utils import parse_project
        project, text = parse_project("[projet:anantys] Audit code")
        assert project == "anantys"
        assert text == "Audit code"

    def test_no_tag(self):
        from utils import parse_project
        project, text = parse_project("Just a message")
        assert project is None
        assert text == "Just a message"

    def test_tag_in_middle(self):
        from utils import parse_project
        project, text = parse_project("Fix [project:koan] bug")
        assert project == "koan"
        assert text == "Fix bug"


class TestInsertPendingMission:
    def test_inserts_into_existing_file(self, tmp_path):
        from utils import insert_pending_mission
        missions = tmp_path / "missions.md"
        missions.write_text("# Missions\n\n## En attente\n\n## En cours\n")

        insert_pending_mission(missions, "- New task")
        content = missions.read_text()
        assert "- New task" in content
        assert content.index("- New task") < content.index("## En cours")

    def test_creates_file_if_missing(self, tmp_path):
        from utils import insert_pending_mission
        missions = tmp_path / "missions.md"

        insert_pending_mission(missions, "- First task")
        assert missions.exists()
        content = missions.read_text()
        assert "- First task" in content
        assert "## En attente" in content

    def test_handles_english_sections(self, tmp_path):
        from utils import insert_pending_mission
        missions = tmp_path / "missions.md"
        missions.write_text("# Missions\n\n## Pending\n\n## In Progress\n")

        insert_pending_mission(missions, "- English task")
        content = missions.read_text()
        assert "- English task" in content

    def test_handles_no_pending_section(self, tmp_path):
        from utils import insert_pending_mission
        missions = tmp_path / "missions.md"
        missions.write_text("# Missions\n\n## En cours\n")

        insert_pending_mission(missions, "- Orphan task")
        content = missions.read_text()
        assert "## En attente" in content
        assert "- Orphan task" in content
