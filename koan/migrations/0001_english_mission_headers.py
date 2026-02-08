"""
Migration 0001: Convert missions.md section headers from French to English.

Replaces:
  - "## En attente"  → "## Pending"
  - "## En cours"    → "## In Progress"
  - "## Terminées"   → "## Done"
  - "## Terminés"    → "## Done"

Silently succeeds if no French headers are found.
"""

import re
from pathlib import Path

# Map French headers to English equivalents (case-insensitive matching)
HEADER_MAP = {
    "en attente": "Pending",
    "en cours": "In Progress",
    "terminées": "Done",
    "terminés": "Done",
}


def migrate(instance_dir: Path) -> None:
    """Convert French section headers to English in missions.md."""
    missions_file = instance_dir / "missions.md"
    if not missions_file.exists():
        return

    content = missions_file.read_text(encoding="utf-8")
    original = content

    for french, english in HEADER_MAP.items():
        # Match "## French Header" with optional trailing whitespace, case-insensitive
        pattern = re.compile(rf"^(##\s+){re.escape(french)}\s*$", re.IGNORECASE | re.MULTILINE)
        content = pattern.sub(rf"\g<1>{english}", content)

    if content != original:
        missions_file.write_text(content, encoding="utf-8")
