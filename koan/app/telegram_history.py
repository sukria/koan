"""Telegram conversation history management — extracted from utils.py.

Handles saving, loading, formatting, and compacting conversation
history stored as JSONL files.
"""

import fcntl
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List


def save_telegram_message(history_file: Path, role: str, text: str):
    """Save a message to the conversation history file (JSONL format).

    Args:
        history_file: Path to the history file (e.g., instance/telegram-history.jsonl)
        role: "user" or "assistant"
        text: Message content
    """
    message = {
        "timestamp": datetime.now().isoformat(),
        "role": role,
        "text": text
    }
    try:
        with open(history_file, "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(json.dumps(message, ensure_ascii=False) + "\n")
            fcntl.flock(f, fcntl.LOCK_UN)
    except OSError as e:
        print(f"[telegram_history] Error saving message to history: {e}")


def load_recent_telegram_history(history_file: Path, max_messages: int = 10) -> List[Dict[str, str]]:
    """Load the most recent messages from conversation history.

    Args:
        history_file: Path to the history file
        max_messages: Maximum number of recent messages to return

    Returns:
        List of message dicts with keys: timestamp, role, text
    """
    if not history_file.exists():
        return []

    try:
        with open(history_file, "r", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            lines = f.readlines()
            fcntl.flock(f, fcntl.LOCK_UN)

        # Parse JSONL (one JSON per line)
        messages = []
        for line in lines:
            line = line.strip()
            if line:
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        # Return last N messages
        return messages[-max_messages:] if len(messages) > max_messages else messages
    except OSError as e:
        print(f"[telegram_history] Error loading history: {e}")
        return []


def format_conversation_history(
    messages: List[Dict[str, str]],
    max_chars: int = 3000,
) -> str:
    """Format conversation history for inclusion in the prompt.

    Args:
        messages: List of message dicts from load_recent_telegram_history
        max_chars: Maximum total characters for the formatted history

    Returns:
        Formatted string ready to include in the prompt
    """
    if not messages:
        return ""

    lines = ["Recent conversation:"]
    total = len(lines[0])
    for msg in messages:
        role_label = "Human" if msg["role"] == "user" else "K\u014dan"
        text = msg["text"]
        if len(text) > 500:
            text = text[:500] + "..."
        line = f"{role_label}: {text}"
        total += len(line) + 1
        if total > max_chars:
            break
        lines.append(line)

    return "\n".join(lines)


def compact_telegram_history(history_file: Path, topics_file: Path, min_messages: int = 20) -> int:
    """Compact telegram history at startup to avoid context bleed.

    Reads all messages from history_file, extracts discussion topics grouped
    by date, appends them to topics_file (JSON array), then truncates history.

    Args:
        history_file: Path to telegram-history.jsonl
        topics_file: Path to previous-discussions-topics.json
        min_messages: Minimum messages before compaction triggers (avoid compacting tiny histories)

    Returns:
        Number of messages compacted (0 if skipped)
    """
    if not history_file.exists():
        return 0

    # Read all messages
    messages = []
    try:
        with open(history_file, "r", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            lines = f.readlines()
            fcntl.flock(f, fcntl.LOCK_UN)
    except OSError:
        return 0

    for line in lines:
        line = line.strip()
        if line:
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if len(messages) < min_messages:
        return 0

    # Group messages by date, extract topics from user messages
    topics_by_date: Dict[str, List[str]] = {}
    for msg in messages:
        ts = msg.get("timestamp", "")
        date = ts[:10] if len(ts) >= 10 else "unknown"
        if msg.get("role") == "user":
            text = msg.get("text", "").strip()
            # Take first sentence as topic hint (max 120 chars)
            topic = text.split(".")[0].split("?")[0].split("!")[0][:120].strip()
            if topic and len(topic) > 5:
                if date not in topics_by_date:
                    topics_by_date[date] = []
                if topic not in topics_by_date[date]:
                    topics_by_date[date].append(topic)

    if not topics_by_date:
        # No extractable topics, just purge
        history_file.write_text("")
        return len(messages)

    # Build compaction entry
    entry = {
        "compacted_at": datetime.now().isoformat(),
        "message_count": len(messages),
        "date_range": {
            "from": min(topics_by_date.keys()),
            "to": max(topics_by_date.keys()),
        },
        "topics_by_date": topics_by_date,
    }

    # Load existing topics file or create new
    existing = []
    if topics_file.exists():
        try:
            existing = json.loads(topics_file.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = [existing]
        except (json.JSONDecodeError, OSError):
            existing = []

    existing.append(entry)

    # Write topics file atomically
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(topics_file.parent), suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, str(topics_file))
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return 0

    # Truncate history
    history_file.write_text("")

    count = len(messages)
    print(f"[telegram_history] Compacted {count} messages \u2192 {topics_file.name}")
    return count
