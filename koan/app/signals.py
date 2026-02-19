"""Centralized signal file name constants for Kōan.

All .koan-* file names used for inter-process communication and state
tracking are defined here. This avoids scattering magic strings across
15+ modules and makes renaming or auditing signal files trivial.

Signal files live at KOAN_ROOT and serve as lightweight IPC:
  - Touch/write to signal state changes (pause, stop, restart, shutdown)
  - Read to check current state (status, project, focus, heartbeat)
  - PID files enforce single-instance per process type
"""

# --- Process lifecycle signals ---

STOP_FILE = ".koan-stop"
SHUTDOWN_FILE = ".koan-shutdown"
RESTART_FILE = ".koan-restart"

# --- Pause state ---

PAUSE_FILE = ".koan-pause"
PAUSE_REASON_FILE = ".koan-pause-reason"

# --- Runtime state ---

STATUS_FILE = ".koan-status"
PROJECT_FILE = ".koan-project"
FOCUS_FILE = ".koan-focus"
HEARTBEAT_FILE = ".koan-heartbeat"
VERBOSE_FILE = ".koan-verbose"

# --- Logging / reporting ---

DEBUG_LOG_FILE = ".koan-debug.log"
DAILY_REPORT_FILE = ".koan-daily-report"
QUOTA_RESET_FILE = ".koan-quota-reset"

# --- PID files (parameterized) ---

PID_FILE_PREFIX = ".koan-pid-"


def pid_file(process_name: str) -> str:
    """Return the PID file name for a given process type.

    Args:
        process_name: One of "run", "awake", "ollama".

    Returns:
        Signal file name like ".koan-pid-run".
    """
    return f"{PID_FILE_PREFIX}{process_name}"
