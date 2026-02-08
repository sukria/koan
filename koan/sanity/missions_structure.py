"""
Kōan — Missions file sanity checker.

Detects and repairs structural issues in missions.md that accumulate
over time: leaked exploration context, duplicate section headers,
orphaned content blocks.

Designed to run periodically (e.g. at startup) to keep the file clean.
"""

import os
from typing import List, Tuple

from app.missions import _SECTION_MAP, classify_section, normalize_content


# Sections that are legitimate in missions.md
_KNOWN_SECTIONS = {"pending", "in_progress", "done"}

# Additional ## headers that are valid (not in _SECTION_MAP but intentional)
_EXTRA_VALID_HEADERS = {"ideas"}


def find_issues(content: str) -> List[str]:
    """Scan missions.md content for structural issues.

    Returns a list of human-readable issue descriptions.
    Does NOT modify the content — use sanitize() for that.
    """
    issues = []
    lines = content.splitlines()

    seen_sections = {}  # canonical_key -> first line number
    foreign_sections = []  # (line_number, header_text) for unrecognized ## headers

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("## "):
            continue

        header_text = stripped[3:].strip()
        header_lower = header_text.lower()

        # Check if it's a known mission section
        canonical = classify_section(header_text)
        if canonical:
            if canonical in seen_sections:
                issues.append(
                    f"Duplicate section '## {header_text}' at line {i + 1} "
                    f"(first seen at line {seen_sections[canonical] + 1})"
                )
            else:
                seen_sections[canonical] = i
            continue

        # Check if it's a valid extra section (like Ideas)
        if header_lower in _EXTRA_VALID_HEADERS:
            if header_lower in seen_sections:
                issues.append(
                    f"Duplicate section '## {header_text}' at line {i + 1} "
                    f"(first seen at line {seen_sections[header_lower] + 1})"
                )
            else:
                seen_sections[header_lower] = i
            continue

        # Foreign section — doesn't belong in missions.md
        foreign_sections.append((i, header_text))
        issues.append(
            f"Foreign section '## {header_text}' at line {i + 1} "
            f"(not a valid missions.md section)"
        )

    return issues


def sanitize(content: str) -> Tuple[str, List[str]]:
    """Clean up missions.md content by removing structural issues.

    Returns (cleaned_content, list_of_changes_made).

    Repairs:
    1. Removes foreign ## sections and their content (e.g. "## Recent activity")
    2. Merges duplicate mission sections (keeps first, appends items from dupes)
    3. Normalizes whitespace via normalize_content()
    """
    changes = []
    lines = content.splitlines()

    # Pass 1: identify all ## headers and their spans
    sections = []  # (line_idx, header_text, canonical_key_or_none, is_extra_valid)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("## "):
            header_text = stripped[3:].strip()
            canonical = classify_section(header_text)
            is_extra = header_text.lower() in _EXTRA_VALID_HEADERS
            sections.append((i, header_text, canonical, is_extra))

    if not sections:
        return normalize_content(content), changes

    # Compute spans: each section runs from its header to the next ## header (or EOF)
    spans = []
    for idx, (line_idx, header_text, canonical, is_extra) in enumerate(sections):
        if idx + 1 < len(sections):
            end_idx = sections[idx + 1][0]
        else:
            end_idx = len(lines)
        spans.append((line_idx, end_idx, header_text, canonical, is_extra))

    # Pass 2: decide what to keep, what to remove, what to merge
    keep_lines = []
    seen_canonical = {}  # canonical -> index in spans where first seen
    merge_items = {}  # canonical -> list of content lines from duplicate sections

    # Keep everything before the first ## header (# Missions title, etc.)
    first_section_line = spans[0][0]
    keep_lines.extend(lines[:first_section_line])

    for span_idx, (start, end, header_text, canonical, is_extra) in enumerate(spans):
        if canonical:
            # Valid mission section
            if canonical not in seen_canonical:
                # First occurrence — keep it
                seen_canonical[canonical] = span_idx
                keep_lines.extend(lines[start:end])
            else:
                # Duplicate — extract mission items and merge into first
                items = _extract_items(lines[start + 1:end])
                if items:
                    if canonical not in merge_items:
                        merge_items[canonical] = []
                    merge_items[canonical].extend(items)
                changes.append(
                    f"Merged duplicate '## {header_text}' "
                    f"({len(items)} item(s)) into first occurrence"
                )
        elif is_extra:
            # Valid extra section (Ideas)
            header_lower = header_text.lower()
            if header_lower not in seen_canonical:
                seen_canonical[header_lower] = span_idx
                keep_lines.extend(lines[start:end])
            else:
                items = _extract_items(lines[start + 1:end])
                if items:
                    if header_lower not in merge_items:
                        merge_items[header_lower] = []
                    merge_items[header_lower].extend(items)
                changes.append(
                    f"Merged duplicate '## {header_text}' "
                    f"({len(items)} item(s)) into first occurrence"
                )
        else:
            # Foreign section — remove entirely
            removed_lines = end - start
            changes.append(
                f"Removed foreign section '## {header_text}' "
                f"({removed_lines} line(s))"
            )

    # Pass 3: inject merged items into the kept sections
    if merge_items:
        result = "\n".join(keep_lines)
        for canonical, items in merge_items.items():
            result = _inject_items(result, canonical, items)
        keep_lines = result.splitlines()

    cleaned = normalize_content("\n".join(keep_lines))
    return cleaned, changes


