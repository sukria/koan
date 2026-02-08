"""Exclusive PID file management for Koan processes.

Ensures only one instance of each process type (run, awake) can run
at a time.

Two enforcement strategies:
- Python processes (awake.py): fcntl.flock() — OS releases lock on crash.
- Bash processes (run.sh): PID file + liveness check via CLI.

PID files live in $KOAN_ROOT:
  .koan-pid-run    — agent loop (run.sh)
  .koan-pid-awake  — Telegram bridge (awake.py)

Usage from Python (awake.py):
    lock = acquire_pidfile(koan_root, "awake")
    # ... run main loop ...
    release_pidfile(lock, koan_root, "awake")

Usage from bash (run.sh):
    # At startup — exits 1 if another instance is running:
    python -m app.pid_manager acquire-pid run "$KOAN_ROOT" $$
    # At shutdown:
    python -m app.pid_manager release-pid run "$KOAN_ROOT"
"""

import fcntl
import os
import sys
from pathlib import Path
from typing import Optional, IO


def _pidfile_path(koan_root: Path, process_name: str) -> Path:
    """Return the PID file path for a given process type."""
    return koan_root / f".koan-pid-{process_name}"


def _read_pid(pidfile: Path) -> Optional[int]:
    """Read the PID from a PID file, or None if unreadable."""
    try:
        text = pidfile.read_text().strip()
        return int(text) if text else None
    except (ValueError, OSError):
        return None


def _is_process_alive(pid: int) -> bool:
    """Check if a process with the given PID is alive."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def acquire_pidfile(koan_root: Path, process_name: str) -> IO:
    """Acquire an exclusive flock on the PID file (for Python processes).

    If another instance holds the lock, prints an error with the
    running PID and exits with code 1.

    Returns the open file handle — caller must keep it alive for the
    duration of the process (closing it releases the lock).
    """
    pidfile = _pidfile_path(koan_root, process_name)

    fh = open(pidfile, "a+")

    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        # Lock held by another process
        fh.seek(0)
        existing_pid = None
        try:
            text = fh.read().strip()
            existing_pid = int(text) if text else None
        except ValueError:
            pass
        fh.close()

        msg = f"Error: {process_name} process already running"
        if existing_pid:
            msg += f" (PID {existing_pid})"
        msg += ". Aborting."
        print(msg, file=sys.stderr)
        sys.exit(1)

    # Lock acquired — write our PID
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()

    return fh


def release_pidfile(fh: IO, koan_root: Path, process_name: str) -> None:
    """Release the PID file lock and remove the file."""
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()
    except (OSError, ValueError):
        # ValueError: file already closed (idempotent release)
        pass

    pidfile = _pidfile_path(koan_root, process_name)
    pidfile.unlink(missing_ok=True)


def acquire_pid(koan_root: Path, process_name: str, pid: int) -> None:
    """Write a PID file after checking no other instance is alive.

    For bash processes that can't hold a Python flock. Checks if the
    PID in the existing file is still alive — if so, aborts.

    Args:
        koan_root: Root path of the Koan installation.
        process_name: Process type ("run" or "awake").
        pid: The PID to write (typically $$ from bash).
    """
    pidfile = _pidfile_path(koan_root, process_name)

    if pidfile.exists():
        existing_pid = _read_pid(pidfile)
        if existing_pid and existing_pid != pid and _is_process_alive(existing_pid):
            print(
                f"Error: {process_name} process already running "
                f"(PID {existing_pid}). Aborting.",
                file=sys.stderr,
            )
            sys.exit(1)

    # Write our PID
    pidfile.write_text(str(pid))


def release_pid(koan_root: Path, process_name: str) -> None:
    """Remove the PID file (for bash processes)."""
    pidfile = _pidfile_path(koan_root, process_name)
    pidfile.unlink(missing_ok=True)


def check_pidfile(koan_root: Path, process_name: str) -> Optional[int]:
    """Check if a process is running via its PID file.

    Tries flock first (detects Python processes), falls back to PID
    liveness check (detects bash processes).

    Returns the PID if running, None otherwise.
    """
    pidfile = _pidfile_path(koan_root, process_name)
    if not pidfile.exists():
        return None

    # Try flock probe — detects Python processes holding the lock
    try:
        fh = open(pidfile, "r")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Lock acquired — no Python process holding it
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            fh.close()
        except (OSError, BlockingIOError):
            # Lock held — Python process is running
            pid = _read_pid(pidfile)
            fh.close()
            return pid
    except OSError:
        pass

    # Fall back to PID liveness check (for bash processes)
    pid = _read_pid(pidfile)
    if pid and _is_process_alive(pid):
        return pid

    return None


# --- CLI interface for run.sh ---
if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(
            "Usage: python -m app.pid_manager "
            "<acquire-pid|release-pid|check> <run|awake> <koan_root> [pid]",
            file=sys.stderr,
        )
        sys.exit(2)

    action = sys.argv[1]
    proc_name = sys.argv[2]
    root = Path(sys.argv[3])

    if action == "acquire-pid":
        if len(sys.argv) < 5:
            print("acquire-pid requires a PID argument", file=sys.stderr)
            sys.exit(2)
        acquire_pid(root, proc_name, int(sys.argv[4]))
    elif action == "release-pid":
        release_pid(root, proc_name)
    elif action == "check":
        pid = check_pidfile(root, proc_name)
        if pid:
            print(f"running:{pid}")
        else:
            print("not_running")
    else:
        print(f"Unknown action: {action}", file=sys.stderr)
        sys.exit(2)
