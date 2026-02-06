"""Tests for koan/app/telegram_history.py — conversation history management."""
import json
from pathlib import Path

import pytest


@pytest.fixture
def history_file(tmp_path):
    """Return path to a temporary history file."""
    return tmp_path / "telegram-history.jsonl"


@pytest.fixture
def topics_file(tmp_path):
    """Return path to a temporary topics file."""
    return tmp_path / "previous-discussions-topics.json"


def _write_messages(history_file, messages):
    """Helper to write JSONL messages to the history file."""
    lines = [json.dumps(m, ensure_ascii=False) for m in messages]
    history_file.write_text("\n".join(lines) + "\n")


# --- save_telegram_message ---


class TestSaveTelegramMessage:
    def test_saves_user_message(self, history_file):
        from app.telegram_history import save_telegram_message

        save_telegram_message(history_file, "user", "Hello")

        lines = history_file.read_text().strip().split("\n")
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

    def test_appends_to_existing(self, history_file):
        from app.telegram_history import save_telegram_message

        save_telegram_message(history_file, "user", "First")
        save_telegram_message(history_file, "assistant", "Second")

        lines = history_file.read_text().strip().split("\n")
        assert len(lines) == 2

    def test_handles_unicode(self, history_file):
        from app.telegram_history import save_telegram_message

        save_telegram_message(history_file, "user", "Salut \u00e7a va ? \U0001f44d")

        msg = json.loads(history_file.read_text().strip())
        assert "\u00e7a va" in msg["text"]
        assert "\U0001f44d" in msg["text"]

    def test_creates_file_if_missing(self, history_file):
        from app.telegram_history import save_telegram_message

        assert not history_file.exists()
        save_telegram_message(history_file, "user", "Hello")
        assert history_file.exists()


# --- load_recent_telegram_history ---


class TestLoadRecentTelegramHistory:
    def test_load_empty_file(self, history_file):
        from app.telegram_history import load_recent_telegram_history

        history_file.write_text("")
        result = load_recent_telegram_history(history_file)
        assert result == []

    def test_load_nonexistent_file(self, history_file):
        from app.telegram_history import load_recent_telegram_history

        result = load_recent_telegram_history(history_file)
        assert result == []

    def test_load_messages(self, history_file):
        from app.telegram_history import load_recent_telegram_history

        messages = [
            {"timestamp": "2026-02-06T10:00:00", "role": "user", "text": "Hello"},
            {"timestamp": "2026-02-06T10:01:00", "role": "assistant", "text": "Hi"},
        ]
        _write_messages(history_file, messages)

        result = load_recent_telegram_history(history_file)
        assert len(result) == 2
        assert result[0]["text"] == "Hello"
        assert result[1]["text"] == "Hi"

    def test_respects_max_messages(self, history_file):
        from app.telegram_history import load_recent_telegram_history

        messages = [
            {"timestamp": f"2026-02-06T10:{i:02d}:00", "role": "user", "text": f"Msg {i}"}
            for i in range(20)
        ]
        _write_messages(history_file, messages)

        result = load_recent_telegram_history(history_file, max_messages=5)
        assert len(result) == 5
        assert result[0]["text"] == "Msg 15"  # last 5
        assert result[4]["text"] == "Msg 19"

    def test_returns_all_when_under_limit(self, history_file):
        from app.telegram_history import load_recent_telegram_history

        messages = [
            {"timestamp": "2026-02-06T10:00:00", "role": "user", "text": "One"},
        ]
        _write_messages(history_file, messages)

        result = load_recent_telegram_history(history_file, max_messages=10)
        assert len(result) == 1

    def test_skips_invalid_json_lines(self, history_file):
        from app.telegram_history import load_recent_telegram_history

        content = (
            '{"timestamp": "2026-02-06T10:00:00", "role": "user", "text": "Good"}\n'
            'this is not json\n'
            '{"timestamp": "2026-02-06T10:01:00", "role": "assistant", "text": "Also good"}\n'
        )
        history_file.write_text(content)

        result = load_recent_telegram_history(history_file)
        assert len(result) == 2

    def test_skips_blank_lines(self, history_file):
        from app.telegram_history import load_recent_telegram_history

        content = (
            '{"timestamp": "2026-02-06T10:00:00", "role": "user", "text": "One"}\n'
            '\n'
            '{"timestamp": "2026-02-06T10:01:00", "role": "user", "text": "Two"}\n'
        )
        history_file.write_text(content)

        result = load_recent_telegram_history(history_file)
        assert len(result) == 2


