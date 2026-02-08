#!/usr/bin/env python3
"""
Kōan — Mission extraction

Extracts the next pending mission from missions.md, scoped to the "Pending"
section only. Prints the mission line to stdout (empty if none found).

This replaces the naive `grep -m1 "^- "` which could match lines from any section.

Usage:
    python3 extract_mission.py /path/to/instance/missions.md [project_name]

If project_name is given, only returns missions tagged [project:name] or untagged.
"""

import sys
from pathlib import Path


def extract_next_mission(missions_path: str, project_name: str = "") -> str:
    """Return the first pending mission line, or empty string if none."""
    from app.missions import extract_next_pending

    path = Path(missions_path)
    if not path.exists():
        return ""

    return extract_next_pending(path.read_text(), project_name)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <missions.md> [project_name]", file=sys.stderr)
        sys.exit(1)

    missions_file = sys.argv[1]
    proj = sys.argv[2] if len(sys.argv) > 2 else ""
    result = extract_next_mission(missions_file, proj)
    print(result)
