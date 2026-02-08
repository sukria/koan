"""Tests for app.telegram_history — conversation history management."""

import json
import pytest
from pathlib import Path


@pytest.fixture
def history_file(tmp_path):
    return tmp_path / "telegram-history.jsonl"


@pytest.fixture
def topics_file(tmp_path):
    return tmp_path / "previous-discussions-topics.json"


# --- save_telegram_message ---


class TestSaveTelegramMessage:
    def test_saves_user_message(self, history_file):
        from app.telegram_history import save_telegram_message
        save_telegram_message(history_file, "user", "Hello")
        lines = history_file.read_text().strip().splitlines()
        assert len(lines) == 1
        msg = json.loads(lines[0])
        assert msg["role"] == "user"
        assert msg["text"] == "Hello"
        assert "timestamp" in msg

    def test_saves_assistant_message(self, history_file):
        from app.telegram_history import save_telegram_message
        save_telegram_message(history_file, "assistant", "Hi there")
        msg = json.loads(history_file.read_text().strip())
        assert msg["role"] == "assistant"
        assert msg["text"] == "Hi there"

    def test_appends_multiple(self, history_file):
        from app.telegram_history import save_telegram_message
        save_telegram_message(history_file, "user", "One")
        save_telegram_message(history_file, "assistant", "Two")
        save_telegram_message(history_file, "user", "Three")
        lines = history_file.read_text().strip().splitlines()
        assert len(lines) == 3

    def test_handles_unicode(self, history_file):
        from app.telegram_history import save_telegram_message
        save_telegram_message(history_file, "user", "Bonjour \u00e0 tous \U0001f389")
        msg = json.loads(history_file.read_text().strip())
        assert "\u00e0" in msg["text"]


# --- load_recent_telegram_history ---


class TestLoadRecentTelegramHistory:
    def test_empty_file(self, history_file):
        from app.telegram_history import load_recent_telegram_history
        assert load_recent_telegram_history(history_file) == []

    def test_nonexistent_file(self, tmp_path):
        from app.telegram_history import load_recent_telegram_history
        assert load_recent_telegram_history(tmp_path / "nope.jsonl") == []

    def test_loads_messages(self, history_file):
        from app.telegram_history import save_telegram_message, load_recent_telegram_history
        save_telegram_message(history_file, "user", "Hello")
        save_telegram_message(history_file, "assistant", "Hi")
        msgs = load_recent_telegram_history(history_file)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"

    def test_max_messages(self, history_file):
        from app.telegram_history import save_telegram_message, load_recent_telegram_history
        for i in range(15):
            save_telegram_message(history_file, "user", f"Message {i}")
        msgs = load_recent_telegram_history(history_file, max_messages=5)
        assert len(msgs) == 5
        assert msgs[0]["text"] == "Message 10"  # last 5

    def test_skips_invalid_json(self, history_file):
        from app.telegram_history import load_recent_telegram_history
        history_file.write_text('{"role":"user","text":"ok"}\nnot json\n{"role":"user","text":"ok2"}\n')
        msgs = load_recent_telegram_history(history_file)
        assert len(msgs) == 2


# --- format_conversation_history ---


class TestFormatConversationHistory:
    def test_empty_messages(self):
        from app.telegram_history import format_conversation_history
        assert format_conversation_history([]) == ""

    def test_formats_roles(self):
        from app.telegram_history import format_conversation_history
        msgs = [
            {"role": "user", "text": "Hello"},
            {"role": "assistant", "text": "Hi there"},
        ]
        result = format_conversation_history(msgs)
        assert "Human: Hello" in result
        assert "K\u014dan: Hi there" in result
        assert "Recent conversation:" in result

    def test_truncates_long_messages(self):
        from app.telegram_history import format_conversation_history
        msgs = [{"role": "user", "text": "x" * 600}]
        result = format_conversation_history(msgs)
        assert "..." in result

    def test_max_chars_limit(self):
        from app.telegram_history import format_conversation_history
        msgs = [{"role": "user", "text": f"Message {i}"} for i in range(100)]
        result = format_conversation_history(msgs, max_chars=200)
        assert len(result) <= 300  # some slack for the last appended line


# --- compact_telegram_history ---


class TestCompactTelegramHistory:
    def test_skips_if_nonexistent(self, history_file, topics_file):
        from app.telegram_history import compact_telegram_history
        assert compact_telegram_history(history_file, topics_file) == 0

    def test_skips_if_too_few(self, history_file, topics_file):
        from app.telegram_history import save_telegram_message, compact_telegram_history
        for i in range(5):
            save_telegram_message(history_file, "user", f"Message {i}")
        assert compact_telegram_history(history_file, topics_file, min_messages=20) == 0

    def test_compacts_messages(self, history_file, topics_file):
        from app.telegram_history import save_telegram_message, compact_telegram_history
        for i in range(25):
            save_telegram_message(history_file, "user", f"Testing topic number {i} of today")
        count = compact_telegram_history(history_file, topics_file, min_messages=20)
        assert count == 25
        # History file should be empty after compaction
        assert history_file.read_text() == ""
        # Topics file should have entries
        topics = json.loads(topics_file.read_text())
        assert len(topics) == 1
        assert topics[0]["message_count"] == 25
        assert "topics_by_date" in topics[0]

    def test_appends_to_existing_topics(self, history_file, topics_file):
        from app.telegram_history import save_telegram_message, compact_telegram_history
        # Pre-existing topics
        topics_file.write_text('[{"existing": true}]')
        for i in range(25):
            save_telegram_message(history_file, "user", f"Something worth discussing number {i}")
        compact_telegram_history(history_file, topics_file, min_messages=20)
        topics = json.loads(topics_file.read_text())
        assert len(topics) == 2
        assert topics[0]["existing"] is True

    def test_handles_no_extractable_topics(self, history_file, topics_file):
        from app.telegram_history import compact_telegram_history
        # Write messages with very short text (< 5 chars → skipped by topic extraction)
        lines = []
        for i in range(25):
            msg = json.dumps({"timestamp": "2026-02-07T10:00:00", "role": "user", "text": "hi"})
            lines.append(msg)
        history_file.write_text("\n".join(lines) + "\n")
        count = compact_telegram_history(history_file, topics_file, min_messages=20)
        assert count == 25
        assert history_file.read_text() == ""


# --- backward compatibility ---


class TestBackwardCompat:
    def test_telegram_functions_accessible_from_utils(self):
        from app.utils import (
            save_telegram_message,
            load_recent_telegram_history,
            format_conversation_history,
            compact_telegram_history,
        )
        assert callable(save_telegram_message)
        assert callable(load_recent_telegram_history)
        assert callable(format_conversation_history)
        assert callable(compact_telegram_history)
