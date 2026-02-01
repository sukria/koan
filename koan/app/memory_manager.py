#!/usr/bin/env python3
"""
Koan — Memory manager

Handles memory scope isolation and periodic cleanup:
- Scoped summary: filter summary.md to only show relevant project sessions
- Summary compaction: keep last N sessions per project, archive older ones
- Learnings dedup: remove duplicate lines from learnings files

Usage from shell:
    python3 memory_manager.py <instance_dir> <command> [args...]

Commands:
    scoped-summary <project_name>   Print summary.md filtered to project-relevant sessions
    compact <max_sessions>          Compact summary.md, keeping last N sessions per date
    cleanup-learnings <project>     Remove duplicate lines from learnings.md
"""

import re
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Tuple


def parse_summary_sessions(content: str) -> List[Tuple[str, str, str]]:
    """Parse summary.md into (date_header, session_text, project_hint) tuples.

    Each entry is a paragraph under a ## date header. The project_hint is
    extracted from "(projet: X)" or "(project: X)" markers, or empty if none.
    """
    sessions = []
    current_date = ""
    current_lines: List[str] = []

    for line in content.splitlines():
        if line.startswith("## "):
            # Flush previous
            if current_lines and current_date:
                _flush_sessions(current_date, current_lines, sessions)
            current_date = line
            current_lines = []
        else:
            current_lines.append(line)

    # Flush last
    if current_lines and current_date:
        _flush_sessions(current_date, current_lines, sessions)

    return sessions


def _flush_sessions(date_header: str, lines: List[str], sessions: list):
    """Split lines into individual session paragraphs and append to sessions."""
    # Sessions are separated by blank lines within a date section
    # Each "Session N" paragraph is one session
    current_paragraph: List[str] = []

    for line in lines:
        if line.strip() == "" and current_paragraph:
            text = "\n".join(current_paragraph)
            project = _extract_project_hint(text)
            sessions.append((date_header, text, project))
            current_paragraph = []
        elif line.strip():
            current_paragraph.append(line)

    if current_paragraph:
        text = "\n".join(current_paragraph)
        project = _extract_project_hint(text)
        sessions.append((date_header, text, project))


def _extract_project_hint(text: str) -> str:
    """Extract project name from session text like '(projet: koan)' or 'projet:koan'."""
    # Match patterns: (projet: X), (project: X), projet:X, project:X
    m = re.search(r"\(?\s*projet?\s*:\s*([a-zA-Z0-9_-]+)\s*\)?", text, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    return ""


def scoped_summary(instance_dir: str, project_name: str) -> str:
    """Return summary.md content filtered to sessions relevant to a project.

    A session is relevant if:
    - It explicitly mentions the project (projet: X)
    - It has no project hint (pre-multi-project sessions, kept for all)
    """
    summary_path = Path(instance_dir) / "memory" / "summary.md"
    if not summary_path.exists():
        return ""

    content = summary_path.read_text()
    sessions = parse_summary_sessions(content)

    # Extract the title (# header) if present
    title = ""
    for line in content.splitlines():
        if line.startswith("# ") and not line.startswith("## "):
            title = line
            break

    # Filter sessions
    filtered = []
    project_lower = project_name.lower()
    for date_header, text, project_hint in sessions:
        if not project_hint or project_hint == project_lower:
            filtered.append((date_header, text))

    # Rebuild output, grouping by date
    output_lines = []
    if title:
        output_lines.append(title)
        output_lines.append("")

    current_date = ""
    for date_header, text in filtered:
        if date_header != current_date:
            if current_date:
                output_lines.append("")
            output_lines.append(date_header)
            output_lines.append("")
            current_date = date_header
        output_lines.append(text)
        output_lines.append("")

    return "\n".join(output_lines).rstrip() + "\n"


def compact_summary(instance_dir: str, max_sessions: int = 10) -> int:
    """Keep only the last N sessions per project in summary.md. Returns removed count."""
    summary_path = Path(instance_dir) / "memory" / "summary.md"
    if not summary_path.exists():
        return 0

    content = summary_path.read_text()
    sessions = parse_summary_sessions(content)

    if len(sessions) <= max_sessions:
        return 0

    # Extract title
    title = ""
    for line in content.splitlines():
        if line.startswith("# ") and not line.startswith("## "):
            title = line
            break

    # Keep last max_sessions sessions (they're in chronological order)
    kept = sessions[-max_sessions:]
    removed = len(sessions) - len(kept)

    # Rebuild
    output_lines = []
    if title:
        output_lines.append(title)
        output_lines.append("")

    current_date = ""
    for date_header, text, _ in kept:
        if date_header != current_date:
            if current_date:
                output_lines.append("")
            output_lines.append(date_header)
            output_lines.append("")
            current_date = date_header
        output_lines.append(text)
        output_lines.append("")

    summary_path.write_text("\n".join(output_lines).rstrip() + "\n")
    return removed


def cleanup_learnings(instance_dir: str, project_name: str) -> int:
    """Remove duplicate lines from a project's learnings.md. Returns removed count."""
    learnings_path = (
        Path(instance_dir) / "memory" / "projects" / project_name / "learnings.md"
    )
    if not learnings_path.exists():
        return 0

    content = learnings_path.read_text()
    lines = content.splitlines()

    seen = set()
    new_lines = []
    removed = 0

    for line in lines:
        stripped = line.strip()
        # Headers and blank lines are always kept
        if stripped.startswith("#") or stripped == "":
            new_lines.append(line)
            continue

        if stripped in seen:
            removed += 1
        else:
            seen.add(stripped)
            new_lines.append(line)

    if removed > 0:
        learnings_path.write_text("\n".join(new_lines) + "\n")

    return removed


def run_cleanup(instance_dir: str, max_sessions: int = 15) -> dict:
    """Run all cleanup tasks. Returns stats dict."""
    stats = {}
    stats["summary_compacted"] = compact_summary(instance_dir, max_sessions)

    # Cleanup learnings for all projects
    projects_dir = Path(instance_dir) / "memory" / "projects"
    if projects_dir.exists():
        for project_dir in projects_dir.iterdir():
            if project_dir.is_dir():
                name = project_dir.name
                removed = cleanup_learnings(instance_dir, name)
                if removed > 0:
                    stats[f"learnings_dedup_{name}"] = removed

    return stats


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(
            f"Usage: {sys.argv[0]} <instance_dir> <command> [args...]",
            file=sys.stderr,
        )
        print("Commands: scoped-summary <project>, compact [max], cleanup-learnings <project>", file=sys.stderr)
        sys.exit(1)

    instance = sys.argv[1]
    command = sys.argv[2]

    if command == "scoped-summary":
        if len(sys.argv) < 4:
            print("Error: project name required", file=sys.stderr)
            sys.exit(1)
        print(scoped_summary(instance, sys.argv[3]))

    elif command == "compact":
        max_s = int(sys.argv[3]) if len(sys.argv) > 3 else 15
        removed = compact_summary(instance, max_s)
        print(f"Compacted: {removed} sessions removed")

    elif command == "cleanup-learnings":
        if len(sys.argv) < 4:
            print("Error: project name required", file=sys.stderr)
            sys.exit(1)
        removed = cleanup_learnings(instance, sys.argv[3])
        print(f"Deduped: {removed} lines removed")

    elif command == "cleanup":
        max_s = int(sys.argv[3]) if len(sys.argv) > 3 else 15
        stats = run_cleanup(instance, max_s)
        for k, v in stats.items():
            print(f"  {k}: {v}")

    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)
