#!/usr/bin/env python3
"""
Koan -- Recurring missions

Manages recurring missions (hourly, daily, weekly) stored in instance/recurring.json.
The scheduler checks which missions are due and inserts them into missions.md pending
section for normal execution by the run loop.

Storage format (recurring.json):
[
    {
        "id": "rec_1706990000",
        "frequency": "daily",
        "text": "summarize unread emails",
        "project": null,
        "created": "2026-02-03T22:00:00",
        "last_run": null,
        "enabled": true
    },
    ...
]
"""

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from app.utils import atomic_write, insert_pending_mission


FREQUENCIES = ("hourly", "daily", "weekly")


def load_recurring(recurring_path: Path) -> List[Dict]:
    """Load recurring missions from JSON file.

    Returns empty list if file doesn't exist or is invalid.
    """
    if not recurring_path.exists():
        return []
    try:
        data = json.loads(recurring_path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        return data
    except (json.JSONDecodeError, OSError):
        return []


def save_recurring(recurring_path: Path, missions: List[Dict]):
    """Save recurring missions to JSON file atomically."""
    content = json.dumps(missions, ensure_ascii=False, indent=2) + "\n"
    atomic_write(recurring_path, content)


def add_recurring(
    recurring_path: Path,
    frequency: str,
    text: str,
    project: Optional[str] = None,
) -> Dict:
    """Add a new recurring mission.

    Args:
        recurring_path: Path to recurring.json
        frequency: One of "hourly", "daily", "weekly"
        text: Mission description
        project: Optional project name

    Returns:
        The created mission dict

    Raises:
        ValueError: If frequency is invalid
    """
    if frequency not in FREQUENCIES:
        raise ValueError(f"Invalid frequency: {frequency}. Must be one of {FREQUENCIES}")

    missions = load_recurring(recurring_path)

    mission = {
        "id": f"rec_{int(time.time())}_{os.getpid()}_{len(missions)}",
        "frequency": frequency,
        "text": text.strip(),
        "project": project,
        "created": datetime.now().isoformat(timespec="seconds"),
        "last_run": None,
        "enabled": True,
    }

    missions.append(mission)
    save_recurring(recurring_path, missions)
    return mission


def remove_recurring(recurring_path: Path, identifier: str) -> str:
    """Remove a recurring mission by number (1-indexed) or keyword.

    Args:
        recurring_path: Path to recurring.json
        identifier: Number (1-indexed) or keyword substring

    Returns:
        Description of the removed mission

    Raises:
        ValueError: If identifier doesn't match any mission
    """
    missions = load_recurring(recurring_path)
    if not missions:
        raise ValueError("No recurring missions configured.")

    enabled = [m for m in missions if m.get("enabled", True)]
    if not enabled:
        raise ValueError("No active recurring missions.")

    if identifier.isdigit():
        idx = int(identifier) - 1
        if idx < 0 or idx >= len(enabled):
            raise ValueError(
                f"Invalid number: {identifier}. "
                f"Valid range: 1-{len(enabled)}"
            )
        target = enabled[idx]
    else:
        # Keyword match (case-insensitive substring)
        matches = [
            m for m in enabled
            if identifier.lower() in m["text"].lower()
        ]
        if not matches:
            raise ValueError(f"No recurring mission matching '{identifier}'.")
        if len(matches) > 1:
            raise ValueError(
                f"Multiple matches for '{identifier}'. Be more specific or use a number."
            )
        target = matches[0]

    # Remove from list
    missions = [m for m in missions if m["id"] != target["id"]]
    save_recurring(recurring_path, missions)
    return f"[{target['frequency']}] {target['text']}"


def list_recurring(recurring_path: Path) -> List[Dict]:
    """List all enabled recurring missions.

    Returns list of mission dicts, sorted by frequency (hourly, daily, weekly).
    """
    missions = load_recurring(recurring_path)
    enabled = [m for m in missions if m.get("enabled", True)]
    freq_order = {"hourly": 0, "daily": 1, "weekly": 2}
    return sorted(enabled, key=lambda m: freq_order.get(m["frequency"], 99))


def format_recurring_list(missions: List[Dict]) -> str:
    """Format recurring missions for display.

    Returns a human-readable string with numbered entries.
    """
    if not missions:
        return "No recurring missions configured."

    lines = ["Recurring missions:"]
    for i, m in enumerate(missions, 1):
        freq = m["frequency"]
        text = m["text"]
        project = m.get("project")
        last_run = m.get("last_run")

        entry = f"  {i}. [{freq}] {text}"
        if project:
            entry += f" (project: {project})"
        if last_run:
            # Show relative time
            try:
                last_dt = datetime.fromisoformat(last_run)
                delta = datetime.now() - last_dt
                if delta.total_seconds() < 3600:
                    entry += f" — last: {int(delta.total_seconds() / 60)}min ago"
                elif delta.total_seconds() < 86400:
                    entry += f" — last: {int(delta.total_seconds() / 3600)}h ago"
                else:
                    entry += f" — last: {int(delta.days)}d ago"
            except (ValueError, TypeError):
                entry += f" — last: {last_run}"
        else:
            entry += " — never run"

        lines.append(entry)

    return "\n".join(lines)


def is_due(mission: Dict, now: Optional[datetime] = None) -> bool:
    """Check if a recurring mission is due for execution.

    Rules:
        - hourly: last_run is None or > 1 hour ago
        - daily: last_run is None or last_run date != today
        - weekly: last_run is None or > 7 days ago
    """
    if not mission.get("enabled", True):
        return False

    now = now or datetime.now()
    last_run = mission.get("last_run")

    if last_run is None:
        return True

    try:
        last_dt = datetime.fromisoformat(last_run)
    except (ValueError, TypeError):
        return True  # Invalid date = treat as never run

    frequency = mission["frequency"]

    if frequency == "hourly":
        return (now - last_dt) >= timedelta(hours=1)
    elif frequency == "daily":
        return last_dt.date() < now.date()
    elif frequency == "weekly":
        return (now - last_dt) >= timedelta(weeks=1)

    return False


def check_and_inject(
    recurring_path: Path,
    missions_path: Path,
    now: Optional[datetime] = None,
) -> List[str]:
    """Check all recurring missions and inject due ones into missions.md.

    This is the main scheduler entry point. Called from run.sh at the top
    of each loop iteration.

    Args:
        recurring_path: Path to recurring.json
        missions_path: Path to missions.md
        now: Optional datetime for testing

    Returns:
        List of mission descriptions that were injected
    """
    missions = load_recurring(recurring_path)
    if not missions:
        return []

    now = now or datetime.now()
    injected = []

    for mission in missions:
        if not is_due(mission, now):
            continue

        text = mission["text"]
        project = mission.get("project")
        freq = mission["frequency"]

        # Build mission entry for missions.md
        tag = f"[{freq}] "
        if project:
            entry = f"- [project:{project}] {tag}{text}"
        else:
            entry = f"- {tag}{text}"

        # Insert into pending section
        insert_pending_mission(missions_path, entry)

        # Update last_run
        mission["last_run"] = now.isoformat(timespec="seconds")
        injected.append(f"[{freq}] {text}")

    # Save updated timestamps
    if injected:
        save_recurring(recurring_path, missions)

    return injected
