#!/usr/bin/env python3
"""
Kōan — Telegram notification helper

Standalone module to send messages to Telegram from any process
(awake.py, run.sh, workers).

Usage from shell:
    python3 notify.py "Mission completed: security audit"

Usage from Python:
    from app.notify import send_telegram
    send_telegram("Mission completed: security audit")
"""

import os
import subprocess
import sys
from pathlib import Path

import requests

from app.utils import load_dotenv


def send_telegram(text: str) -> bool:
    """Send a message to the configured Telegram chat. Returns True on success."""
    load_dotenv()

    BOT_TOKEN = os.environ.get("KOAN_TELEGRAM_TOKEN", "")
    CHAT_ID = os.environ.get("KOAN_TELEGRAM_CHAT_ID", "")

    if not BOT_TOKEN or not CHAT_ID:
        print("[notify] KOAN_TELEGRAM_TOKEN or KOAN_TELEGRAM_CHAT_ID not set.", file=sys.stderr)
        return False

    TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
    ok = True
    for chunk in [text[i:i + 4000] for i in range(0, len(text), 4000)]:
        try:
            resp = requests.post(
                f"{TELEGRAM_API}/sendMessage",
                json={"chat_id": CHAT_ID, "text": chunk},
                timeout=10,
            )
            data = resp.json()
            if not data.get("ok"):
                print(f"[notify] Telegram API error: {resp.text[:200]}", file=sys.stderr)
                ok = False
        except (requests.RequestException, ValueError) as e:
            print(f"[notify] Send error: {e}", file=sys.stderr)
            ok = False
    return ok


def format_and_send(raw_message: str, instance_dir: str = None,
                     project_name: str = "") -> bool:
    """Format a message through Claude with Kōan's personality, then send to Telegram.

    Every message sent to Telegram should go through this function to ensure
    consistent personality and readability on mobile.

    Args:
        raw_message: The raw/technical message to format
        instance_dir: Path to instance directory (auto-detected from KOAN_ROOT if None)
        project_name: Optional project name for scoped memory context

    Returns:
        True if message was sent successfully
    """
    from app.format_outbox import (
        format_for_telegram, load_soul, load_human_prefs,
        load_memory_context, fallback_format
    )

    if not instance_dir:
        load_dotenv()
        koan_root = os.environ.get("KOAN_ROOT", "")
        if koan_root:
            instance_dir = str(Path(koan_root) / "instance")
        else:
            # Can't format without instance dir — send raw with basic cleanup
            return send_telegram(fallback_format(raw_message))

    instance_path = Path(instance_dir)
    try:
        soul = load_soul(instance_path)
        prefs = load_human_prefs(instance_path)
        memory = load_memory_context(instance_path, project_name)
        formatted = format_for_telegram(raw_message, soul, prefs, memory)
        return send_telegram(formatted)
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        print(f"[notify] Format error, sending fallback: {e}", file=sys.stderr)
        return send_telegram(fallback_format(raw_message))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} [--format] <message>", file=sys.stderr)
        print(f"  --format: Format through Claude before sending", file=sys.stderr)
        sys.exit(1)

    args = sys.argv[1:]
    use_format = False
    if args[0] == "--format":
        use_format = True
        args = args[1:]

    if not args:
        print(f"Usage: {sys.argv[0]} [--format] <message>", file=sys.stderr)
        sys.exit(1)

    message = " ".join(args)

    if use_format:
        project_name = os.environ.get("KOAN_CURRENT_PROJECT", "")
        success = format_and_send(message, project_name=project_name)
    else:
        success = send_telegram(message)
    sys.exit(0 if success else 1)
