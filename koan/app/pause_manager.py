#!/usr/bin/env python3
"""
Koan -- Pause State Manager

Manages the .koan-pause and .koan-pause-reason files that control the
agent loop's pause/resume behavior. Replaces fragile bash logic (sed,
timestamp math, multi-line file parsing) with testable Python.

Pause state format:
  .koan-pause          — existence = paused (empty file, touched by run.sh)
  .koan-pause-reason   — 3-line file:
    line 1: reason (e.g., "quota", "max_runs")
    line 2: timestamp (UNIX epoch — reset time for quota, pause time for max_runs)
    line 3: display info (human-readable, e.g., "resets 10am (Europe/Paris)")
"""

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# Default cooldown for non-quota pauses (max_runs, manual)
DEFAULT_COOLDOWN_SECONDS = 5 * 60 * 60  # 5 hours


@dataclass
class PauseState:
    """Represents the current pause state."""

    reason: str  # "quota", "max_runs", or other
    timestamp: int  # Reset time (quota) or pause time (max_runs)
    display: str  # Human-readable info

    @property
    def is_quota(self) -> bool:
        return self.reason == "quota"


def is_paused(koan_root: str) -> bool:
    """Check if the pause file exists."""
    return os.path.isfile(os.path.join(koan_root, ".koan-pause"))


def get_pause_state(koan_root: str) -> Optional[PauseState]:
    """
    Read the current pause state from .koan-pause-reason.

    Returns None if not paused or no reason file exists.
    """
    if not is_paused(koan_root):
        return None

    reason_file = os.path.join(koan_root, ".koan-pause-reason")
    if not os.path.isfile(reason_file):
        return None

    try:
        with open(reason_file) as f:
            lines = f.read().strip().splitlines()
    except OSError:
        return None

    if not lines:
        return None

    reason = lines[0].strip()
    timestamp = 0
    display = ""

    if len(lines) >= 2:
        try:
            timestamp = int(lines[1].strip())
        except (ValueError, IndexError):
            timestamp = 0

    if len(lines) >= 3:
        display = lines[2].strip()

    return PauseState(reason=reason, timestamp=timestamp, display=display)


def should_auto_resume(state: PauseState, now: Optional[int] = None) -> bool:
    """
    Determine if auto-resume conditions are met.

    For quota pauses: resume when current time >= reset timestamp.
    For max_runs/other: resume after DEFAULT_COOLDOWN_SECONDS (5h).
    """
    if now is None:
        now = int(time.time())

    if state.is_quota:
        # Quota: resume when reset time is reached
        return state.timestamp > 0 and now >= state.timestamp
    else:
        # Non-quota: resume after 5h cooldown from pause time
        if state.timestamp <= 0:
            return False
        elapsed = now - state.timestamp
        return elapsed >= DEFAULT_COOLDOWN_SECONDS


def create_pause(
    koan_root: str,
    reason: str,
    timestamp: Optional[int] = None,
    display: str = "",
) -> None:
    """
    Create pause files atomically.

    Args:
        koan_root: Path to koan root directory
        reason: Pause reason ("quota", "max_runs", etc.)
        timestamp: Reset time (quota) or pause time (max_runs).
                   Defaults to current time.
        display: Human-readable display info
    """
    if timestamp is None:
        timestamp = int(time.time())

    pause_file = os.path.join(koan_root, ".koan-pause")
    reason_file = os.path.join(koan_root, ".koan-pause-reason")

    # Write reason file first (so it's ready before the signal file)
    content = f"{reason}\n{timestamp}\n{display}\n"
    with open(reason_file, "w") as f:
        f.write(content)

    # Touch the pause file (signal)
    Path(pause_file).touch()


def remove_pause(koan_root: str) -> None:
    """Remove both pause files."""
    for name in (".koan-pause", ".koan-pause-reason"):
        path = os.path.join(koan_root, name)
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def check_and_resume(koan_root: str) -> Optional[str]:
    """
    Check if paused and if auto-resume conditions are met.

    Returns:
        A resume message if auto-resumed, None if still paused or not paused.
        The caller should notify the user with the returned message.

    Side effects:
        Removes pause files if auto-resuming.
    """
    state = get_pause_state(koan_root)
    if state is None:
        return None

    if not should_auto_resume(state):
        return None

    # Auto-resume: remove pause files
    remove_pause(koan_root)

    if state.is_quota:
        return f"quota reset time reached ({state.display})"
    else:
        return f"5h have passed since pause ({state.reason})"


# CLI interface for run.sh
if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(
            "Usage: pause_manager.py <command> <koan_root> [args...]",
            file=sys.stderr,
        )
        print("Commands:", file=sys.stderr)
        print(
            "  check <root>       - Check auto-resume (exit 0=resumed, 1=still paused)",
            file=sys.stderr,
        )
        print(
            "  status <root>      - Print pause status as JSON",
            file=sys.stderr,
        )
        print(
            "  create <root> <reason> [timestamp] [display]  - Create pause",
            file=sys.stderr,
        )
        print("  remove <root>      - Remove pause", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]
    koan_root = sys.argv[2]

    if cmd == "check":
        # Check for auto-resume, print message if resumed
        resume_msg = check_and_resume(koan_root)
        if resume_msg:
            print(resume_msg)
            sys.exit(0)
        else:
            sys.exit(1)

    elif cmd == "status":
        # Print pause state as JSON (for run.sh to parse)
        state = get_pause_state(koan_root)
        if state:
            result = {
                "paused": True,
                "reason": state.reason,
                "timestamp": state.timestamp,
                "display": state.display,
            }
        else:
            result = {"paused": is_paused(koan_root), "reason": "", "timestamp": 0, "display": ""}
        print(json.dumps(result))

    elif cmd == "create":
        if len(sys.argv) < 4:
            print("Usage: pause_manager.py create <root> <reason> [timestamp] [display]", file=sys.stderr)
            sys.exit(1)
        reason = sys.argv[3]
        timestamp = int(sys.argv[4]) if len(sys.argv) > 4 else None
        display = sys.argv[5] if len(sys.argv) > 5 else ""
        create_pause(koan_root, reason, timestamp, display)

    elif cmd == "remove":
        remove_pause(koan_root)

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)
