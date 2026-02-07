"""Shared module-level state for the Telegram bridge.

This module holds configuration and runtime constants that are shared between
awake.py (main loop, chat, outbox) and command_handlers.py (slash commands).
Extracted to avoid circular imports between those two modules.
"""

import os
from pathlib import Path
from typing import Optional

from app.skills import SkillRegistry, build_registry
from app.utils import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ.get("KOAN_TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("KOAN_TELEGRAM_CHAT_ID", "")
POLL_INTERVAL = int(os.environ.get("KOAN_BRIDGE_INTERVAL", "3"))
CHAT_TIMEOUT = int(os.environ.get("KOAN_CHAT_TIMEOUT", "180"))

KOAN_ROOT = Path(os.environ["KOAN_ROOT"])
INSTANCE_DIR = KOAN_ROOT / "instance"
MISSIONS_FILE = INSTANCE_DIR / "missions.md"
OUTBOX_FILE = INSTANCE_DIR / "outbox.md"
TELEGRAM_HISTORY_FILE = INSTANCE_DIR / "telegram-history.jsonl"
TOPICS_FILE = INSTANCE_DIR / "previous-discussions-topics.json"
PROJECT_PATH = os.environ.get("KOAN_PROJECT_PATH", "")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Context loaded once at startup
SOUL = ""
soul_path = INSTANCE_DIR / "soul.md"
if soul_path.exists():
    SOUL = soul_path.read_text()

SUMMARY = ""
summary_path = INSTANCE_DIR / "memory" / "summary.md"
if summary_path.exists():
    SUMMARY = summary_path.read_text()

# Skills registry — loaded once at import time
_skill_registry: Optional[SkillRegistry] = None


def _get_registry() -> SkillRegistry:
    """Get or initialize the skill registry (lazy singleton)."""
    global _skill_registry
    if _skill_registry is None:
        extra_dirs = []
        instance_skills = INSTANCE_DIR / "skills"
        if instance_skills.is_dir():
            extra_dirs.append(instance_skills)
        _skill_registry = build_registry(extra_dirs)
    return _skill_registry


def _reset_registry():
    """Reset the registry (for testing)."""
    global _skill_registry
    _skill_registry = None
