#!/usr/bin/env python3
"""
Send session retrospective when budget is exhausted.

Extracts session summary from today's journal and appends to outbox
for delivery via Telegram. Called when usage tracker enters WAIT mode.

Usage: python send_retrospective.py <instance_dir> <project_name>
"""

import fcntl
import sys
from datetime import date
from pathlib import Path


def get_todays_journal(instance_dir: Path, project_name: str) -> Path:
    """Find today's journal file for the given project.

    Supports both nested (journal/YYYY-MM-DD/project.md) and
    flat (journal/YYYY-MM-DD.md) structures.

    Args:
        instance_dir: Path to instance directory
        project_name: Name of the project

    Returns:
        Path to journal file (may not exist)
    """
    today = date.today().strftime("%Y-%m-%d")
    journal_dir = instance_dir / "journal"

    # Try nested structure first
    nested_journal = journal_dir / today / f"{project_name}.md"
    if nested_journal.exists():
        return nested_journal

    # Try flat structure
    flat_journal = journal_dir / f"{today}.md"
    if flat_journal.exists():
        return flat_journal

    # Return nested path as default (even if doesn't exist)
    return nested_journal


def extract_session_summary(journal_path: Path, max_chars: int = 800) -> str:
    """Extract a summary of the entire session from journal.

    Args:
        journal_path: Path to today's journal file
        max_chars: Maximum characters to extract

    Returns:
        Summary text for retrospective (raw, will be formatted by Claude later)
    """
    if not journal_path.exists():
        return "No journal entries today — session was brief."

    content = journal_path.read_text()
    if not content.strip():
        return "No journal entries today — session was brief."

    # Extract last ~800 chars for context (will be formatted by Claude)
    # This gives Claude the session context without dumping the whole journal
    if len(content) > max_chars:
        # Get last section or last N chars
        sections = content.split("\n## ")
        if len(sections) > 1:
            # Take last 2-3 sections
            recent_sections = sections[-3:]
            summary = "\n## ".join(recent_sections)
            if len(summary) > max_chars:
                summary = "..." + summary[-max_chars:]
        else:
            summary = "..." + content[-max_chars:]
    else:
        summary = content

    return summary


def append_to_outbox(instance_dir: Path, message: str):
    """Append message to outbox.md with file locking.

    Args:
        instance_dir: Path to instance directory
        message: Message to append
    """
    outbox_file = instance_dir / "outbox.md"

    try:
        with open(outbox_file, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(message + "\n")
            fcntl.flock(f, fcntl.LOCK_UN)
    except Exception as e:
        print(f"[send_retrospective] Error writing to outbox: {e}", file=sys.stderr)


def create_retrospective(instance_dir: Path, project_name: str):
    """Create and send retrospective when budget is exhausted.

    Args:
        instance_dir: Path to instance directory
        project_name: Name of current project
    """
    journal_path = get_todays_journal(instance_dir, project_name)
    summary = extract_session_summary(journal_path)

    # Create retrospective message (raw format - will be formatted by format_outbox.py)
    retrospective = f"""# Session Retrospective — {date.today():%Y-%m-%d}

**Project**: {project_name}
**Status**: Budget exhausted, Kōan paused

{summary}

---
*Kōan paused due to quota limit. Use /resume command when quota resets.*
"""

    append_to_outbox(instance_dir, retrospective)
    print(f"[send_retrospective] Retrospective sent to outbox ({len(retrospective)} chars)")


def main():
    """CLI entry point."""
    if len(sys.argv) < 3:
        print("Usage: send_retrospective.py <instance_dir> <project_name>", file=sys.stderr)
        sys.exit(1)

    instance_dir = Path(sys.argv[1])
    project_name = sys.argv[2]

    if not instance_dir.exists():
        print(f"[send_retrospective] Instance directory not found: {instance_dir}", file=sys.stderr)
        sys.exit(1)

    create_retrospective(instance_dir, project_name)


if __name__ == "__main__":
    main()