# --- format_conversation_history ---


class TestFormatConversationHistory:
    def test_empty_messages(self):
        from app.telegram_history import format_conversation_history

        assert format_conversation_history([]) == ""

    def test_formats_user_and_assistant(self):
        from app.telegram_history import format_conversation_history

        messages = [
            {"role": "user", "text": "Hello"},
            {"role": "assistant", "text": "Hi there"},
        ]
        result = format_conversation_history(messages)
        assert "Recent conversation:" in result
        assert "Human: Hello" in result
        assert "K\u014dan: Hi there" in result

    def test_truncates_long_messages(self):
        from app.telegram_history import format_conversation_history

        messages = [
            {"role": "user", "text": "A" * 600},
        ]
        result = format_conversation_history(messages)
        assert "..." in result
        # Individual message truncated to 500 chars + "..."
        lines = result.split("\n")
        human_line = [l for l in lines if l.startswith("Human:")][0]
        assert len(human_line) < 520  # "Human: " + 500 + "..."

    def test_respects_max_chars(self):
        from app.telegram_history import format_conversation_history

        messages = [
            {"role": "user", "text": f"Message number {i}" * 10}
            for i in range(50)
        ]
        result = format_conversation_history(messages, max_chars=200)
        assert len(result) <= 250  # approximate, includes header

    def test_single_message(self):
        from app.telegram_history import format_conversation_history

        messages = [{"role": "user", "text": "Solo"}]
        result = format_conversation_history(messages)
        assert "Human: Solo" in result


# --- compact_telegram_history ---


