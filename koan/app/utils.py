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

_MISSIONS_DEFAULT = "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n"
_MISSIONS_LOCK = threading.Lock()


def get_journal_file(instance_dir: Path, target_date, project_name: str) -> Path:
    """Find journal file for a project on a given date.

    Supports both nested (journal/YYYY-MM-DD/project.md) and
    flat (journal/YYYY-MM-DD.md) structures. Returns nested path as default.

    Args:
        instance_dir: Path to instance directory
        target_date: date object or string "YYYY-MM-DD"
        project_name: Project name (used for nested structure)

    Returns:
        Path to journal file (may not exist)
    """
    if hasattr(target_date, 'strftime'):
        date_str = target_date.strftime("%Y-%m-%d")
    else:
        date_str = str(target_date)

    journal_dir = instance_dir / "journal"
    nested = journal_dir / date_str / f"{project_name}.md"
    if nested.exists():
        return nested

    flat = journal_dir / f"{date_str}.md"
    if flat.exists():
        return flat

    return nested


def read_all_journals(instance_dir: Path, target_date) -> str:
    """Read all journal entries for a date across all project subdirs.

    Combines flat (legacy) and nested per-project files.

    Args:
        instance_dir: Path to instance directory
        target_date: date object or string "YYYY-MM-DD"

    Returns:
        Combined journal content
    """
    if hasattr(target_date, 'strftime'):
        date_str = target_date.strftime("%Y-%m-%d")
    else:
        date_str = str(target_date)

    journal_base = instance_dir / "journal"
    journal_dir = journal_base / date_str
    parts = []

    # Check for flat file (legacy)
    flat = journal_base / f"{date_str}.md"
    if flat.is_file():
        parts.append(flat.read_text())

    # Check nested per-project files
    if journal_dir.is_dir():
        for f in sorted(journal_dir.iterdir()):
            if f.suffix == ".md":
                parts.append(f"[{f.stem}]\n{f.read_text()}")

    return "\n\n---\n\n".join(parts)


def get_latest_journal(instance_dir: Path, project: Optional[str] = None,
                       target_date=None, max_chars: int = 500) -> str:
    """Read the latest journal entry, optionally filtered by project.

    Args:
        instance_dir: Path to instance directory
        project: Project name filter (None = all projects)
        target_date: date object or "YYYY-MM-DD" string (None = today)
        max_chars: Maximum characters to return (tail)

    Returns:
        Formatted journal excerpt or informative "nothing found" message
    """
    from datetime import date as _date

    if target_date is None:
        target_date = _date.today()

    if hasattr(target_date, 'strftime'):
        date_str = target_date.strftime("%Y-%m-%d")
    else:
        date_str = str(target_date)

    if project:
        journal_path = get_journal_file(instance_dir, target_date, project)
        if not journal_path.exists():
            return f"No journal for {project} on {date_str}."
        content = journal_path.read_text().strip()
        if not content:
            return f"Empty journal for {project} on {date_str}."
        header = f"📓 {project} — {date_str}"
    else:
        content = read_all_journals(instance_dir, target_date)
        if not content:
            return f"No journal for {date_str}."
        header = f"📓 Journal — {date_str}"

    # Tail: keep last max_chars
    if len(content) > max_chars:
        content = "...\n" + content[-(max_chars - 4):]

    return f"{header}\n\n{content}"


