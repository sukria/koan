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
import sys
import tempfile
import threading
import yaml
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, List, Dict


if "KOAN_ROOT" not in os.environ:
    raise SystemExit("KOAN_ROOT environment variable is not set. Run via 'make run' or 'make awake'.")
KOAN_ROOT = Path(os.environ["KOAN_ROOT"])

# Pre-compiled regex for project tag extraction (accepts both [project:X] and [projet:X])
_PROJECT_TAG_RE = re.compile(r'\[projec?t:([a-zA-Z0-9_-]+)\]')
_PROJECT_TAG_STRIP_RE = re.compile(r'\[projec?t:[a-zA-Z0-9_-]+\]\s*')

_MISSIONS_DEFAULT = "# Missions\n\n## En attente\n\n## En cours\n\n## Terminées\n"
_MISSIONS_LOCK = threading.Lock()


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


def load_config() -> dict:
    """Load configuration from instance/config.yaml.

    Returns the full config dict, or empty dict if file doesn't exist.
    """
    config_path = KOAN_ROOT / "instance" / "config.yaml"
    if not config_path.exists():
        return {}
    try:
        with open(config_path, "r") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        print(f"[utils] Error loading config: {e}")
        return {}


def get_allowed_tools() -> str:
    """Get comma-separated list of allowed tools from config.

    Returns default tools if config doesn't specify.
    """
    config = load_config()
    tools = config.get("tools", {}).get("allowed", ["Read", "Glob", "Grep", "Edit", "Write"])
    return ",".join(tools)


def get_tools_description() -> str:
    """Get tools description from config for inclusion in prompts."""
    config = load_config()
    return config.get("tools", {}).get("description", "")


def get_auto_merge_config(config: dict, project_name: str) -> dict:
    """Get auto-merge config with per-project override support.

    Merges global defaults with project-specific overrides.

    Args:
        config: Full config dict from load_config()
        project_name: Name of the project (e.g., "koan", "anantys-back")

    Returns:
        Merged config with keys: enabled, base_branch, strategy, rules
    """
    global_cfg = config.get("git_auto_merge", {})
    project_cfg = config.get("projects", {}).get(project_name, {}).get("git_auto_merge", {})

    # Deep merge: project overrides global
    return {
        "enabled": project_cfg.get("enabled", global_cfg.get("enabled", True)),
        "base_branch": project_cfg.get("base_branch", global_cfg.get("base_branch", "main")),
        "strategy": project_cfg.get("strategy", global_cfg.get("strategy", "squash")),
        "rules": project_cfg.get("rules", global_cfg.get("rules", []))
    }


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


def atomic_write(path: Path, content: str):
    """Write content to a file atomically using write-to-temp + rename.

    Prevents data loss if the process crashes mid-write. Uses an exclusive
    lock on the temp file to serialize concurrent writers.
    """
    dir_path = path.parent
    fd, tmp = tempfile.mkstemp(dir=str(dir_path), prefix=".koan-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def insert_pending_mission(missions_path: Path, entry: str):
    """Insert a mission entry into the pending section of missions.md.

    Uses file locking for the entire read-modify-write cycle to prevent
    TOCTOU race conditions between awake.py and dashboard.py.
    Creates the file with default structure if it doesn't exist.
    """
    # Thread lock (in-process) + file lock (cross-process) for full protection
    with _MISSIONS_LOCK:
        if not missions_path.exists():
            missions_path.write_text(_MISSIONS_DEFAULT)

        with open(missions_path, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            content = f.read()
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

            f.seek(0)
            f.truncate()
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
        role_label = "Human" if msg["role"] == "user" else "Kōan"
        text = msg["text"]
        if len(text) > 500:
            text = text[:500] + "..."
        line = f"{role_label}: {text}"
        total += len(line) + 1
        if total > max_chars:
            break
        lines.append(line)

    return "\n".join(lines)
