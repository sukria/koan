#!/usr/bin/env python3
"""
Kōan — Crash recovery

Detects missions left in "In Progress" / "En cours" from a previous interrupted run.
Moves simple mission items (- lines) back to "Pending" / "En attente".
Skips complex multi-step missions (### headers with sub-items) — those are long-running
and should not be auto-recovered.

Usage from shell:
    python3 recover.py /path/to/instance

Returns via stdout:
    Number of recovered missions (0 if none).
    Missions file is updated in-place if recovery happens.
"""

import fcntl
import re
import sys
from pathlib import Path

from app.notify import format_and_send


def check_pending_journal(instance_dir: str) -> bool:
    """Check if a pending.md exists from an interrupted run. Returns True if found.

    We do NOT delete it — the next Claude session reads it for recovery context.
    We just log its presence so the human knows recovery will happen.
    """
    pending_path = Path(instance_dir) / "journal" / "pending.md"
    if pending_path.exists():
        content = pending_path.read_text().strip()
        lines = content.splitlines()
        # Count progress lines (after the --- separator)
        separator_seen = False
        progress_lines = 0
        for line in lines:
            if line.strip() == "---":
                separator_seen = True
                continue
            if separator_seen and line.strip():
                progress_lines += 1
        print(f"[recover] Found pending.md with {progress_lines} progress entries — next run will resume")
        return True
    return False


def recover_missions(instance_dir: str) -> int:
    """Move stale in-progress simple missions back to pending. Returns count."""
    missions_path = Path(instance_dir) / "missions.md"
    if not missions_path.exists():
        return 0

    from app.missions import find_section_boundaries

    # Read with shared lock to get consistent snapshot
    with open(missions_path, "r") as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        content = f.read()
        fcntl.flock(f, fcntl.LOCK_UN)
    lines = content.splitlines()

    # Find section boundaries
    boundaries = find_section_boundaries(lines)
    if "pending" not in boundaries or "in_progress" not in boundaries:
        return 0

    pending_start = boundaries["pending"][0]
    in_progress_start, in_progress_end = boundaries["in_progress"]

    # Extract simple mission items from in-progress section
    # Simple = starts with "- " and is NOT a strikethrough-only line (already done)
    # Skip ### headers and their sub-items (complex long-running missions)
    recovered = []
    remaining_in_progress = []
    in_complex_mission = False

    for i in range(in_progress_start + 1, in_progress_end):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("### "):
            # Complex mission header — keep it, skip its sub-items
            in_complex_mission = True
            remaining_in_progress.append(line)
            continue

        if in_complex_mission:
            # Sub-item of a complex mission — keep it
            if stripped.startswith("- ") or stripped.startswith("  ") or stripped == "":
                remaining_in_progress.append(line)
                if stripped == "":
                    in_complex_mission = False
                continue
            else:
                in_complex_mission = False

        if stripped.startswith("- ") and not re.match(r"^- ~~.*~~\s*$", stripped):
            # Simple mission item, not fully struck through — recover it
            recovered.append(line)
        elif stripped == "" or stripped == "(aucune)" or stripped == "(none)":
            remaining_in_progress.append(line)
        else:
            remaining_in_progress.append(line)

    if not recovered:
        return 0

    # Rebuild the file
    # Find where to insert recovered missions (right after pending header)
    pending_insert = pending_start + 1
    # Skip blank lines and "(aucune)" after pending header
    while pending_insert < len(lines):
        s = lines[pending_insert].strip()
        if s == "" or s == "(aucune)" or s == "(none)":
            pending_insert += 1
        else:
            break

    # Rebuild file with recovered missions moved to pending
    new_lines = []
    for i, line in enumerate(lines):
        # Skip (aucune)/(none) placeholders in the pending section
        if pending_start < i < in_progress_start:
            if line.strip() in ("(aucune)", "(none)"):
                continue

        # Skip in-progress body lines (will be replaced)
        if in_progress_start < i < in_progress_end:
            continue

        # Append the current line (headers, other content)
        new_lines.append(line)

        # After the pending header: insert recovered missions
        if i == pending_start:
            new_lines.append("")
            for m in recovered:
                new_lines.append(m)

        # After the in-progress header: re-add remaining items
        if i == in_progress_start:
            for m in remaining_in_progress:
                new_lines.append(m)
            if not any(m.strip() for m in remaining_in_progress):
                new_lines.append("")

    from app.utils import atomic_write
    from app.missions import normalize_content
    atomic_write(missions_path, normalize_content("\n".join(new_lines) + "\n"))
    return len(recovered)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <instance_dir>", file=sys.stderr)
        sys.exit(1)

    instance_dir = sys.argv[1]
    has_pending = check_pending_journal(instance_dir)
    count = recover_missions(instance_dir)

    if count > 0 or has_pending:
        parts = []
        if count > 0:
            parts.append(f"{count} mission(s) moved back to Pending")
        if has_pending:
            parts.append("interrupted run detected (pending.md) — will resume")
        msg = "Restart — " + ", ".join(parts) + "."
        format_and_send(msg)
        print(f"[recover] {msg}")
    else:
        print("[recover] No stale missions found")

    print(count)