class TestCompactTelegramHistory:
    def test_skips_when_too_few_messages(self, history_file, topics_file):
        from app.telegram_history import compact_telegram_history

        messages = [
            {"timestamp": "2026-02-06T10:00:00", "role": "user", "text": "Hello there"}
        ]
        _write_messages(history_file, messages)

        result = compact_telegram_history(history_file, topics_file, min_messages=20)
        assert result == 0
        assert not topics_file.exists()

    def test_skips_nonexistent_file(self, history_file, topics_file):
        from app.telegram_history import compact_telegram_history

        result = compact_telegram_history(history_file, topics_file)
        assert result == 0

    def test_compacts_messages_and_extracts_topics(self, history_file, topics_file):
        from app.telegram_history import compact_telegram_history

        messages = [
            {"timestamp": "2026-02-06T10:00:00", "role": "user", "text": "Fix the auth bug in login"},
            {"timestamp": "2026-02-06T10:01:00", "role": "assistant", "text": "Looking into it"},
            {"timestamp": "2026-02-06T10:02:00", "role": "user", "text": "Also check the dashboard"},
        ] * 10  # 30 messages

        _write_messages(history_file, messages)

        result = compact_telegram_history(history_file, topics_file, min_messages=5)
        assert result == 30

        # History file should be empty
        assert history_file.read_text() == ""

        # Topics file should have an entry
        topics = json.loads(topics_file.read_text())
        assert isinstance(topics, list)
        assert len(topics) == 1
        entry = topics[0]
        assert "compacted_at" in entry
        assert entry["message_count"] == 30
        assert "2026-02-06" in entry["topics_by_date"]

    def test_extracts_first_sentence_as_topic(self, history_file, topics_file):
        from app.telegram_history import compact_telegram_history

        messages = [
            {"timestamp": "2026-02-06T10:00:00", "role": "user", "text": "Fix the bug. Then deploy."},
        ] * 25

        _write_messages(history_file, messages)

        compact_telegram_history(history_file, topics_file, min_messages=5)

        topics = json.loads(topics_file.read_text())
        topics_list = topics[0]["topics_by_date"]["2026-02-06"]
        assert "Fix the bug" in topics_list

    def test_ignores_short_topics(self, history_file, topics_file):
        from app.telegram_history import compact_telegram_history

        messages = [
            {"timestamp": "2026-02-06T10:00:00", "role": "user", "text": "ok"},
            {"timestamp": "2026-02-06T10:01:00", "role": "user", "text": "yes"},
            {"timestamp": "2026-02-06T10:02:00", "role": "user", "text": "This is a real topic about something"},
        ] * 10

        _write_messages(history_file, messages)

        compact_telegram_history(history_file, topics_file, min_messages=5)

        topics = json.loads(topics_file.read_text())
        topics_list = topics[0]["topics_by_date"]["2026-02-06"]
        assert "ok" not in topics_list
        assert "yes" not in topics_list
        assert any("real topic" in t for t in topics_list)

    def test_skips_assistant_messages_for_topics(self, history_file, topics_file):
        from app.telegram_history import compact_telegram_history

        messages = [
            {"timestamp": "2026-02-06T10:00:00", "role": "user", "text": "User question here about auth"},
            {"timestamp": "2026-02-06T10:01:00", "role": "assistant", "text": "Assistant answer that should not appear as topic"},
        ] * 15

        _write_messages(history_file, messages)

        compact_telegram_history(history_file, topics_file, min_messages=5)

        topics = json.loads(topics_file.read_text())
        topics_list = topics[0]["topics_by_date"]["2026-02-06"]
        assert any("User question" in t for t in topics_list)
        assert not any("Assistant answer" in t for t in topics_list)

    def test_appends_to_existing_topics_file(self, history_file, topics_file):
        from app.telegram_history import compact_telegram_history

        # Pre-existing topics
        existing = [{"compacted_at": "2026-02-05T12:00:00", "message_count": 10, "topics_by_date": {}}]
        topics_file.write_text(json.dumps(existing))

        messages = [
            {"timestamp": "2026-02-06T10:00:00", "role": "user", "text": "Something meaningful to discuss"}
        ] * 25
        _write_messages(history_file, messages)

        compact_telegram_history(history_file, topics_file, min_messages=5)

        topics = json.loads(topics_file.read_text())
        assert len(topics) == 2

    def test_deduplicates_topics_within_date(self, history_file, topics_file):
        from app.telegram_history import compact_telegram_history

        # Same message repeated
        messages = [
            {"timestamp": "2026-02-06T10:00:00", "role": "user", "text": "Fix the auth bug please"},
        ] * 25
        _write_messages(history_file, messages)

        compact_telegram_history(history_file, topics_file, min_messages=5)

        topics = json.loads(topics_file.read_text())
        topics_list = topics[0]["topics_by_date"]["2026-02-06"]
        assert len(topics_list) == 1  # deduplicated

    def test_groups_by_date(self, history_file, topics_file):
        from app.telegram_history import compact_telegram_history

        messages = [
            {"timestamp": "2026-02-05T10:00:00", "role": "user", "text": "Yesterday's discussion about deployment"},
            {"timestamp": "2026-02-06T10:00:00", "role": "user", "text": "Today's discussion about testing"},
        ] * 15
        _write_messages(history_file, messages)

        compact_telegram_history(history_file, topics_file, min_messages=5)

        topics = json.loads(topics_file.read_text())
        assert "2026-02-05" in topics[0]["topics_by_date"]
        assert "2026-02-06" in topics[0]["topics_by_date"]

    def test_date_range_in_entry(self, history_file, topics_file):
        from app.telegram_history import compact_telegram_history

        messages = [
            {"timestamp": "2026-02-04T10:00:00", "role": "user", "text": "First day discussion topic"},
            {"timestamp": "2026-02-06T10:00:00", "role": "user", "text": "Last day discussion topic"},
        ] * 15
        _write_messages(history_file, messages)

        compact_telegram_history(history_file, topics_file, min_messages=5)

        topics = json.loads(topics_file.read_text())
        assert topics[0]["date_range"]["from"] == "2026-02-04"
        assert topics[0]["date_range"]["to"] == "2026-02-06"

    def test_handles_no_extractable_topics(self, history_file, topics_file):
        from app.telegram_history import compact_telegram_history

        # Only assistant messages and very short user messages
        messages = [
            {"timestamp": "2026-02-06T10:00:00", "role": "assistant", "text": "I'm thinking about this"},
            {"timestamp": "2026-02-06T10:01:00", "role": "user", "text": "ok"},
        ] * 15
        _write_messages(history_file, messages)

        result = compact_telegram_history(history_file, topics_file, min_messages=5)
        assert result == 30
        assert history_file.read_text() == ""
        assert not topics_file.exists()  # no topics to write

    def test_handles_malformed_topics_file(self, history_file, topics_file):
        from app.telegram_history import compact_telegram_history

        topics_file.write_text("not valid json")

        messages = [
            {"timestamp": "2026-02-06T10:00:00", "role": "user", "text": "Something worth discussing here"}
        ] * 25
        _write_messages(history_file, messages)

        compact_telegram_history(history_file, topics_file, min_messages=5)

        # Should have recovered and written valid JSON
        topics = json.loads(topics_file.read_text())
        assert isinstance(topics, list)
        assert len(topics) == 1
