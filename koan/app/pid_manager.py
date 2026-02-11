"""Exclusive PID file management for Kōan processes.

Ensures only one instance of each process type (run, awake, ollama) can run
at a time. Uses fcntl.flock() — OS releases lock on crash.

PID files live in $KOAN_ROOT:
  .koan-pid-run    — agent loop (run.py)
  .koan-pid-awake  — Telegram bridge (awake.py)
  .koan-pid-ollama — ollama serve (external binary)

Usage from Python:
    lock = acquire_pidfile(koan_root, "awake")
    # ... run main loop ...
    release_pidfile(lock, koan_root, "awake")
"""

import fcntl
import os
import shutil
import signal
import subprocess
import sys
import time
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
        koan_root: Root path of the Kōan installation.
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


PROCESS_NAMES = ("run", "awake", "ollama")


def start_runner(koan_root: Path, verify_timeout: float = 3.0) -> tuple:
    """Start the agent loop (run.py) as a detached subprocess.

    Clears .koan-stop signal, launches run.py, and verifies startup
    via PID file.

    Returns (success: bool, message: str).
    """
    # Already running?
    pid = check_pidfile(koan_root, "run")
    if pid:
        return False, f"Agent loop already running (PID {pid})"

    # Clear stop signal so run.py doesn't exit immediately
    stop_file = koan_root / ".koan-stop"
    stop_file.unlink(missing_ok=True)

    # Build launch command — mirrors `make run`
    python = sys.executable
    koan_dir = koan_root / "koan"

    env = {**os.environ, "KOAN_ROOT": str(koan_root), "PYTHONPATH": "."}

    try:
        subprocess.Popen(
            [python, "app/run.py"],
            cwd=str(koan_dir),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        return False, f"Failed to launch: {e}"

    # Wait briefly for run.py to acquire its PID file
    deadline = time.monotonic() + verify_timeout
    while time.monotonic() < deadline:
        new_pid = check_pidfile(koan_root, "run")
        if new_pid:
            return True, f"Agent loop started (PID {new_pid})"
        time.sleep(0.3)

    return False, "Launched but PID not detected — check logs"


def start_ollama(koan_root: Path, verify_timeout: float = 5.0) -> tuple:
    """Start ollama serve as a detached subprocess.

    Checks that ollama binary is available, not already running,
    then launches it in the background with a tracked PID file.

    Returns (success: bool, message: str).
    """
    pid = check_pidfile(koan_root, "ollama")
    if pid:
        return False, f"ollama already running (PID {pid})"

    ollama_bin = shutil.which("ollama")
    if not ollama_bin:
        return False, "ollama not found in PATH — install with: brew install ollama"

    try:
        proc = subprocess.Popen(
            [ollama_bin, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        return False, f"Failed to launch ollama: {e}"

    # Write PID file — ollama serve is an external binary (no flock)
    acquire_pid(koan_root, "ollama", proc.pid)

    # Wait briefly for ollama to start listening
    deadline = time.monotonic() + verify_timeout
    while time.monotonic() < deadline:
        if _is_process_alive(proc.pid):
            return True, f"ollama serve started (PID {proc.pid})"
        time.sleep(0.3)

    return False, "ollama launched but exited immediately — check ollama logs"


def start_stack(koan_root: Path) -> dict:
    """Start the full ollama stack: ollama serve + awake + run.

    Returns dict mapping component name to (success, message).
    """
    results = {}

    # 1. Start ollama serve
    ok, msg = start_ollama(koan_root)
    results["ollama"] = (ok, msg)

    # Give ollama a moment to bind its port
    if ok:
        time.sleep(1)

    # 2. Start awake (Telegram bridge) in background
    awake_pid = check_pidfile(koan_root, "awake")
    if awake_pid:
        results["awake"] = (False, f"Already running (PID {awake_pid})")
    else:
        python = sys.executable
        koan_dir = koan_root / "koan"
        env = {**os.environ, "KOAN_ROOT": str(koan_root), "PYTHONPATH": "."}
        try:
            subprocess.Popen(
                [python, "app/awake.py"],
                cwd=str(koan_dir),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            # Wait for awake PID
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                new_pid = check_pidfile(koan_root, "awake")
                if new_pid:
                    results["awake"] = (True, f"Bridge started (PID {new_pid})")
                    break
                time.sleep(0.3)
            else:
                results["awake"] = (False, "Launched but PID not detected")
        except Exception as e:
            results["awake"] = (False, f"Failed to launch: {e}")

    # 3. Start agent loop (run.py) in background
    ok, msg = start_runner(koan_root)
    results["run"] = (ok, msg)

    return results


def _wait_for_exit(pid: int, timeout: float) -> bool:
    """Wait for a process to exit, with timeout.

    Returns True if the process exited, False if still alive after timeout.
    Handles both child processes (waitpid) and non-children (kill probe).
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # Try to reap child zombie (works if we're the parent)
        try:
            result = os.waitpid(pid, os.WNOHANG)
            if result[0] != 0:
                return True  # Child reaped
        except ChildProcessError:
            pass  # Not our child — use kill probe

        if not _is_process_alive(pid):
            return True
        time.sleep(0.2)
    return not _is_process_alive(pid)


def stop_processes(koan_root: Path, timeout: float = 5.0) -> dict:
    """Stop all running Kōan processes (run + awake + ollama).

    Sends SIGTERM to each running process, waits up to timeout seconds
    for termination. Creates .koan-stop signal file for graceful shutdown.

    Returns dict mapping process name to result: "stopped", "not_running",
    or "force_killed".
    """
    results = {}

    # Create .koan-stop signal file for graceful run loop shutdown
    stop_file = koan_root / ".koan-stop"
    stop_file.write_text("STOP")

    for name in PROCESS_NAMES:
        pid = check_pidfile(koan_root, name)
        if not pid:
            results[name] = "not_running"
            continue

        # Send SIGTERM
        try:
            os.kill(pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            results[name] = "not_running"
            continue

        # Wait for process to exit
        if _wait_for_exit(pid, timeout):
            results[name] = "stopped"
        else:
            # Force kill
            try:
                os.kill(pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
            # Wait briefly for SIGKILL to take effect
            _wait_for_exit(pid, 1.0)
            results[name] = "force_killed"

        # Clean up PID file
        pidfile = _pidfile_path(koan_root, name)
        pidfile.unlink(missing_ok=True)

    return results


# --- CLI interface ---
if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(
            "Usage: python -m app.pid_manager "
            "<acquire-pid|release-pid|check|stop-all> <run|awake|KOAN_ROOT> [koan_root] [pid]",
            file=sys.stderr,
        )
        sys.exit(2)

    action = sys.argv[1]

    if action == "stop-all":
        root = Path(sys.argv[2])
        results = stop_processes(root)
        any_stopped = False
        for name, result in results.items():
            if result == "stopped":
                print(f"  {name}: stopped")
                any_stopped = True
            elif result == "force_killed":
                print(f"  {name}: force killed")
                any_stopped = True
            else:
                print(f"  {name}: not running")
        if not any_stopped:
            print("No processes were running.")
        sys.exit(0)

    if action == "start-runner":
        root = Path(sys.argv[2])
        ok, msg = start_runner(root)
        print(f"  {msg}")
        sys.exit(0 if ok else 1)

    if action == "start-ollama":
        root = Path(sys.argv[2])
        ok, msg = start_ollama(root)
        print(f"  {msg}")
        sys.exit(0 if ok else 1)

    if action == "start-stack":
        root = Path(sys.argv[2])
        results = start_stack(root)
        any_failed = False
        for name in ("ollama", "awake", "run"):
            ok, msg = results.get(name, (False, "skipped"))
            status = "ok" if ok else "FAILED"
            print(f"  {name}: {msg}")
            if not ok and "already running" not in msg.lower():
                any_failed = True
        sys.exit(1 if any_failed else 0)

    if action == "status-all":
        root = Path(sys.argv[2])
        for name in PROCESS_NAMES:
            pid = check_pidfile(root, name)
            if pid:
                print(f"  {name}: running (PID {pid})")
            else:
                print(f"  {name}: not running")
        sys.exit(0)

    if len(sys.argv) < 4:
        print(
            "Usage: python -m app.pid_manager "
            "<acquire-pid|release-pid|check> <run|awake> <koan_root> [pid]",
            file=sys.stderr,
        )
        sys.exit(2)

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
