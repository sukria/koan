#!/usr/bin/env python3
"""
Post-mission reflection for Kōan.

After significant missions, Kōan writes a genuine reflection to shared-journal.md.
This creates an asynchronous conversation layer between Kōan and the human —
deeper and slower than Telegram.

Usage: python -m app.post_mission_reflection <instance_dir> <project_name> <journal_file> [--mission-title "..."]
"""

import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from app.prompts import load_prompt
from app.utils import atomic_write


# Keywords that signal a "significant" mission worth reflecting on
_SIGNIFICANT_KEYWORDS = re.compile(
    r"\b(audit|feature|refactor|security|architect|redesign|introspect|"
    r"migration|breaking|deploy|performance|retrospective)\b",
    re.IGNORECASE,
)

# Minimum journal content length to consider the mission substantial
_MIN_JOURNAL_LENGTH = 500


def should_write_reflection(
    mission_title: str,
    journal_content: str,
) -> bool:
    """Decide whether a completed mission warrants a reflection entry.

    Heuristics:
    - Mission title contains significant keywords (audit, feature, refactor, etc.)
    - Journal content is substantial (>500 chars = real work happened)

    Both conditions must be met: significant topic + substantial work.

    Args:
        mission_title: The mission title/description
        journal_content: The content of the journal entry for this mission

    Returns:
        True if reflection is warranted
    """
    if not mission_title or not journal_content:
        return False

    has_keyword = bool(_SIGNIFICANT_KEYWORDS.search(mission_title))
    is_substantial = len(journal_content.strip()) >= _MIN_JOURNAL_LENGTH

    return has_keyword and is_substantial


def build_reflection_prompt(
    instance_dir: Path,
    project_name: str,
    journal_content: str,
    mission_title: str = "",
) -> str:
    """Build the prompt for generating a post-mission reflection.

    Args:
        instance_dir: Path to instance directory
        project_name: Project name
        journal_content: Today's journal content for context
        mission_title: Optional mission title

    Returns:
        Formatted prompt string
    """
    # Load the template
    prompt = load_prompt(
        "journal-reflection",
        INSTANCE=str(instance_dir),
        PROJECT_NAME=project_name,
        MISSION_TITLE=mission_title or "(autonomous work)",
    )

    # Inject journal content as context
    # Truncate to keep prompt reasonable
    truncated = journal_content[:3000] if len(journal_content) > 3000 else journal_content
    prompt += f"\n\n# Journal content from this mission\n\n{truncated}\n"

    # Inject recent shared-journal entries for continuity
    shared_journal = instance_dir / "shared-journal.md"
    if shared_journal.exists():
        sj_content = shared_journal.read_text().strip()
        if sj_content:
            # Last 2000 chars to keep context manageable
            recent = sj_content[-2000:] if len(sj_content) > 2000 else sj_content
            prompt += f"\n\n# Recent shared-journal entries (for continuity)\n\n{recent}\n"

    return prompt


def run_reflection(prompt: str) -> str:
    """Run the reflection prompt via Claude and return the output.

    Args:
        prompt: The reflection prompt

    Returns:
        Reflection text, or empty string on failure
    """
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--max-turns", "1"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except subprocess.TimeoutExpired:
        print("[reflection] Claude timeout", file=sys.stderr)
    except Exception as e:
        print(f"[reflection] Error: {e}", file=sys.stderr)

    return ""


def save_reflection(instance_dir: Path, reflection: str):
    """Append a Kōan reflection entry to shared-journal.md.

    Args:
        instance_dir: Path to instance directory
        reflection: The reflection text to save
    """
    shared_journal = instance_dir / "shared-journal.md"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    existing = shared_journal.read_text() if shared_journal.exists() else ""
    entry = f"\n\n## Kōan — {timestamp}\n\n{reflection}\n"
    atomic_write(shared_journal, existing + entry)


def main():
    """CLI entry point.

    Usage: python -m app.post_mission_reflection <instance_dir> <project_name> <journal_file> [--mission-title "..."]
    """
    if len(sys.argv) < 4:
        print(
            "Usage: post_mission_reflection.py <instance_dir> <project_name> <journal_file> [--mission-title ...]",
            file=sys.stderr,
        )
        sys.exit(1)

    instance_dir = Path(sys.argv[1])
    project_name = sys.argv[2]
    journal_file = Path(sys.argv[3])

    # Parse optional --mission-title
    mission_title = ""
    if "--mission-title" in sys.argv:
        idx = sys.argv.index("--mission-title")
        if idx + 1 < len(sys.argv):
            mission_title = sys.argv[idx + 1]

    if not instance_dir.exists():
        print(f"[reflection] Instance directory not found: {instance_dir}", file=sys.stderr)
        sys.exit(1)

    if not journal_file.exists():
        print("[reflection] No journal file found, skipping reflection.", file=sys.stderr)
        return

    journal_content = journal_file.read_text()

    if not should_write_reflection(mission_title, journal_content):
        print("[reflection] Mission not significant enough for reflection.")
        return

    print("[reflection] Mission is significant — generating reflection...")
    prompt = build_reflection_prompt(instance_dir, project_name, journal_content, mission_title)
    reflection = run_reflection(prompt)

    if reflection:
        save_reflection(instance_dir, reflection)
        print("[reflection] Reflection saved to shared-journal.md")
    else:
        print("[reflection] No reflection generated.")


if __name__ == "__main__":
    main()
