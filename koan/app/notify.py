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
import sys

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


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <message>", file=sys.stderr)
        sys.exit(1)
    message = " ".join(sys.argv[1:])
    success = send_telegram(message)
    sys.exit(0 if success else 1)
