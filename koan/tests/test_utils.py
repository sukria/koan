"""Tests for koan/utils.py — shared utilities."""
import os
import threading
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
        from app.utils import load_dotenv, KOAN_ROOT

        env_file = tmp_path / ".env"
        env_file.write_text('FOO_TEST=bar\nBAZ_TEST="quoted"\n')

        from app import utils
        monkeypatch.setattr(utils, "KOAN_ROOT", tmp_path)

        load_dotenv()
        assert os.environ.get("FOO_TEST") == "bar"
        assert os.environ.get("BAZ_TEST") == "quoted"

    def test_skips_comments_and_blanks(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text('# comment\n\nKEY_TEST=val\n')

        from app import utils
        monkeypatch.setattr(utils, "KOAN_ROOT", tmp_path)

        from app.utils import load_dotenv
        load_dotenv()
        assert os.environ.get("KEY_TEST") == "val"

    def test_does_not_overwrite_existing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EXISTING_TEST", "original")
        env_file = tmp_path / ".env"
        env_file.write_text("EXISTING_TEST=overwritten\n")

        from app import utils
        monkeypatch.setattr(utils, "KOAN_ROOT", tmp_path)

        from app.utils import load_dotenv
        load_dotenv()
        assert os.environ["EXISTING_TEST"] == "original"

    def test_missing_env_file(self, tmp_path, monkeypatch):
        from app import utils
        monkeypatch.setattr(utils, "KOAN_ROOT", tmp_path)
        # Should not raise
        from app.utils import load_dotenv
        load_dotenv()


class TestParseProject:
    def test_extracts_project_tag(self):
        from app.utils import parse_project
        project, text = parse_project("[project:koan] Fix bug")
        assert project == "koan"
        assert text == "Fix bug"

    def test_extracts_projet_tag(self):
        from app.utils import parse_project
        project, text = parse_project("[projet:anantys] Audit code")
        assert project == "anantys"
        assert text == "Audit code"

    def test_no_tag(self):
        from app.utils import parse_project
        project, text = parse_project("Just a message")
        assert project is None
        assert text == "Just a message"

    def test_tag_in_middle(self):
        from app.utils import parse_project
        project, text = parse_project("Fix [project:koan] bug")
        assert project == "koan"
        assert text == "Fix bug"


class TestInsertPendingMission:
    def test_inserts_into_existing_file(self, tmp_path):
        from app.utils import insert_pending_mission
        missions = tmp_path / "missions.md"
        missions.write_text("# Missions\n\n## En attente\n\n## En cours\n")

        insert_pending_mission(missions, "- New task")
        content = missions.read_text()
        assert "- New task" in content
        assert content.index("- New task") < content.index("## En cours")

    def test_creates_file_if_missing(self, tmp_path):
        from app.utils import insert_pending_mission
        missions = tmp_path / "missions.md"

        insert_pending_mission(missions, "- First task")
        assert missions.exists()
        content = missions.read_text()
        assert "- First task" in content
        assert "## En attente" in content

    def test_handles_english_sections(self, tmp_path):
        from app.utils import insert_pending_mission
        missions = tmp_path / "missions.md"
        missions.write_text("# Missions\n\n## Pending\n\n## In Progress\n")

        insert_pending_mission(missions, "- English task")
        content = missions.read_text()
        assert "- English task" in content

    def test_handles_no_pending_section(self, tmp_path):
        from app.utils import insert_pending_mission
        missions = tmp_path / "missions.md"
        missions.write_text("# Missions\n\n## En cours\n")

        insert_pending_mission(missions, "- Orphan task")
        content = missions.read_text()
        assert "## En attente" in content
        assert "- Orphan task" in content

    def test_concurrent_inserts_no_lost_missions(self, tmp_path):
        """Regression: concurrent inserts must not lose missions (TOCTOU fix)."""
        from app.utils import insert_pending_mission
        missions = tmp_path / "missions.md"
        missions.write_text("# Missions\n\n## En attente\n\n## En cours\n\n## Terminées\n")

        num_threads = 8
        errors = []

        def insert_task(i):
            try:
                insert_pending_mission(missions, f"- Task {i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=insert_task, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors during concurrent insert: {errors}"
        content = missions.read_text()
        for i in range(num_threads):
            assert f"- Task {i}" in content, f"Task {i} lost during concurrent insert"



class TestGetJournalFile:
    def test_nested_exists(self, tmp_path):
        from app.utils import get_journal_file
        nested = tmp_path / "journal" / "2026-02-01" / "koan.md"
        nested.parent.mkdir(parents=True)
        nested.write_text("nested content")
        result = get_journal_file(tmp_path, "2026-02-01", "koan")
        assert result == nested

    def test_flat_fallback(self, tmp_path):
        from app.utils import get_journal_file
        flat = tmp_path / "journal" / "2026-02-01.md"
        flat.parent.mkdir(parents=True)
        flat.write_text("flat content")
        result = get_journal_file(tmp_path, "2026-02-01", "koan")
        assert result == flat

    def test_default_nested(self, tmp_path):
        from app.utils import get_journal_file
        (tmp_path / "journal").mkdir()
        result = get_journal_file(tmp_path, "2026-02-01", "koan")
        assert str(result).endswith("journal/2026-02-01/koan.md")
        assert not result.exists()

    def test_accepts_date_object(self, tmp_path):
        from datetime import date
        from app.utils import get_journal_file
        (tmp_path / "journal").mkdir()
        result = get_journal_file(tmp_path, date(2026, 2, 1), "koan")
        assert "2026-02-01" in str(result)


class TestReadAllJournals:
    def test_nested_files(self, tmp_path):
        from app.utils import read_all_journals
        d = tmp_path / "journal" / "2026-02-01"
        d.mkdir(parents=True)
        (d / "koan.md").write_text("koan journal")
        (d / "other.md").write_text("other journal")
        result = read_all_journals(tmp_path, "2026-02-01")
        assert "[koan]" in result
        assert "[other]" in result

    def test_flat_file(self, tmp_path):
        from app.utils import read_all_journals
        (tmp_path / "journal").mkdir()
        (tmp_path / "journal" / "2026-02-01.md").write_text("flat journal")
        result = read_all_journals(tmp_path, "2026-02-01")
        assert "flat journal" in result

    def test_empty_dir(self, tmp_path):
        from app.utils import read_all_journals
        (tmp_path / "journal").mkdir()
        result = read_all_journals(tmp_path, "2026-02-01")
        assert result == ""

    def test_accepts_date_object(self, tmp_path):
        from datetime import date
        from app.utils import read_all_journals
        d = tmp_path / "journal" / "2026-02-01"
        d.mkdir(parents=True)
        (d / "koan.md").write_text("content")
        result = read_all_journals(tmp_path, date(2026, 2, 1))
        assert "content" in result


class TestGetLatestJournal:
    def test_project_today(self, tmp_path):
        from datetime import date
        from app.utils import get_latest_journal
        d = tmp_path / "journal" / date.today().strftime("%Y-%m-%d")
        d.mkdir(parents=True)
        (d / "koan.md").write_text("## Session 29\n\nDid some work.")
        result = get_latest_journal(tmp_path, project="koan")
        assert "koan" in result
        assert "Did some work" in result

    def test_project_specific_date(self, tmp_path):
        from app.utils import get_latest_journal
        d = tmp_path / "journal" / "2026-01-15"
        d.mkdir(parents=True)
        (d / "myproj.md").write_text("Old entry.")
        result = get_latest_journal(tmp_path, project="myproj", target_date="2026-01-15")
        assert "myproj" in result
        assert "2026-01-15" in result
        assert "Old entry." in result

    def test_all_projects(self, tmp_path):
        from datetime import date
        from app.utils import get_latest_journal
        d = tmp_path / "journal" / date.today().strftime("%Y-%m-%d")
        d.mkdir(parents=True)
        (d / "koan.md").write_text("koan entry")
        (d / "web-app.md").write_text("web-app entry")
        result = get_latest_journal(tmp_path)
        assert "koan" in result
        assert "web-app" in result

    def test_no_journal_found(self, tmp_path):
        from app.utils import get_latest_journal
        (tmp_path / "journal").mkdir()
        result = get_latest_journal(tmp_path, project="koan")
        assert "Pas de journal" in result

    def test_truncation(self, tmp_path):
        from datetime import date
        from app.utils import get_latest_journal
        d = tmp_path / "journal" / date.today().strftime("%Y-%m-%d")
        d.mkdir(parents=True)
        (d / "koan.md").write_text("x" * 1000)
        result = get_latest_journal(tmp_path, project="koan", max_chars=200)
        assert len(result) < 300  # header + truncated content
        assert "..." in result

    def test_empty_journal(self, tmp_path):
        from datetime import date
        from app.utils import get_latest_journal
        d = tmp_path / "journal" / date.today().strftime("%Y-%m-%d")
        d.mkdir(parents=True)
        (d / "koan.md").write_text("")
        result = get_latest_journal(tmp_path, project="koan")
        assert "vide" in result.lower()

    def test_no_journal_all_projects(self, tmp_path):
        from app.utils import get_latest_journal
        (tmp_path / "journal").mkdir()
        result = get_latest_journal(tmp_path)
        assert "Pas de journal" in result

    def test_accepts_date_object(self, tmp_path):
        from datetime import date
        from app.utils import get_latest_journal
        d = tmp_path / "journal" / "2026-02-01"
        d.mkdir(parents=True)
        (d / "koan.md").write_text("entry content")
        result = get_latest_journal(tmp_path, project="koan", target_date=date(2026, 2, 1))
        assert "entry content" in result


class TestAppendToJournal:
    def test_creates_and_appends(self, tmp_path):
        from app.utils import append_to_journal
        append_to_journal(tmp_path, "koan", "first entry\n")
        append_to_journal(tmp_path, "koan", "second entry\n")
        # Find the journal file (date-dependent)
        journal_dirs = list((tmp_path / "journal").iterdir())
        assert len(journal_dirs) == 1
        journal_file = journal_dirs[0] / "koan.md"
        content = journal_file.read_text()
        assert "first entry" in content
        assert "second entry" in content

    def test_creates_directory(self, tmp_path):
        from app.utils import append_to_journal
        append_to_journal(tmp_path, "myproject", "entry\n")
        assert (tmp_path / "journal").is_dir()


class TestAtomicWrite:
    def test_writes_content(self, tmp_path):
        from app.utils import atomic_write
        target = tmp_path / "test.md"
        atomic_write(target, "hello world\n")
        assert target.read_text() == "hello world\n"

    def test_overwrites_existing(self, tmp_path):
        from app.utils import atomic_write
        target = tmp_path / "test.md"
        target.write_text("old content")
        atomic_write(target, "new content")
        assert target.read_text() == "new content"

    def test_no_temp_files_left(self, tmp_path):
        from app.utils import atomic_write
        target = tmp_path / "test.md"
        atomic_write(target, "content")
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].name == "test.md"

    def test_concurrent_writes_no_corruption(self, tmp_path):
        from app.utils import atomic_write
        target = tmp_path / "missions.md"
        target.write_text("")

        errors = []

        def writer(n):
            try:
                for _ in range(20):
                    atomic_write(target, f"writer-{n}\n" * 10)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        content = target.read_text()
        lines = [l for l in content.splitlines() if l]
        assert len(set(lines)) == 1

    def test_preserves_utf8(self, tmp_path):
        from app.utils import atomic_write
        target = tmp_path / "test.md"
        atomic_write(target, "kōan — été — 日本語\n")
        assert target.read_text(encoding="utf-8") == "kōan — été — 日本語\n"


class TestAppendToOutbox:
    def test_creates_file_if_missing(self, tmp_path):
        from app.utils import append_to_outbox
        outbox = tmp_path / "outbox.md"
        append_to_outbox(outbox, "Hello world\n")
        assert outbox.read_text() == "Hello world\n"

    def test_appends_to_existing(self, tmp_path):
        from app.utils import append_to_outbox
        outbox = tmp_path / "outbox.md"
        outbox.write_text("First\n")
        append_to_outbox(outbox, "Second\n")
        assert outbox.read_text() == "First\nSecond\n"

    def test_concurrent_appends(self, tmp_path):
        from app.utils import append_to_outbox
        outbox = tmp_path / "outbox.md"
        outbox.write_text("")
        threads = []
        for i in range(10):
            t = threading.Thread(target=append_to_outbox, args=(outbox, f"msg{i}\n"))
            threads.append(t)
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        content = outbox.read_text()
        assert content.count("\n") == 10


class TestCompactTelegramHistory:
    def _write_messages(self, path, messages):
        import json
        with open(path, "w") as f:
            for msg in messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

    def _make_msg(self, role, text, date="2026-02-01", time="12:00:00"):
        return {"timestamp": f"{date}T{time}", "role": role, "text": text}

    def test_skips_when_no_file(self, tmp_path):
        from app.utils import compact_telegram_history
        result = compact_telegram_history(
            tmp_path / "history.jsonl", tmp_path / "topics.json"
        )
        assert result == 0

    def test_skips_below_threshold(self, tmp_path):
        from app.utils import compact_telegram_history
        history = tmp_path / "history.jsonl"
        msgs = [self._make_msg("user", f"msg {i}") for i in range(5)]
        self._write_messages(history, msgs)
        result = compact_telegram_history(history, tmp_path / "topics.json", min_messages=20)
        assert result == 0
        assert history.read_text() != ""  # Not truncated

    def test_compacts_above_threshold(self, tmp_path):
        import json
        from app.utils import compact_telegram_history
        history = tmp_path / "history.jsonl"
        topics_file = tmp_path / "topics.json"
        msgs = [self._make_msg("user", f"Discussion about topic {i}") for i in range(25)]
        self._write_messages(history, msgs)
        result = compact_telegram_history(history, topics_file, min_messages=20)
        assert result == 25
        assert history.read_text() == ""  # Truncated
        assert topics_file.exists()
        data = json.loads(topics_file.read_text())
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["message_count"] == 25
        assert "topics_by_date" in data[0]
        assert "2026-02-01" in data[0]["topics_by_date"]

    def test_appends_to_existing_topics(self, tmp_path):
        import json
        from app.utils import compact_telegram_history
        history = tmp_path / "history.jsonl"
        topics_file = tmp_path / "topics.json"
        # Pre-existing topics
        topics_file.write_text(json.dumps([{"old": True}]))
        msgs = [self._make_msg("user", f"New topic {i}") for i in range(25)]
        self._write_messages(history, msgs)
        compact_telegram_history(history, topics_file, min_messages=20)
        data = json.loads(topics_file.read_text())
        assert len(data) == 2
        assert data[0]["old"] is True

    def test_extracts_topics_from_user_messages_only(self, tmp_path):
        import json
        from app.utils import compact_telegram_history
        history = tmp_path / "history.jsonl"
        topics_file = tmp_path / "topics.json"
        msgs = []
        for i in range(15):
            msgs.append(self._make_msg("user", f"User question about feature {i}"))
            msgs.append(self._make_msg("assistant", f"Response about feature {i}"))
        self._write_messages(history, msgs)
        compact_telegram_history(history, topics_file, min_messages=20)
        data = json.loads(topics_file.read_text())
        topics = data[0]["topics_by_date"]["2026-02-01"]
        # Only user messages become topics
        assert all("User question" in t for t in topics)

    def test_groups_by_date(self, tmp_path):
        import json
        from app.utils import compact_telegram_history
        history = tmp_path / "history.jsonl"
        topics_file = tmp_path / "topics.json"
        msgs = [self._make_msg("user", f"Day1 msg {i}", date="2026-02-01") for i in range(12)]
        msgs += [self._make_msg("user", f"Day2 msg {i}", date="2026-02-02") for i in range(12)]
        self._write_messages(history, msgs)
        compact_telegram_history(history, topics_file, min_messages=20)
        data = json.loads(topics_file.read_text())
        assert "2026-02-01" in data[0]["topics_by_date"]
        assert "2026-02-02" in data[0]["topics_by_date"]
        assert data[0]["date_range"]["from"] == "2026-02-01"
        assert data[0]["date_range"]["to"] == "2026-02-02"

    def test_deduplicates_topics(self, tmp_path):
        import json
        from app.utils import compact_telegram_history
        history = tmp_path / "history.jsonl"
        topics_file = tmp_path / "topics.json"
        msgs = [self._make_msg("user", "Same question repeated")] * 25
        self._write_messages(history, msgs)
        compact_telegram_history(history, topics_file, min_messages=20)
        data = json.loads(topics_file.read_text())
        assert len(data[0]["topics_by_date"]["2026-02-01"]) == 1

    def test_ignores_short_messages(self, tmp_path):
        import json
        from app.utils import compact_telegram_history
        history = tmp_path / "history.jsonl"
        topics_file = tmp_path / "topics.json"
        msgs = [self._make_msg("user", "ok")] * 15 + [self._make_msg("user", "A real question about something")] * 10
        self._write_messages(history, msgs)
        compact_telegram_history(history, topics_file, min_messages=20)
        data = json.loads(topics_file.read_text())
        topics = data[0]["topics_by_date"]["2026-02-01"]
        assert all(len(t) > 5 for t in topics)


class TestGetStartOnPause:
    def test_returns_true_when_enabled(self, tmp_path, monkeypatch):
        from app import utils
        monkeypatch.setattr(utils, "KOAN_ROOT", tmp_path)
        config_dir = tmp_path / "instance"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("start_on_pause: true\n")
        from app.utils import get_start_on_pause
        assert get_start_on_pause() is True

    def test_returns_false_when_disabled(self, tmp_path, monkeypatch):
        from app import utils
        monkeypatch.setattr(utils, "KOAN_ROOT", tmp_path)
        config_dir = tmp_path / "instance"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("start_on_pause: false\n")
        from app.utils import get_start_on_pause
        assert get_start_on_pause() is False

    def test_returns_false_when_missing(self, tmp_path, monkeypatch):
        from app import utils
        monkeypatch.setattr(utils, "KOAN_ROOT", tmp_path)
        config_dir = tmp_path / "instance"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("other_setting: value\n")
        from app.utils import get_start_on_pause
        assert get_start_on_pause() is False

    def test_returns_false_when_no_config(self, tmp_path, monkeypatch):
        from app import utils
        monkeypatch.setattr(utils, "KOAN_ROOT", tmp_path)
        # No config file at all
        from app.utils import get_start_on_pause
        assert get_start_on_pause() is False


class TestGetKnownProjects:
    def test_multi_project(self, monkeypatch):
        monkeypatch.setenv("KOAN_PROJECTS", "beta:/b;alpha:/a")
        from app.utils import get_known_projects
        result = get_known_projects()
        assert result == [("alpha", "/a"), ("beta", "/b")]

    def test_single_project_fallback(self, monkeypatch):
        monkeypatch.delenv("KOAN_PROJECTS", raising=False)
        monkeypatch.setenv("KOAN_PROJECT_PATH", "/my/project")
        from app.utils import get_known_projects
        result = get_known_projects()
        assert result == [("default", "/my/project")]

    def test_empty_when_no_env(self, monkeypatch):
        monkeypatch.delenv("KOAN_PROJECTS", raising=False)
        monkeypatch.delenv("KOAN_PROJECT_PATH", raising=False)
        from app.utils import get_known_projects
        assert get_known_projects() == []

    def test_sorts_alphabetically_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("KOAN_PROJECTS", "Zulu:/z;alpha:/a;Beta:/b")
        from app.utils import get_known_projects
        result = get_known_projects()
        assert [name for name, _ in result] == ["alpha", "Beta", "Zulu"]

    def test_handles_whitespace(self, monkeypatch):
        monkeypatch.setenv("KOAN_PROJECTS", " foo : /foo ; bar : /bar ")
        from app.utils import get_known_projects
        result = get_known_projects()
        assert result == [("bar", "/bar"), ("foo", "/foo")]

    def test_skips_malformed_entries(self, monkeypatch):
        monkeypatch.setenv("KOAN_PROJECTS", "good:/path;badentry;also_good:/other")
        from app.utils import get_known_projects
        result = get_known_projects()
        assert len(result) == 2
        assert result[0][0] == "also_good"
        assert result[1][0] == "good"
