#!/usr/bin/env python3
"""
Koan -- Shared utilities

Consolidates duplicated helpers used across modules:
- load_dotenv: .env file loading
- parse_project: [project:name] / [projet:name] tag extraction
- insert_pending_mission: append mission to missions.md pending section
"""

import fcntl
import os
import re
from pathlib import Path
from typing import Optional, Tuple


KOAN_ROOT = Path(__file__).parent.parent

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
