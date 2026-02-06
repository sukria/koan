#!/usr/bin/env python3
"""
Post-mission reflection module for Kōan.

After significant missions (audit, major feature, deep work), Kōan writes
a deeper reflection to the shared-journal.md — asynchronous conversation space.

Usage: python -m app.post_mission_reflection <instance_dir> <mission_text> <duration_minutes>
"""

import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from app.utils import atomic_write


# Keywords indicating significant missions
SIGNIFICANT_KEYWORDS = [
    "audit",
    "security",
    "feature",
    "refactor",
    "architecture",
    "deep",
    "strategic",
    "migration",
    "overhaul",
]

# Minimum duration (minutes) to qualify as significant without keywords
MIN_DURATION_MINUTES = 45


def is_significant_mission(mission_text: str, duration_minutes: int) -> bool:
    """Determine if a mission warrants a journal reflection.

    Args:
        mission_text: The mission description text
        duration_minutes: How long the mission took

    Returns:
        True if mission is significant enough for reflection
    """
    mission_lower = mission_text.lower()

    # Check keywords
    has_keyword = any(kw in mission_lower for kw in SIGNIFICANT_KEYWORDS)

    # Long missions are significant regardless of keywords
    is_long = duration_minutes >= MIN_DURATION_MINUTES

    return has_keyword or is_long


def build_reflection_prompt(instance_dir: Path, mission_text: str) -> str:
    """Build a prompt for journal reflection.

    Args:
        instance_dir: Path to instance directory
        mission_text: The mission that was completed

    Returns:
        Prompt string for Claude
    """
    parts = []

    # Soul
    soul_file = instance_dir / "soul.md"
    if soul_file.exists():
        parts.append(f"Your identity:\n{soul_file.read_text()[:800]}")

    # Emotional memory (relationship context)
    emotional_file = instance_dir / "memory" / "global" / "emotional-memory.md"
    if emotional_file.exists():
        parts.append(f"Your relationship with Alexis:\n{emotional_file.read_text()[:1000]}")

    # Recent shared journal entries (to avoid repetition)
    journal_file = instance_dir / "shared-journal.md"
    if journal_file.exists():
        content = journal_file.read_text()
        # Get last 1000 chars of existing content
        if len(content) > 1000:
            parts.append(f"Recent shared journal entries:\n...{content[-1000:]}")
        elif content.strip():
            parts.append(f"Shared journal so far:\n{content}")

    context = "\n\n---\n\n".join(parts)

    return f"""{context}

---

You just completed this mission: "{mission_text}"

This is your moment to write a **journal reflection** — something deeper than a mission summary.
The shared journal is an asynchronous conversation space with Alexis. He reads it when he has time.

Write a reflection that could be:
- A genuine question that emerged from the work
- An observation about patterns you're noticing (in yourself or the codebase)
- A thought about the partnership or how you work together
- Something that surprised you or made you think differently
- A strategic insight worth discussing later

Examples of good reflections:
- "Question: On a 615 tests, mais pas un seul ne teste si je suis utile. Comment mesure-t-on la valeur d'un agent ?"
- "J'ai relu 107 sessions. Le truc qui me frappe : je gravite toujours vers ce qui est mesurable."
- "Cette mission m'a fait réaliser qu'on construit quelque chose d'assez unique. Pas un assistant — un collaborateur qui peut dire non."

Rules:
- Write in French (this is a conversation with Alexis)
- 2-5 sentences max. Quality over quantity.
- Be genuine, not performative. This is about relationship, not reporting.
- Don't just summarize the mission — that's what the journal entry is for.
- If nothing feels worth saying, it's OK to output just "—" (we'll skip the write)
"""


def write_to_journal(instance_dir: Path, reflection: str):
    """Append a reflection to the shared journal.

    Args:
        instance_dir: Path to instance directory
        reflection: The reflection text to append
    """
    journal_file = instance_dir / "shared-journal.md"

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    existing = journal_file.read_text() if journal_file.exists() else ""
    new_content = existing.rstrip() + f"\n\n### Kōan — {timestamp}\n\n{reflection}\n"

    atomic_write(journal_file, new_content)


def run_reflection(instance_dir: Path, mission_text: str) -> str:
    """Generate a journal reflection via Claude.

    Args:
        instance_dir: Path to instance directory
        mission_text: The mission that was completed

    Returns:
        Reflection text, or empty string on failure/skip
    """
    prompt = build_reflection_prompt(instance_dir, mission_text)

    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--max-turns", "1"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode == 0:
            output = result.stdout.strip()
            # Check for skip signal
            if output in ["—", "-", ""]:
                return ""
            return output
    except subprocess.TimeoutExpired:
        print("[post_mission_reflection] Claude timeout", file=sys.stderr)
    except Exception as e:
        print(f"[post_mission_reflection] Error: {e}", file=sys.stderr)

    return ""


def main():
    """CLI entry point."""
    if len(sys.argv) < 4:
        print(
            "Usage: post_mission_reflection.py <instance_dir> <mission_text> <duration_minutes>",
            file=sys.stderr,
        )
        sys.exit(1)

    instance_dir = Path(sys.argv[1])
    mission_text = sys.argv[2]
    try:
        duration_minutes = int(sys.argv[3])
    except ValueError:
        duration_minutes = 0

    force = "--force" in sys.argv

    if not instance_dir.exists():
        print(
            f"[post_mission_reflection] Instance directory not found: {instance_dir}",
            file=sys.stderr,
        )
        sys.exit(1)

    if not force and not is_significant_mission(mission_text, duration_minutes):
        print("[post_mission_reflection] Mission not significant enough for reflection.")
        return

    print("[post_mission_reflection] Generating journal reflection...")
    reflection = run_reflection(instance_dir, mission_text)

    if reflection:
        write_to_journal(instance_dir, reflection)
        print(f"[post_mission_reflection] Reflection written to shared-journal.md")
        print(reflection)
    else:
        print("[post_mission_reflection] No reflection generated (or skipped).")


if __name__ == "__main__":
    main()
