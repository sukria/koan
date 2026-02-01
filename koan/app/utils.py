#!/usr/bin/env python3
"""
Koan -- Shared utilities

Consolidates duplicated helpers used across modules:
- load_dotenv: .env file loading
- parse_project: [project:name] / [projet:name] tag extraction
- insert_pending_mission: append mission to missions.md pending section
"""

import fcntl
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, List, Dict


KOAN_ROOT = Path(os.environ["KOAN_ROOT"])

# Pre-compiled regex for project tag extraction (accepts both [project:X] and [projet:X])
_PROJECT_TAG_RE = re.compile(r'\[projec?t:([a-zA-Z0-9_-]+)\]')
_PROJECT_TAG_STRIP_RE = re.compile(r'\[projec?t:[a-zA-Z0-9_-]+\]\s*')

_MISSIONS_DEFAULT = "# Missions\n\n## En attente\n\n## En cours\n\n## Terminées\n"


def load_dotenv():
    """Load .env file from the project root, stripping quotes from values.

    Uses os.environ.setdefault so existing env vars are not overwritten.
    """
    env_path = KOAN_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def parse_project(text: str) -> Tuple[Optional[str], str]:
    """Extract [project:name] or [projet:name] from text.

    Returns (project_name, cleaned_text) where cleaned_text has the tag removed.
    Returns (None, text) if no tag found.
    """
    match = _PROJECT_TAG_RE.search(text)
    if match:
        project = match.group(1)
        cleaned = _PROJECT_TAG_STRIP_RE.sub('', text).strip()
        return project, cleaned
    return None, text


def insert_pending_mission(missions_path: Path, entry: str):
    """Insert a mission entry into the pending section of missions.md.

    Uses file locking to prevent race conditions between awake.py and dashboard.py.
    Creates the file with default structure if it doesn't exist.
    """
    if not missions_path.exists():
        content = _MISSIONS_DEFAULT
    else:
        content = missions_path.read_text()
        if not content.strip():
            content = _MISSIONS_DEFAULT

    marker = None
    for candidate in ("## En attente", "## Pending"):
        if candidate in content:
            marker = candidate
            break

    if marker:
        idx = content.index(marker) + len(marker)
        while idx < len(content) and content[idx] == "\n":
            idx += 1
        content = content[:idx] + f"\n{entry}\n" + content[idx:]
    else:
        content += f"\n## En attente\n\n{entry}\n"

    # Write with file locking to prevent races with awake.py / dashboard.py
    with open(missions_path, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(content)
        fcntl.flock(f, fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# Conversation history management (Telegram + Dashboard)
# ---------------------------------------------------------------------------

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
    except Exception as e:
        print(f"[utils] Error saving message to history: {e}")


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
    except Exception as e:
        print(f"[utils] Error loading history: {e}")
        return []


def format_conversation_history(messages: List[Dict[str, str]]) -> str:
    """Format conversation history for inclusion in the prompt.

    Args:
        messages: List of message dicts from load_recent_telegram_history

    Returns:
        Formatted string ready to include in the prompt
    """
    if not messages:
        return ""

    lines = ["Recent conversation:"]
    for msg in messages:
        role_label = "Human" if msg["role"] == "user" else "Kōan"
        lines.append(f"{role_label}: {msg['text']}")

    return "\n".join(lines)
