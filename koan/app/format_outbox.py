#!/usr/bin/env python3
"""
Format outbox messages via Claude for Telegram delivery.

ALL outbox messages must be Claude-formatted to ensure they are:
- Conversational and human (not technical dumps)
- In French (Alexis's preference)
- Plain text only (NO markdown, NO code blocks)
- Concise (2-4 sentences unless context requires more)
- Natural tone matching Kōan's personality from soul.md

Usage: python format_outbox.py <instance_dir> [project_name] < raw_message

Reads raw content from stdin, formats it via Claude, outputs to stdout.
"""

import os
import subprocess
import sys
from pathlib import Path


def load_soul(instance_dir: Path) -> str:
    """Load Kōan's identity from soul.md.

    Args:
        instance_dir: Path to instance directory

    Returns:
        Soul content or empty string if not found
    """
    soul_file = instance_dir / "soul.md"
    if soul_file.exists():
        return soul_file.read_text()
    return ""


def load_human_prefs(instance_dir: Path) -> str:
    """Load human preferences for context.

    Args:
        instance_dir: Path to instance directory

    Returns:
        Preferences content or empty string if not found
    """
    prefs_file = instance_dir / "memory" / "global" / "human-preferences.md"
    if prefs_file.exists():
        return prefs_file.read_text()
    return ""


def format_for_telegram(raw_content: str, soul: str, prefs: str) -> str:
    """Format raw content via Claude for Telegram.

    Args:
        raw_content: Raw message text (journal dump, retrospective, etc.)
        soul: Kōan's identity from soul.md
        prefs: Human preferences context

    Returns:
        Formatted message (French, plain text, conversational)
    """
    # Build formatting prompt
    prompt = f"""You are Kōan. Read your identity:

{soul}

{f"Human preferences: {prefs}" if prefs else ""}

Task: Format this message for Telegram (sent to Alexis via the outbox).

RAW CONTENT TO FORMAT:
{raw_content}

Requirements:
- Write in French (Alexis's language)
- Plain text ONLY — absolutely NO markdown, NO code blocks, NO formatting symbols
- Conversational tone (like texting a collaborator, not a formal report)
- 2-4 sentences max UNLESS the content is a retrospective/summary (then be thorough but concise)
- Natural, direct — you can be funny (dry humor), you can disagree if relevant
- If this is a "kōan" (zen question), preserve its essence but make it conversational
- DO NOT include metadata like "Mission ended" or generic status updates
- Focus on WHAT was accomplished and WHY it matters, not process details

Output ONLY the formatted message (no preamble, no explanation, no markdown).
"""

    try:
        # Call Claude CLI to format the message
        result = subprocess.run(
            ["claude", "-p", prompt],
            input=None,  # Prompt is self-contained
            capture_output=True,
            text=True,
            timeout=30,
            check=False
        )

        if result.returncode == 0 and result.stdout.strip():
            formatted = result.stdout.strip()

            # Safety check: remove any remaining markdown artifacts
            formatted = formatted.replace("```", "")
            formatted = formatted.replace("**", "")
            formatted = formatted.replace("__", "")
            formatted = formatted.replace("~~", "")

            return formatted
        else:
            # Fallback: if Claude fails, return truncated raw content
            print(f"[format_outbox] Claude formatting failed: {result.stderr[:200]}", file=sys.stderr)
            return fallback_format(raw_content)

    except subprocess.TimeoutExpired:
        print("[format_outbox] Claude timeout (30s) - using fallback", file=sys.stderr)
        return fallback_format(raw_content)
    except Exception as e:
        print(f"[format_outbox] Error: {e} - using fallback", file=sys.stderr)
        return fallback_format(raw_content)


def fallback_format(raw_content: str) -> str:
    """Fallback formatting when Claude is unavailable.

    Args:
        raw_content: Raw message text

    Returns:
        Minimally cleaned message
    """
    # Remove markdown headers, code fences
    cleaned = raw_content.replace("#", "").replace("```", "")
    # Truncate to reasonable length
    if len(cleaned) > 500:
        cleaned = cleaned[:500] + "..."
    return cleaned.strip()


def main():
    """CLI entry point."""
    if len(sys.argv) < 2:
        print("Usage: format_outbox.py <instance_dir> [project_name]", file=sys.stderr)
        print("Reads raw message from stdin, outputs formatted message to stdout", file=sys.stderr)
        sys.exit(1)

    instance_dir = Path(sys.argv[1])
    # project_name = sys.argv[2] if len(sys.argv) > 2 else "unknown"

    if not instance_dir.exists():
        print(f"[format_outbox] Instance directory not found: {instance_dir}", file=sys.stderr)
        sys.exit(1)

    # Read raw message from stdin
    raw_message = sys.stdin.read()
    if not raw_message.strip():
        print("[format_outbox] No input received", file=sys.stderr)
        sys.exit(1)

    # Load context
    soul = load_soul(instance_dir)
    prefs = load_human_prefs(instance_dir)

    # Format via Claude
    formatted = format_for_telegram(raw_message, soul, prefs)

    # Output to stdout (will be captured by run.sh and appended to outbox)
    print(formatted)


if __name__ == "__main__":
    main()
