#!/usr/bin/env python3
"""
Kōan -- Shared utilities

Core shared utilities used across modules:
- load_dotenv: .env file loading
- load_config: config.yaml loading
- parse_project: [project:name] / [projet:name] tag extraction
- atomic_write: crash-safe file writes
- insert_pending_mission: append mission to missions.md pending section
- modify_missions_file: locked read-modify-write on missions.md
- get_known_projects / resolve_project_path: project registry
- append_to_outbox: outbox file appending

Configuration, journal, and telegram history functions have been
extracted to dedicated modules (config.py, journal.py, telegram_history.py).
Backward-compatible re-exports are provided below.
"""

import fcntl
import os
import re
import tempfile
import threading
import yaml
from pathlib import Path
from typing import Optional, Tuple


if "KOAN_ROOT" not in os.environ:
    raise SystemExit("KOAN_ROOT environment variable is not set. Run via 'make run' or 'make awake'.")
KOAN_ROOT = Path(os.environ["KOAN_ROOT"])

# Pre-compiled regex for project tag extraction (accepts both [project:X] and [projet:X])
_PROJECT_TAG_RE = re.compile(r'\[projec?t:([a-zA-Z0-9_-]+)\]')
_PROJECT_TAG_STRIP_RE = re.compile(r'\[projec?t:[a-zA-Z0-9_-]+\]\s*')

_MISSIONS_DEFAULT = "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n"
_MISSIONS_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Core utilities (stay here)
# ---------------------------------------------------------------------------

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


def detect_project_from_text(text: str) -> Tuple[Optional[str], str]:
    """Detect project name from the first word of text.

    If the first word matches a known project name (case-insensitive),
    returns (project_name, remaining_text). Otherwise returns (None, text).
    """
    parts = text.strip().split(None, 1)
    if not parts:
        return None, text

    first_word = parts[0].lower()
    known = get_known_projects()
    project_names = {name.lower(): name for name, _path in known}

    if first_word in project_names:
        remaining = parts[1].strip() if len(parts) > 1 else ""
        return project_names[first_word], remaining

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


def insert_pending_mission(missions_path: Path, entry: str, *, urgent: bool = False):
    """Insert a mission entry into the pending section of missions.md.

    By default, inserts at the bottom of the pending section (FIFO queue).
    When urgent=True, inserts at the top (next to be picked up).

    Uses file locking for the entire read-modify-write cycle to prevent
    TOCTOU race conditions between awake.py and dashboard.py.
    Creates the file with default structure if it doesn't exist.
    """
    from app.missions import insert_mission

    # Thread lock (in-process) + file lock (cross-process) for full protection
    with _MISSIONS_LOCK:
        if not missions_path.exists():
            missions_path.write_text(_MISSIONS_DEFAULT)

        with open(missions_path, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            content = f.read()
            if not content.strip():
                content = _MISSIONS_DEFAULT

            content = insert_mission(content, entry, urgent=urgent)

            f.seek(0)
            f.truncate()
            f.write(content)
            fcntl.flock(f, fcntl.LOCK_UN)


def modify_missions_file(missions_path: Path, transform):
    """Apply a transform function to missions.md content with file locking.

    Args:
        missions_path: Path to missions.md
        transform: Callable(content: str) -> str that returns modified content.

    Returns the transformed content.
    """
    with _MISSIONS_LOCK:
        if not missions_path.exists():
            missions_path.write_text(_MISSIONS_DEFAULT)

        with open(missions_path, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            content = f.read()
            if not content.strip():
                content = _MISSIONS_DEFAULT

            new_content = transform(content)

            f.seek(0)
            f.truncate()
            f.write(new_content)
            fcntl.flock(f, fcntl.LOCK_UN)

    return new_content


def get_known_projects() -> list:
    """Return sorted list of (name, path) tuples.

    Resolution order:
    1. projects.yaml (if file exists at KOAN_ROOT)
    2. KOAN_PROJECTS env var (fallback)

    Returns empty list if none is configured.
    """
    # 1. Try projects.yaml
    try:
        from app.projects_config import load_projects_config, get_projects_from_config
        config = load_projects_config(str(KOAN_ROOT))
        if config is not None:
            return get_projects_from_config(config)
    except Exception:
        # Invalid YAML or import error — fall through to env var
        pass

    # 2. KOAN_PROJECTS env var
    projects_str = os.environ.get("KOAN_PROJECTS", "")
    if projects_str:
        result = []
        for pair in projects_str.split(";"):
            pair = pair.strip()
            if ":" in pair:
                name, path = pair.split(":", 1)
                result.append((name.strip(), path.strip()))
        return sorted(result, key=lambda x: x[0].lower())

    return []


def resolve_project_path(repo_name: str) -> Optional[str]:
    """Find local project path matching a repository name.

    Tries in order:
    1. Exact match on project name (case-insensitive)
    2. Match on directory basename (case-insensitive)
    3. Fallback to single project if only one configured
    """
    projects = get_known_projects()

    # Try exact match on project name
    for name, path in projects:
        if name.lower() == repo_name.lower():
            return path

    # Try matching repo name against directory basename
    for name, path in projects:
        if Path(path).name.lower() == repo_name.lower():
            return path

    # Fallback to single project
    if len(projects) == 1:
        return projects[0][1]

    return None


def append_to_outbox(outbox_path: Path, content: str):
    """Append content to outbox.md with file locking.

    Safe to call from run.py via: python3 -c "from app.utils import append_to_outbox; ..."
    or from Python directly.
    """
    with open(outbox_path, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(content)
        fcntl.flock(f, fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# Backward-compatible re-exports
# ---------------------------------------------------------------------------
# These preserve existing `from app.utils import X` patterns.
# New code should import from the dedicated modules directly.

from app.config import (  # noqa: E402, F401
    get_chat_tools,
    get_mission_tools,
    get_allowed_tools,
    get_tools_description,
    get_model_config,
    get_start_on_pause,
    get_max_runs,
    get_interval_seconds,
    get_fast_reply_model,
    get_branch_prefix,
    get_contemplative_chance,
    build_claude_flags,
    get_claude_flags_for_role,
    get_cli_binary_for_shell,
    get_cli_provider_name,
    get_tool_flags_for_shell,
    get_output_flags_for_shell,
    get_auto_merge_config,
)

from app.journal import (  # noqa: E402, F401
    get_journal_file,
    read_all_journals,
    get_latest_journal,
    append_to_journal,
)

from app.telegram_history import (  # noqa: E402, F401
    save_telegram_message,
    load_recent_telegram_history,
    format_conversation_history,
    compact_telegram_history,
)
