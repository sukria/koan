#!/usr/bin/env python3
"""
Tests for conversation history functionality.

Tests the conversation memory system that allows Kōan to remember
previous messages in a chat session.
"""

import json
import os
import tempfile
from pathlib import Path

import pytest


# Set KOAN_ROOT before importing
@pytest.fixture(autouse=True)
def setup_koan_root(tmp_path, monkeypatch):
    """Set up KOAN_ROOT for all tests."""
    monkeypatch.setenv("KOAN_ROOT", str(tmp_path))


@pytest.fixture
def temp_history_file():
    """Create a temporary history file for testing."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
        temp_file = Path(f.name)
    yield temp_file
    # Cleanup
    if temp_file.exists():
        temp_file.unlink()


def test_save_message(temp_history_file):
    """Test saving a message to history."""
    from app.utils import save_telegram_message

    save_telegram_message(temp_history_file, "user", "Hello Kōan")

    assert temp_history_file.exists()

    with open(temp_history_file, 'r') as f:
        lines = f.readlines()

    assert len(lines) == 1

    msg = json.loads(lines[0])
    assert msg["role"] == "user"
    assert msg["text"] == "Hello Kōan"
    assert "timestamp" in msg


def test_save_multiple_messages(temp_history_file):
    """Test saving multiple messages."""
    from app.utils import save_telegram_message

    save_telegram_message(temp_history_file, "user", "Testons : mot magique : abaladic")
    save_telegram_message(temp_history_file, "assistant", "Reçu. Abaladic. Je note le mot magique.")
    save_telegram_message(temp_history_file, "user", "Quel est le mot magique ?")

    with open(temp_history_file, 'r') as f:
        lines = f.readlines()

    assert len(lines) == 3


def test_load_recent_history(temp_history_file):
    """Test loading recent conversation history."""
    from app.utils import save_telegram_message, load_recent_telegram_history

    save_telegram_message(temp_history_file, "user", "Message 1")
    save_telegram_message(temp_history_file, "assistant", "Response 1")
    save_telegram_message(temp_history_file, "user", "Message 2")

    history = load_recent_telegram_history(temp_history_file, max_messages=10)

    assert len(history) == 3
    assert history[0]["text"] == "Message 1"
    assert history[1]["text"] == "Response 1"
    assert history[2]["text"] == "Message 2"


def test_load_history_max_messages(temp_history_file):
    """Test that max_messages limit works correctly."""
    from app.utils import save_telegram_message, load_recent_telegram_history

    # Add 15 messages
    for i in range(15):
        role = "user" if i % 2 == 0 else "assistant"
        save_telegram_message(temp_history_file, role, f"Message {i}")

    # Load with limit of 5
    history = load_recent_telegram_history(temp_history_file, max_messages=5)

    assert len(history) == 5
    # Should get the last 5 messages (10-14)
    assert history[0]["text"] == "Message 10"
    assert history[4]["text"] == "Message 14"


def test_load_nonexistent_file():
    """Test loading from a file that doesn't exist."""
    from app.utils import load_recent_telegram_history

    nonexistent = Path("/tmp/nonexistent_history.jsonl")
    history = load_recent_telegram_history(nonexistent, max_messages=10)

    assert history == []


def test_format_conversation_history(temp_history_file):
    """Test formatting conversation history for prompt."""
    from app.utils import save_telegram_message, load_recent_telegram_history, format_conversation_history

    save_telegram_message(temp_history_file, "user", "Testons : mot magique : abaladic")
    save_telegram_message(temp_history_file, "assistant", "Reçu. Abaladic. Je note le mot magique.")
    save_telegram_message(temp_history_file, "user", "Quel est le mot magique ?")

    history = load_recent_telegram_history(temp_history_file, max_messages=10)
    formatted = format_conversation_history(history)

    assert "Recent conversation:" in formatted
    assert "Human: Testons : mot magique : abaladic" in formatted
    assert "Kōan: Reçu. Abaladic. Je note le mot magique." in formatted
    assert "Human: Quel est le mot magique ?" in formatted


def test_format_empty_history():
    """Test formatting with no history."""
    from app.utils import format_conversation_history

    formatted = format_conversation_history([])
    assert formatted == ""


def test_message_structure(temp_history_file):
    """Test that saved messages have correct structure."""
    from app.utils import save_telegram_message

    save_telegram_message(temp_history_file, "user", "Test message")

    with open(temp_history_file, 'r') as f:
        msg = json.loads(f.readline())

    # Check all required fields are present
    assert "timestamp" in msg
    assert "role" in msg
    assert "text" in msg

    # Check timestamp format (ISO 8601)
    assert "T" in msg["timestamp"]

    # Check role is valid
    assert msg["role"] in ["user", "assistant"]


def test_unicode_handling(temp_history_file):
    """Test that Unicode characters are preserved correctly."""
    from app.utils import save_telegram_message, load_recent_telegram_history

    unicode_text = "Kōan avec des accents: é à ü 日本語 🎉"
    save_telegram_message(temp_history_file, "user", unicode_text)

    history = load_recent_telegram_history(temp_history_file, max_messages=10)

    assert len(history) == 1
    assert history[0]["text"] == unicode_text


def test_jsonl_format_invalid_lines(temp_history_file):
    """Test that invalid JSON lines are skipped gracefully."""
    from app.utils import load_recent_telegram_history

    # Write some valid and invalid lines
    with open(temp_history_file, 'w') as f:
        f.write('{"timestamp": "2026-02-01T10:00:00", "role": "user", "text": "Valid"}\n')
        f.write('Invalid JSON line\n')
        f.write('{"timestamp": "2026-02-01T10:01:00", "role": "assistant", "text": "Also valid"}\n')

    history = load_recent_telegram_history(temp_history_file, max_messages=10)

    # Should only load the 2 valid messages
    assert len(history) == 2
    assert history[0]["text"] == "Valid"
    assert history[1]["text"] == "Also valid"


def test_conversation_continuity():
    """
    Integration test: simulate the 'magic word' scenario.

    This is the exact scenario from the bug report where Kōan
    should remember what was said earlier in the conversation.
    """
    from app.utils import save_telegram_message, load_recent_telegram_history, format_conversation_history

    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
        temp_file = Path(f.name)

    try:
        # First exchange: user mentions the magic word
        save_telegram_message(temp_file, "user", "Testons : mot magique : abaladic")
        save_telegram_message(temp_file, "assistant",
            "Reçu. Abaladic. Je note le mot magique, le canal fonctionne.")

        # Second exchange: user asks about the magic word
        save_telegram_message(temp_file, "user", "Quel est le mot magique ?")

        # Load history - this is what would be included in Claude's context
        history = load_recent_telegram_history(temp_file, max_messages=10)
        formatted = format_conversation_history(history)

        # Verify the history contains the magic word
        assert "abaladic" in formatted
        assert "mot magique" in formatted

        # Verify Kōan's response is in the history
        assert "Je note le mot magique" in formatted

        # Verify the question is in the history
        assert "Quel est le mot magique" in formatted

        # This formatted history would now be included in the prompt,
        # allowing Claude to see that "abaladic" was the magic word
        # and answer correctly instead of saying "S'il te plaît"

    finally:
        if temp_file.exists():
            temp_file.unlink()
