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

import re
import sys
from pathlib import Path

from notify import send_telegram


def recover_missions(instance_dir: str) -> int:
    """Move stale in-progress simple missions back to pending. Returns count."""
    missions_path = Path(instance_dir) / "missions.md"
    if not missions_path.exists():
        return 0

    content = missions_path.read_text()
    lines = content.splitlines()

    # Find section boundaries
    pending_start = None
    in_progress_start = None
    in_progress_end = None

    for i, line in enumerate(lines):
        stripped = line.strip().lower()
        if stripped in ("## en attente", "## pending"):
            pending_start = i
        elif stripped in ("## en cours", "## in progress"):
            in_progress_start = i
        elif in_progress_start is not None and line.startswith("## "):
            in_progress_end = i
            break

    if in_progress_start is None or pending_start is None:
        return 0

    if in_progress_end is None:
        in_progress_end = len(lines)

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

    # Remove "(aucune)" from pending section if we're adding missions
    new_lines = []
    for i, line in enumerate(lines):
        if i == pending_start + 1:
            # After pending header: insert recovered missions
            new_lines.append("")
            for m in recovered:
                new_lines.append(m)
        if pending_start < i < (in_progress_start or len(lines)):
            if line.strip() in ("(aucune)", "(none)"):
                continue  # Remove placeholder
        if in_progress_start < i < in_progress_end:
            continue  # Will be replaced
        new_lines.append(line)

        if i == in_progress_start:
            # Re-add remaining in-progress items
            for m in remaining_in_progress:
                new_lines.append(m)
            # If nothing remains, add placeholder
            if not any(m.strip() for m in remaining_in_progress):
                new_lines.append("")

    missions_path.write_text("\n".join(new_lines) + "\n")
    return len(recovered)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <instance_dir>", file=sys.stderr)
        sys.exit(1)

    instance_dir = sys.argv[1]
    count = recover_missions(instance_dir)

    if count > 0:
        msg = f"🔄 Restart — {count} mission(s) recovered from interrupted run, moved back to Pending."
        send_telegram(msg)
        print(f"[recover] {count} mission(s) moved back to Pending")
    else:
        print("[recover] No stale missions found")

    print(count)