def _extract_items(section_lines: List[str]) -> List[str]:
    """Extract mission items (- ...) from a section's body lines.

    Returns complete items including continuation lines.
    """
    items = []
    current_item = None

    for line in section_lines:
        stripped = line.strip()
        if stripped.startswith("- "):
            if current_item is not None:
                items.append("\n".join(current_item))
            current_item = [line]
        elif current_item is not None:
            if stripped and not stripped.startswith("#"):
                current_item.append(line)
            else:
                items.append("\n".join(current_item))
                current_item = None

    if current_item is not None:
        items.append("\n".join(current_item))

    return items


def _inject_items(content: str, section_key: str, items: List[str]) -> str:
    """Inject items at the end of a section identified by canonical key."""
    lines = content.splitlines()

    # Find the section
    section_start = None
    section_end = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("## "):
            header_text = stripped[3:].strip()
            canonical = classify_section(header_text)
            if canonical == section_key or header_text.lower() == section_key:
                section_start = i
                # Find end (next ## or EOF)
                for j in range(i + 1, len(lines)):
                    if lines[j].strip().startswith("## "):
                        section_end = j
                        break
                if section_end is None:
                    section_end = len(lines)
                break

    if section_start is None:
        return content

    # Find the last content line in the section
    insert_at = section_start + 1
    for i in range(section_start + 1, section_end):
        stripped = lines[i].strip()
        if stripped.startswith("- ") or (stripped and not stripped.startswith("#")):
            insert_at = i + 1

    # Insert items
    for item in reversed(items):
        lines.insert(insert_at, item)

    return "\n".join(lines)


def run_sanity_check(missions_path: str) -> Tuple[bool, List[str]]:
    """Run sanity check on a missions.md file.

    Args:
        missions_path: Path to missions.md file.

    Returns:
        (was_modified, list_of_changes)
    """
    from pathlib import Path

    path = Path(missions_path)
    if not path.exists():
        return False, []

    content = path.read_text()
    if not content.strip():
        return False, []

    cleaned, changes = sanitize(content)
    if not changes:
        return False, []

    # Write back atomically
    try:
        from app.utils import atomic_write
        atomic_write(path, cleaned)
    except ImportError:
        # Fallback: direct write
        path.write_text(cleaned)

    return True, changes


def run(instance_dir: str) -> Tuple[bool, List[str]]:
    """Sanity runner interface: check missions.md structure."""
    missions_path = os.path.join(instance_dir, "missions.md")
    return run_sanity_check(missions_path)