def append_to_journal(instance_dir: Path, project_name: str, content: str):
    """Append content to today's journal file for a project.

    Creates the directory structure if needed. Uses file locking.

    Args:
        instance_dir: Path to instance directory
        project_name: Project name
        content: Content to append
    """
    from datetime import datetime as _dt
    date_str = _dt.now().strftime("%Y-%m-%d")
    journal_dir = instance_dir / "journal" / date_str
    journal_dir.mkdir(parents=True, exist_ok=True)
    journal_file = journal_dir / f"{project_name}.md"

    with open(journal_file, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(content)
        fcntl.flock(f, fcntl.LOCK_UN)


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
    except (yaml.YAMLError, OSError) as e:
        print(f"[utils] Error loading config: {e}")
        return {}


def get_chat_tools() -> str:
    """Get comma-separated list of tools for chat responses.

    Chat uses a restricted set by default (read-only) to prevent prompt
    injection attacks from Telegram messages. Bash is explicitly excluded.

    Config key: tools.chat (default: Read, Glob, Grep)

    Returns:
        Comma-separated tool names.
    """
    config = load_config()
    default_chat_tools = ["Read", "Glob", "Grep"]
    tools = config.get("tools", {}).get("chat", default_chat_tools)
    return ",".join(tools)


def get_mission_tools() -> str:
    """Get comma-separated list of tools for mission execution.

    Missions run with full tool access including Bash for code execution.

    Config key: tools.mission (default: Read, Glob, Grep, Edit, Write, Bash)

    Returns:
        Comma-separated tool names.
    """
    config = load_config()
    default_mission_tools = ["Read", "Glob", "Grep", "Edit", "Write", "Bash"]
    tools = config.get("tools", {}).get("mission", default_mission_tools)
    return ",".join(tools)


# Backward compatibility alias
def get_allowed_tools() -> str:
    """Deprecated: Use get_chat_tools() or get_mission_tools() instead."""
    return get_mission_tools()


def get_tools_description() -> str:
    """Get tools description from config for inclusion in prompts."""
    config = load_config()
    return config.get("tools", {}).get("description", "")


def get_model_config() -> dict:
    """Get model configuration from config.yaml.

    Returns dict with keys: mission, chat, lightweight, fallback, review_mode.
    Empty strings mean "use default model".
    """
    config = load_config()
    defaults = {
        "mission": "",
        "chat": "",
        "lightweight": "haiku",
        "fallback": "sonnet",
        "review_mode": "",
    }
    models = config.get("models", {})
    return {k: models.get(k, v) for k, v in defaults.items()}


def get_start_on_pause() -> bool:
    """Check if start_on_pause is enabled in config.yaml.

    Returns True if koan should boot directly into pause mode.
    """
    config = load_config()
    return bool(config.get("start_on_pause", False))


def get_max_runs() -> int:
    """Get maximum runs per day from config.yaml.

    This is the primary source of truth for max_runs configuration.
    Returns default of 20 if not configured.
    """
    config = load_config()
    return int(config.get("max_runs_per_day", 20))


def get_interval_seconds() -> int:
    """Get interval between runs in seconds from config.yaml.

    This is the primary source of truth for run interval configuration.
    Returns default of 300 (5 minutes) if not configured.
    """
    config = load_config()
    return int(config.get("interval_seconds", 300))
def get_fast_reply_model() -> str:
    """Get model to use for fast replies (command handlers like /usage, /sparring).

    When config.fast_reply is True, returns the lightweight model (usually Haiku)
    for faster, cheaper responses. When False, returns empty string (use default).

    Returns:
        Model name string (e.g., "haiku") or empty string for default model.
    """
    config = load_config()
    fast_reply = config.get("fast_reply", False)
    if fast_reply:
        models = get_model_config()
        return models["lightweight"]
    return ""


def get_contemplative_chance() -> int:
    """Get probability (0-100) of triggering contemplative mode on autonomous runs.

    When no mission is pending, this is the chance that koan will run a
    contemplative session instead of autonomous work. Allows for regular
    moments of reflection without waiting for budget exhaustion.

    Returns:
        Integer percentage (0-100). Default: 10 (one in ten autonomous runs).
    """
    config = load_config()
    return int(config.get("contemplative_chance", 10))


def build_claude_flags(
    model: str = "",
    fallback: str = "",
    disallowed_tools: Optional[List[str]] = None,
) -> List[str]:
    """Build extra CLI flags for a Claude invocation.

    Args:
        model: Model name/alias (empty = use default)
        fallback: Fallback model when primary is overloaded (empty = none)
        disallowed_tools: Tools to block (e.g., ["Bash", "Edit", "Write"] for read-only)

    Returns:
        List of CLI flag strings to append to the command.
    """
    flags: List[str] = []
    if model:
        flags.extend(["--model", model])
    if fallback:
        flags.extend(["--fallback-model", fallback])
    if disallowed_tools:
        flags.extend(["--disallowedTools"] + disallowed_tools)
    return flags


def get_claude_flags_for_role(role: str, autonomous_mode: str = "") -> str:
    """Get CLI flags for a Claude invocation role, as a space-separated string.

    Designed to be called from run.sh to get model/fallback flags.

    Args:
        role: One of "mission", "chat", "lightweight", "contemplative"
        autonomous_mode: Current mode (review/implement/deep) — affects tool restrictions

    Returns:
        Space-separated CLI flags string (may be empty)
    """
    models = get_model_config()
    flags: List[str] = []

    if role == "mission":
        model = models["mission"]
        # In review mode, prefer cheaper model if configured
        if autonomous_mode == "review" and models["review_mode"]:
            model = models["review_mode"]
        if model:
            flags.extend(["--model", model])
        if models["fallback"]:
            flags.extend(["--fallback-model", models["fallback"]])
        # Review mode: block write tools
        if autonomous_mode == "review":
            flags.extend(["--disallowedTools", "Bash", "Edit", "Write"])
    elif role == "contemplative":
        if models["lightweight"]:
            flags.extend(["--model", models["lightweight"]])
    elif role == "chat":
        if models["chat"]:
            flags.extend(["--model", models["chat"]])
        if models["fallback"]:
            flags.extend(["--fallback-model", models["fallback"]])

    return " ".join(flags)


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


def get_known_projects() -> List[str]:
    """Return list of known project names from KOAN_PROJECTS or KOAN_PROJECT_PATH.

    Parses the KOAN_PROJECTS env var (format: 'name:path;name2:path2').
    Falls back to KOAN_PROJECT_PATH with name 'default'.
    Returns empty list if neither is set.
    """
    projects_env = os.environ.get("KOAN_PROJECTS", "")
    if projects_env:
        names = []
        for pair in projects_env.split(";"):
            pair = pair.strip()
            if ":" in pair:
                name = pair.split(":")[0].strip()
                if name:
                    names.append(name)
        return sorted(names)

    project_path = os.environ.get("KOAN_PROJECT_PATH", "")
    if project_path:
        return ["default"]

    return []


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
            for candidate in ("## Pending", "## En attente"):
                if candidate in content:
                    marker = candidate
                    break

            if marker:
                idx = content.index(marker) + len(marker)
                while idx < len(content) and content[idx] == "\n":
                    idx += 1
                content = content[:idx] + f"\n{entry}\n" + content[idx:]
            else:
                content += f"\n## Pending\n\n{entry}\n"

            from app.missions import normalize_content
            content = normalize_content(content)

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
    except OSError as e:
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
    except OSError as e:
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
    print(f"[utils] Compacted {count} messages → {topics_file.name}")
    return count


def append_to_outbox(outbox_path: Path, content: str):
    """Append content to outbox.md with file locking.

    Safe to call from run.sh via: python3 -c "from app.utils import append_to_outbox; ..."
    or from Python directly.
    """
    with open(outbox_path, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(content)
        fcntl.flock(f, fcntl.LOCK_UN)
