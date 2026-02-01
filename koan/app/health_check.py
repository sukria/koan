#!/usr/bin/env python3
"""
Kōan — Health check

Monitors the Telegram bridge (awake.py) heartbeat.
awake.py writes a timestamp to .koan-heartbeat every poll cycle.
This module checks staleness and alerts via notify.py if the bridge is down.

Usage from shell:
    python3 health_check.py /path/to/koan_root [--max-age 60]

Exit codes:
    0 = healthy (or no heartbeat file yet — first run)
    1 = stale heartbeat (bridge likely down)
    2 = usage error
"""

import sys
import time
from pathlib import Path

from app.notify import send_telegram


HEARTBEAT_FILENAME = ".koan-heartbeat"
DEFAULT_MAX_AGE = 60  # seconds


def write_heartbeat(koan_root: str) -> None:
    """Write current timestamp to heartbeat file. Called by awake.py."""
    path = Path(koan_root) / HEARTBEAT_FILENAME
    path.write_text(str(time.time()))


def check_heartbeat(koan_root: str, max_age: int = DEFAULT_MAX_AGE) -> bool:
    """Check if the heartbeat is fresh.

    Returns True if healthy (fresh or no file yet), False if stale.
    """
    path = Path(koan_root) / HEARTBEAT_FILENAME
    if not path.exists():
        # No heartbeat file = bridge never started or first run. Not an error.
        return True

    try:
        ts = float(path.read_text().strip())
    except (ValueError, OSError):
        return False

    age = time.time() - ts
    return age <= max_age


def check_and_alert(koan_root: str, max_age: int = DEFAULT_MAX_AGE) -> bool:
    """Check heartbeat and send alert if stale. Returns True if healthy."""
    if check_heartbeat(koan_root, max_age):
        return True

    path = Path(koan_root) / HEARTBEAT_FILENAME
    try:
        ts = float(path.read_text().strip())
        age_minutes = (time.time() - ts) / 60
        send_telegram(
            f"Telegram bridge (awake.py) appears down — "
            f"last heartbeat {age_minutes:.0f} min ago."
        )
    except (ValueError, OSError):
        send_telegram("Telegram bridge (awake.py) appears down — heartbeat file unreadable.")

    return False


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <koan_root> [--max-age SECONDS]", file=sys.stderr)
        sys.exit(2)

    root = sys.argv[1]
    max_age = DEFAULT_MAX_AGE

    if "--max-age" in sys.argv:
        idx = sys.argv.index("--max-age")
        if idx + 1 < len(sys.argv):
            try:
                max_age = int(sys.argv[idx + 1])
            except ValueError:
                print(f"Invalid max-age value: {sys.argv[idx + 1]}", file=sys.stderr)
                sys.exit(2)

    healthy = check_and_alert(root, max_age)
    status = "healthy" if healthy else "STALE"
    print(f"[health] Bridge: {status}")
    sys.exit(0 if healthy else 1)
