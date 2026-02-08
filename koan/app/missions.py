#!/usr/bin/env python3
"""
Kōan — Centralized missions.md parser

Single source of truth for parsing, querying, and modifying missions.md.
All modules that interact with missions.md should use these functions
instead of reimplementing section detection and parsing.
"""

import re
from collections import defaultdict
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


def extract_now_flag(text: str) -> Tuple[bool, str]:
    """Check for --now flag in the first 5 words of mission text.

    Returns (is_urgent, cleaned_text) where cleaned_text has --now removed.
    """
    words = text.split()
    first_five = words[:5]
    if "--now" in first_five:
        words_copy = list(words)
        # Remove the first occurrence of --now from the full word list
        words_copy.remove("--now")
        return True, " ".join(words_copy)
    return False, text


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


def insert_mission(content: str, entry: str, *, urgent: bool = False) -> str:
    """Insert a mission entry into the pending section of missions.md content.

    By default, inserts at the bottom of the pending section (FIFO queue).
    When urgent=True, inserts at the top (next to be picked up).

    Returns the updated content string.
    """
    if not content:
        content = DEFAULT_SKELETON

    if urgent:
        # Insert at top of pending section (right after the header)
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
    else:
        # Insert at bottom of pending section (before next ## header)
        lines = content.splitlines()
        in_pending = False
        last_content_line = None
        pending_header_line = None

        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.lower() in ("## pending", "## en attente"):
                in_pending = True
                pending_header_line = i
                continue
            if in_pending and stripped.startswith("## "):
                break  # Next section
            if in_pending and (stripped.startswith("- ") or
                               (stripped and not stripped.startswith("#") and
                                last_content_line is not None)):
                last_content_line = i

        if pending_header_line is not None:
            insert_after = last_content_line if last_content_line is not None else pending_header_line
            lines.insert(insert_after + 1, entry)
            content = "\n".join(lines)
        else:
            content += f"\n## Pending\n\n{entry}\n"

    return normalize_content(content)


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
    result = defaultdict(lambda: {"pending": [], "in_progress": []})

    sections = parse_sections(content)
    for item in sections["pending"]:
        project = extract_project_tag(item)
        result[project]["pending"].append(item)
    for item in sections["in_progress"]:
        project = extract_project_tag(item)
        result[project]["in_progress"].append(item)

    return dict(result)


def normalize_content(content: str) -> str:
    """Normalize missions.md content by collapsing excessive blank lines.

    Rules:
    - Max 1 blank line between any two non-blank lines
    - No trailing blank lines at end of file (just a final newline)
    - Preserves all non-blank content exactly as-is
    """
    lines = content.splitlines()
    result = []
    prev_blank = False

    for line in lines:
        is_blank = line.strip() == ""
        if is_blank and prev_blank:
            continue  # skip consecutive blank lines
        result.append(line)
        prev_blank = is_blank

    # Strip trailing blank lines, ensure single final newline
    while result and result[-1].strip() == "":
        result.pop()

    return "\n".join(result) + "\n" if result else ""


def parse_ideas(content: str) -> List[str]:
    """Parse the ## Ideas section and return a list of idea items.

    Items start with "- ..." and may include continuation lines (indented
    or non-heading text that follows). The Ideas section is intentionally
    not part of _SECTION_MAP — ideas are never picked up by the agent loop.
    """
    ideas = []
    in_ideas = False

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("## "):
            section_name = stripped[3:].strip().lower()
            if section_name == "ideas":
                in_ideas = True
            elif in_ideas:
                break  # Left the Ideas section
            continue

        if not in_ideas:
            continue

        if stripped.startswith("- "):
            ideas.append(stripped)
        elif stripped and not stripped.startswith("#") and ideas:
            # Continuation line — append to last idea
            ideas[-1] += "\n" + line

    return ideas


