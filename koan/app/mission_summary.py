#!/usr/bin/env python3
"""
Extract a short summary from today's journal for post-mission outbox notification.

Usage: python mission_summary.py <instance_dir> <project_name> [max_chars]

Reads the latest journal entry for the given project, extracts the last
## section, and prints a 2-3 line summary suitable for Telegram.
"""

import re
import sys
from datetime import date
from pathlib import Path


def extract_latest_section(journal_text: str) -> str:
    """Extract the last ## section from journal text."""
    sections = re.split(r'^## ', journal_text, flags=re.MULTILINE)
    if len(sections) < 2:
        return journal_text.strip()
    # Last section (with its heading restored)
    last = "## " + sections[-1]
    return last.strip()


def summarize_section(section: str, max_chars: int = 400) -> str:
    """Produce a short summary from a journal section.

    Strategy: take the heading + first non-empty paragraph.
    """
    lines = section.splitlines()
    if not lines:
        return ""

    # Extract heading
    heading = ""
    body_lines = []
    for line in lines:
        if line.startswith("## ") and not heading:
            heading = line[3:].strip()
        elif line.strip():
            body_lines.append(line.strip())

    # Take first meaningful lines (skip code blocks, metadata)
    summary_lines = []
    for line in body_lines:
        if line.startswith("```"):
            break
        if line.startswith("---"):
            continue
        summary_lines.append(line)
        if len("\n".join(summary_lines)) > max_chars:
            break
        if len(summary_lines) >= 4:
            break

    summary = "\n".join(summary_lines)
    if len(summary) > max_chars:
        summary = summary[:max_chars].rsplit(" ", 1)[0] + "..."

    if heading:
        return f"{heading}\n\n{summary}"
    return summary


def get_mission_summary(instance_dir: str, project_name: str, max_chars: int = 400) -> str:
    """Get a summary of the latest journal entry for a project."""
    instance = Path(instance_dir)
    today = date.today().strftime("%Y-%m-%d")

    # Try nested journal structure first
    journal_file = instance / "journal" / today / f"{project_name}.md"
    if not journal_file.exists():
        # Try flat structure
        journal_file = instance / "journal" / f"{today}.md"
    if not journal_file.exists():
        return ""

    content = journal_file.read_text().strip()
    if not content:
        return ""

    section = extract_latest_section(content)
    return summarize_section(section, max_chars)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: mission_summary.py <instance_dir> <project_name> [max_chars]", file=sys.stderr)
        sys.exit(1)

    instance_dir = sys.argv[1]
    project_name = sys.argv[2]
    max_chars = int(sys.argv[3]) if len(sys.argv) > 3 else 400

    summary = get_mission_summary(instance_dir, project_name, max_chars)
    if summary:
        print(summary)
