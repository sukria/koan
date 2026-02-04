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

DEFAULT_SKELETON = "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n"


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

    return content


def count_pending(content: str) -> int:
    """Count pending mission items."""
    sections = parse_sections(content)
    return len(sections["pending"])


def extract_next_pending(content: str, project_name: str = "") -> str:
    """Return the first pending mission line, or empty string if none.

    If project_name is given, only returns missions tagged [projet:name]
    or [project:name], or under a ### project:name / ### projet:name sub-header,
    or untagged missions (outside any sub-header).
    """
    in_pending = False
    current_subheader_project = ""  # project from ### sub-header, empty = no sub-header
    for line in content.splitlines():
        stripped = line.strip()
        stripped_lower = stripped.lower()

        if stripped_lower.startswith("## "):
            section_key = classify_section(stripped_lower[3:].strip())
            if section_key == "pending":
                in_pending = True
                current_subheader_project = ""
            elif in_pending:
                break  # Left the pending section
            continue

        if not in_pending:
            continue

        # Track ### project:X sub-headers within pending section
        if stripped_lower.startswith("### "):
            subheader_match = re.search(
                r"###\s+projec?t\s*:\s*([a-zA-Z0-9_-]+)", stripped, re.IGNORECASE
            )
            if subheader_match:
                current_subheader_project = subheader_match.group(1).lower()
            else:
                current_subheader_project = ""
            continue

        if not stripped.startswith("- "):
            continue

        if project_name:
            # 1. Check inline tag first (takes priority)
            tag_match = re.search(r"\[projec?t:([a-zA-Z0-9_-]+)\]", line)
            if tag_match:
                if tag_match.group(1).lower() != project_name.lower():
                    continue
            elif current_subheader_project:
                # 2. Check sub-header project context
                if current_subheader_project != project_name.lower():
                    continue
            # 3. No tag and no sub-header = untagged, always matches

        return stripped

    return ""


def extract_project_tag(line: str) -> str:
    """Extract project name from a mission line or block, or 'default'.

    Checks for:
    1. Inline tag: [project:name] or [projet:name]
    2. Sub-header: ### project:name or ### projet:name
    """
    # Inline tag (brackets)
    match = re.search(r'\[(?:project|projet):([a-zA-Z0-9_-]+)\]', line)
    if match:
        return match.group(1)
    # Sub-header format (### project:name)
    match = re.search(r'###\s+projec?t\s*:\s*([a-zA-Z0-9_-]+)', line, re.IGNORECASE)
    if match:
        return match.group(1)
    return "default"


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


def format_queue(content: str) -> str:
    """Build a full numbered queue of pending and in-progress missions.

    Returns a formatted string showing all missions with position numbers,
    grouped as in-progress first then pending. Project tags are stripped
    from display but project name is shown inline.
    """
    sections = parse_sections(content)
    in_progress = sections.get("in_progress", [])
    pending = sections.get("pending", [])

    if not in_progress and not pending:
        return "Queue is empty. Nothing in progress."

    lines = ["📋 Mission Queue\n"]

    if in_progress:
        lines.append("▶️ In progress:")
        for item in in_progress:
            project = extract_project_tag(item)
            display = _strip_project_tag(item)
            tag = f" [{project}]" if project != "default" else ""
            lines.append(f"  → {display}{tag}")

    if pending:
        lines.append(f"\n⏳ Pending ({len(pending)}):")
        for i, item in enumerate(pending, 1):
            project = extract_project_tag(item)
            display = _strip_project_tag(item)
            tag = f" [{project}]" if project != "default" else ""
            lines.append(f"  {i}. {display}{tag}")

    return "\n".join(lines)


def _strip_project_tag(item: str) -> str:
    """Remove [project:X] / [projet:X] tag and leading '- ' from a mission line."""
    # Take first line only (for multi-line blocks)
    first_line = item.split("\n")[0]
    # Remove leading "- "
    if first_line.startswith("- "):
        first_line = first_line[2:]
    # Remove project tag
    first_line = re.sub(r'\[projec?t:[a-zA-Z0-9_-]+\]\s*', '', first_line)
    return first_line.strip()


def reorder_mission(content: str, position: int, target: int = 1) -> Tuple[str, str]:
    """Move a pending mission from one position to another.

    Args:
        content: Full missions.md content.
        position: 1-indexed position of the mission to move (in pending list).
        target: 1-indexed target position (default 1 = top of queue).

    Returns:
        (new_content, moved_text) where moved_text is the mission that was moved.

    Raises:
        ValueError: If position is invalid or no pending missions.
    """
    lines = content.splitlines()
    boundaries = find_section_boundaries(lines)

    if "pending" not in boundaries:
        raise ValueError("No pending section found.")

    start, end = boundaries["pending"]

    # Collect pending items as (start_line_idx, end_line_idx, text) tuples
    items = []
    i = start + 1  # Skip the ## header line
    while i < end:
        stripped = lines[i].strip()
        if stripped.startswith("- "):
            item_start = i
            i += 1
            # Include continuation lines (indented, not a new item or header)
            while i < end:
                next_stripped = lines[i].strip()
                if (next_stripped.startswith("- ") or
                        next_stripped.startswith("## ") or
                        next_stripped.startswith("### ") or
                        next_stripped == ""):
                    break
                i += 1
            items.append((item_start, i, "\n".join(lines[item_start:i])))
        else:
            i += 1

    if not items:
        raise ValueError("No pending missions to reorder.")

    if position < 1 or position > len(items):
        raise ValueError(
            f"Invalid position: {position}. Queue has {len(items)} pending mission(s)."
        )

    if target < 1 or target > len(items):
        raise ValueError(
            f"Invalid target: {target}. Queue has {len(items)} pending mission(s)."
        )

    if position == target:
        raise ValueError(f"Mission #{position} is already at position {target}.")

    # Extract the item to move
    moved_start, moved_end, moved_text = items[position - 1]

    # Remove the moved item's lines
    new_lines = lines[:moved_start] + lines[moved_end:]

    # Recalculate target insertion point after removal
    new_boundaries = find_section_boundaries(new_lines)
    new_start, new_end = new_boundaries["pending"]

    new_items = []
    j = new_start + 1
    while j < new_end:
        s = new_lines[j].strip()
        if s.startswith("- "):
            item_start_j = j
            j += 1
            while j < new_end:
                ns = new_lines[j].strip()
                if (ns.startswith("- ") or
                        ns.startswith("## ") or
                        ns.startswith("### ") or
                        ns == ""):
                    break
                j += 1
            new_items.append(item_start_j)
        else:
            j += 1

    # Determine insertion line index
    if target == 1:
        insert_idx = new_start + 1
        while insert_idx < new_end and new_lines[insert_idx].strip() == "":
            insert_idx += 1
    elif target - 1 < len(new_items):
        insert_idx = new_items[target - 1]
    else:
        if new_items:
            last_start = new_items[-1]
            insert_idx = last_start + 1
            while insert_idx < new_end:
                ns = new_lines[insert_idx].strip()
                if (ns.startswith("- ") or
                        ns.startswith("## ") or
                        ns.startswith("### ") or
                        ns == ""):
                    break
                insert_idx += 1
        else:
            insert_idx = new_start + 1

    # Insert the moved lines
    moved_lines = moved_text.splitlines()
    result_lines = new_lines[:insert_idx] + moved_lines + new_lines[insert_idx:]

    first_line = moved_text.split("\n")[0]
    display = _strip_project_tag(first_line)

    return "\n".join(result_lines), display