def insert_idea(content: str, entry: str) -> str:
    """Insert an idea entry at the bottom of the Ideas section of missions.md.

    Creates the section if it doesn't exist (right after # Missions header).
    Returns the updated content string.
    """
    if not content:
        content = DEFAULT_SKELETON

    # Find ## Ideas section and insert at the bottom
    lines = content.splitlines()
    in_ideas = False
    last_idea_line = None
    ideas_header_line = None

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.lower() == "## ideas":
            in_ideas = True
            ideas_header_line = i
            continue
        if in_ideas and stripped.startswith("## "):
            break  # Next section
        if in_ideas and (stripped.startswith("- ") or
                         (stripped and not stripped.startswith("#") and last_idea_line is not None)):
            last_idea_line = i

    if ideas_header_line is not None:
        # Insert after the last idea, or after the header if section is empty
        insert_after = last_idea_line if last_idea_line is not None else ideas_header_line
        lines.insert(insert_after + 1, entry)
        return normalize_content("\n".join(lines))

    # No Ideas section — create one after # Missions
    if "# Missions" in content:
        idx = content.index("# Missions") + len("# Missions")
        while idx < len(content) and content[idx] == "\n":
            idx += 1
        content = content[:idx] + f"\n## Ideas\n\n{entry}\n\n" + content[idx:]
    else:
        content = f"# Missions\n\n## Ideas\n\n{entry}\n\n" + content

    return normalize_content(content)


def delete_idea(content: str, index: int) -> Tuple[str, Optional[str]]:
    """Delete an idea by 1-based index from the Ideas section.

    Handles multi-line ideas (continuation lines after the initial "- ..." line).
    Returns (updated_content, deleted_text) or (original_content, None) if
    the index is out of range.
    """
    ideas = parse_ideas(content)
    if index < 1 or index > len(ideas):
        return content, None

    target = ideas[index - 1]

    # Find and remove all lines belonging to this idea
    lines = content.splitlines()
    idea_count = 0
    in_ideas = False
    remove_start = None

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.lower().startswith("## "):
            section_name = stripped[3:].strip().lower()
            if section_name == "ideas":
                in_ideas = True
            elif in_ideas:
                break
            continue

        if in_ideas and stripped.startswith("- "):
            # If we were collecting lines for a previous match, stop
            if remove_start is not None:
                break
            idea_count += 1
            if idea_count == index:
                remove_start = i
        elif in_ideas and remove_start is not None:
            if stripped and not stripped.startswith("#"):
                # Continuation line — include in removal
                continue
            else:
                # Empty line or heading — stop collecting
                break

    if remove_start is not None:
        # Determine how many lines to remove
        remove_end = remove_start + 1
        for j in range(remove_start + 1, len(lines)):
            stripped = lines[j].strip()
            if stripped.startswith("- ") or stripped.startswith("## "):
                break
            if stripped == "":
                break
            remove_end = j + 1
        del lines[remove_start:remove_end]
        return normalize_content("\n".join(lines)), target

    return content, None


def promote_idea(content: str, index: int) -> Tuple[str, Optional[str]]:
    """Promote an idea (by 1-based index) to the Pending section.

    Removes the idea from Ideas, adds it to Pending.
    Returns (updated_content, promoted_text) or (original_content, None).
    """
    updated, deleted = delete_idea(content, index)
    if deleted is None:
        return content, None

    # Insert the deleted idea into the pending section (at the top — promoted ideas are urgent)
    updated = insert_mission(updated, deleted, urgent=True)
    return updated, deleted


def promote_all_ideas(content: str) -> Tuple[str, List[str]]:
    """Promote all ideas to the Pending section.

    Returns (updated_content, list_of_promoted_texts).
    If no ideas exist, returns (original_content, []).
    """
    ideas = parse_ideas(content)
    if not ideas:
        return content, []

    # Promote from last to first so indices stay valid
    promoted = []
    updated = content
    for i in range(len(ideas), 0, -1):
        updated, text = promote_idea(updated, i)
        if text is not None:
            promoted.append(text)

    promoted.reverse()  # restore original order
    return updated, promoted


def list_pending(content: str) -> List[str]:
    """Return all pending mission lines."""
    sections = parse_sections(content)
    return sections["pending"]


