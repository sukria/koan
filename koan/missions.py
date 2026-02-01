#!/usr/bin/env python3
"""
Kōan — Centralized missions.md parser

Single source of truth for parsing, querying, and modifying missions.md.
All modules that interact with missions.md should use these functions
instead of reimplementing section detection and parsing.
"""

import re
from typing import Dict, List, Optional, Tuple


# Section name normalization — accepts French and English variants
_SECTION_MAP = {
    "en attente": "pending",
    "pending": "pending",
    "en cours": "in_progress",
    "in progress": "in_progress",
    "in_progress": "in_progress",
    "terminées": "done",
    "terminés": "done",
    "done": "done",
    "completed": "done",
}

DEFAULT_SKELETON = "# Missions\n\n## En attente\n\n## En cours\n\n## Terminées\n"


def classify_section(header_text: str) -> Optional[str]:
    """Normalize a ## header into a canonical section key.

    Returns "pending", "in_progress", "done", or None if unrecognized.
    """
    return _SECTION_MAP.get(header_text.strip().lower())


def parse_sections(content: str) -> Dict[str, List[str]]:
    """Parse missions.md content into structured sections.

    Returns {"pending": [...], "in_progress": [...], "done": [...]}.
    Each item is either a simple "- ..." line or a multi-line block
    (for ### complex missions).
    """
    sections = {"pending": [], "in_progress": [], "done": []}
    current = None
    current_block = []

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            # Flush any pending complex block
            if current_block and current:
                sections[current].append("\n".join(current_block))
                current_block = []
            section_name = stripped[3:].strip()
            current = classify_section(section_name)
            continue

        if current is None:
            continue

        if stripped.startswith("### "):
            # Flush previous block
            if current_block:
                sections[current].append("\n".join(current_block))
            current_block = [line]
        elif current_block:
            if stripped == "":
                sections[current].append("\n".join(current_block))
                current_block = []
            else:
                current_block.append(line)
        elif stripped.startswith("- "):
            sections[current].append(stripped)
        elif stripped and not stripped.startswith("#"):
            # Continuation line (indented sub-items)
            if sections[current]:
                sections[current][-1] += "\n" + line

    # Flush remaining block
    if current_block and current:
        sections[current].append("\n".join(current_block))

    return sections


def insert_mission(content: str, entry: str) -> str:
    """Insert a mission entry into the pending section of missions.md content.

    Returns the updated content string.
    """
    if not content:
        content = DEFAULT_SKELETON

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

    return content


def count_pending(content: str) -> int:
    """Count pending mission items."""
    sections = parse_sections(content)
    return len(sections["pending"])


def extract_next_pending(content: str, project_name: str = "") -> str:
    """Return the first pending mission line, or empty string if none.

    If project_name is given, only returns missions tagged [projet:name]
    or [project:name], or untagged missions.
    """
    in_pending = False
    for line in content.splitlines():
        stripped_lower = line.strip().lower()

        if stripped_lower.startswith("## "):
            section_key = classify_section(stripped_lower[3:].strip())
            if section_key == "pending":
                in_pending = True
            elif in_pending:
                break  # Left the pending section
            continue

        if not in_pending:
            continue

        if not line.strip().startswith("- "):
            continue

        if project_name:
            tag_match = re.search(r"\[projet?:([a-zA-Z0-9_-]+)\]", line)
            if tag_match:
                if tag_match.group(1).lower() != project_name.lower():
                    continue
            # No tag = default project, always matches

        return line.strip()

    return ""


def extract_project_tag(line: str) -> str:
    """Extract project name from a mission line, or 'default'."""
    match = re.search(r'\[(?:project|projet):([a-zA-Z0-9_-]+)\]', line)
    return match.group(1) if match else "default"


def group_by_project(content: str) -> Dict[str, Dict[str, List[str]]]:
    """Parse missions and group them by project.

    Returns {project: {"pending": [...], "in_progress": [...]}}.
    """
    from collections import defaultdict
    result = defaultdict(lambda: {"pending": [], "in_progress": []})

    sections = parse_sections(content)
    for item in sections["pending"]:
        project = extract_project_tag(item)
        result[project]["pending"].append(item)
    for item in sections["in_progress"]:
        project = extract_project_tag(item)
        result[project]["in_progress"].append(item)

    return dict(result)


def find_section_boundaries(lines: List[str]) -> Dict[str, Tuple[int, int]]:
    """Find line indices for each section.

    Returns {section_key: (start_line, end_line)} where start_line is the
    ## header line and end_line is the next ## header (or len(lines)).
    """
    boundaries = {}
    section_order = []

    for i, line in enumerate(lines):
        stripped = line.strip().lower()
        if stripped.startswith("## "):
            key = classify_section(stripped[3:].strip())
            if key:
                section_order.append((key, i))

    for idx, (key, start) in enumerate(section_order):
        if idx + 1 < len(section_order):
            end = section_order[idx + 1][1]
        else:
            end = len(lines)
        boundaries[key] = (start, end)

    return boundaries