def cancel_pending_mission(content: str, identifier: str) -> Tuple[str, str]:
    """Cancel a pending mission by number (1-indexed) or keyword match.

    Args:
        content: Full missions.md content.
        identifier: A number string ("3") or keyword ("fix auth").

    Returns:
        (updated_content, cancelled_mission_text)

    Raises:
        ValueError: If no matching mission is found.
    """
    pending = list_pending(content)
    if not pending:
        raise ValueError("No pending missions.")

    identifier = identifier.strip()

    # Determine which pending item to cancel
    target_idx = None
    if identifier.isdigit():
        num = int(identifier) - 1
        if num < 0 or num >= len(pending):
            raise ValueError(
                f"Mission #{identifier} not found. "
                f"There are {len(pending)} pending mission(s)."
            )
        target_idx = num
    else:
        # Keyword match (case-insensitive, first match)
        keyword = identifier.lower()
        for i, item in enumerate(pending):
            if keyword in item.lower():
                target_idx = i
                break
        if target_idx is None:
            raise ValueError(f'No pending mission matching "{identifier}".')

    target_text = pending[target_idx]

    # Remove the target from raw content by finding the Nth "- " line in pending section
    lines = content.splitlines()
    boundaries = find_section_boundaries(lines)
    if "pending" not in boundaries:
        raise ValueError("No pending section found.")

    start, end = boundaries["pending"]
    pending_count = 0
    remove_start = None
    remove_end = None

    for i in range(start + 1, end):
        stripped = lines[i].strip()
        if stripped.startswith("- "):
            if pending_count == target_idx:
                remove_start = i
                # Include continuation lines (indented, non-empty, non-header)
                remove_end = i + 1
                for j in range(i + 1, end):
                    next_stripped = lines[j].strip()
                    if next_stripped == "" or next_stripped.startswith("- ") or next_stripped.startswith("#"):
                        break
                    remove_end = j + 1
                break
            pending_count += 1

    if remove_start is None:
        raise ValueError("Could not locate mission in file content.")

    new_lines = lines[:remove_start] + lines[remove_end:]
    return normalize_content("\n".join(new_lines)), target_text


def clean_mission_display(text: str, max_length: int = 120) -> str:
    """Clean a mission or idea line for display.

    For multi-line ideas, only the first line is shown. Strips leading "- ",
    converts [project:X] tags to readable [X] prefix, and truncates long lines.
    """
    # For multi-line ideas, use only the first line
    if "\n" in text:
        text = text.split("\n")[0]

    # Strip leading "- "
    if text.startswith("- "):
        text = text[2:]

    # Strip project tag but keep project name as prefix
    tag_match = re.search(r'\[projec?t:([a-zA-Z0-9_-]+)\]\s*', text)
    if tag_match:
        project = tag_match.group(1)
        text = re.sub(r'\[projec?t:[a-zA-Z0-9_-]+\]\s*', '', text)
        text = f"[{project}] {text}"

    # Truncate for readability
    if len(text) > max_length:
        text = text[:max_length - 3] + "..."

    return text


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


def reorder_mission(content: str, position: int, target: int = 1) -> Tuple[str, str]:
    """Move a pending mission from one position to another.

    Args:
        content: Full missions.md content.
        position: 1-indexed position of the mission to move (in pending list).
        target: 1-indexed target position (default 1 = top of queue).

    Returns:
        (new_content, moved_display_text) tuple.

    Raises:
        ValueError: If position or target is invalid, or no pending missions.
    """
    lines = content.splitlines()
    boundaries = find_section_boundaries(lines)

    if "pending" not in boundaries:
        raise ValueError("No pending section found.")

    start, end = boundaries["pending"]

    # Collect pending items as (start_line_idx, end_line_idx) tuples
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
            items.append((item_start, i))
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
    moved_start, moved_end = items[position - 1]
    moved_lines = lines[moved_start:moved_end]
    moved_text = "\n".join(moved_lines)

    # Remove the moved item's lines
    new_lines = lines[:moved_start] + lines[moved_end:]

    # Recalculate item positions after removal
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
    result_lines = new_lines[:insert_idx] + moved_lines + new_lines[insert_idx:]

    display = clean_mission_display(moved_text)
    return normalize_content("\n".join(result_lines)), display
