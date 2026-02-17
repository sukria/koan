"""Kōan — Main agent loop.

Manages the agent loop: mission picking, Claude CLI execution,
post-mission processing, pause/resume, signal handling, and
lifecycle notifications.

Usage:
    python -m app.run              # Normal start
    python -m app.run --restart    # Re-exec after restart signal (exit 42)

Features:
- Double-tap CTRL-C protection across ALL phases (missions, rituals,
  sleep, startup, git sync). First press shows warning with current
  activity name; second press within 10s aborts.
- Automatic exception recovery with backoff (survives crashes)
- protected_phase() context manager for easy phase protection
- Restart wrapper (exit code 42 → re-exec)
- Process group isolation for Claude subprocess (SIGINT ignored)
- Colored log output with TTY detection
"""

import os
import signal
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Optional

from app.iteration_manager import plan_iteration
from app.loop_manager import check_pending_missions, interruptible_sleep
from app.pid_manager import acquire_pidfile, release_pidfile
from app.shutdown_manager import is_shutdown_requested, clear_shutdown
from app.utils import atomic_write


# ---------------------------------------------------------------------------
# Recovery configuration
# ---------------------------------------------------------------------------

# Maximum consecutive iteration errors before entering pause mode.
MAX_CONSECUTIVE_ERRORS = 10

# Maximum crashes in main() before giving up.
MAX_MAIN_CRASHES = 5

# Backoff parameters (in seconds).
BACKOFF_MULTIPLIER = 10
MAX_BACKOFF_MAIN = 60
MAX_BACKOFF_ITERATION = 300

# Notification throttling: notify on first error, then every N errors.
ERROR_NOTIFICATION_INTERVAL = 5


# ---------------------------------------------------------------------------
# Colored logging
# ---------------------------------------------------------------------------

_COLORS = {}

# Standalone ANSI reset (no dependency on _COLORS initialization)
_ANSI_RESET = "\033[0m"


def _reset_terminal():
    """Write an ANSI reset to stdout and flush, restoring default attributes.

    Called on exit paths to ensure the terminal is not left with active
    ANSI attributes (DIM, BOLD, color, etc.) after Kōan shuts down.
    """
    try:
        sys.stdout.write(_ANSI_RESET)
        sys.stdout.flush()
    except Exception:
        pass  # Terminal may be gone during shutdown


def _init_colors():
    """Initialize ANSI color codes based on TTY detection."""
    global _COLORS
    if os.environ.get("KOAN_FORCE_COLOR", "") or sys.stdout.isatty():
        _COLORS = {
            "reset": "\033[0m",
            "bold": "\033[1m",
            "dim": "\033[2m",
            "red": "\033[31m",
            "green": "\033[32m",
            "yellow": "\033[33m",
            "blue": "\033[34m",
            "magenta": "\033[35m",
            "cyan": "\033[36m",
            "white": "\033[37m",
        }
    else:
        _COLORS = {k: "" for k in [
            "reset", "bold", "dim", "red", "green", "yellow",
            "blue", "magenta", "cyan", "white",
        ]}


_CATEGORY_COLORS = {
    "koan": "cyan",
    "error": "bold+red",
    "init": "blue",
    "health": "yellow",
    "git": "magenta",
    "mission": "green",
    "quota": "bold+yellow",
    "pause": "dim+blue",
}


def log(category: str, message: str):
    """Print a colored log message."""
    if not _COLORS:
        _init_colors()
    color_spec = _CATEGORY_COLORS.get(category, "white")
    parts = color_spec.split("+")
    prefix = "".join(_COLORS.get(p, "") for p in parts)
    reset = _COLORS.get("reset", "")
    print(f"{prefix}[{category}]{reset} {message}", flush=True)


def _styled(text: str, *styles: str) -> str:
    """Apply ANSI styles to text. E.g. _styled("hi", "bold", "cyan")."""
    if not _COLORS:
        _init_colors()
    prefix = "".join(_COLORS.get(s, "") for s in styles)
    return f"{prefix}{text}{_COLORS.get('reset', '')}"


def bold_cyan(text: str) -> str:
    return _styled(text, "bold", "cyan")


def bold_green(text: str) -> str:
    return _styled(text, "bold", "green")


# ---------------------------------------------------------------------------
# Recovery helpers
# ---------------------------------------------------------------------------

def _calculate_backoff(attempt: int, max_backoff: int) -> int:
    """Calculate linear backoff capped at max_backoff.

    Returns: attempt * BACKOFF_MULTIPLIER, capped at max_backoff.
    """
    return min(BACKOFF_MULTIPLIER * attempt, max_backoff)


def _should_notify_error(attempt: int) -> bool:
    """Determine if error notification should be sent.

    Notifies on first error and every ERROR_NOTIFICATION_INTERVAL errors.
    """
    return attempt == 1 or attempt % ERROR_NOTIFICATION_INTERVAL == 0


# ---------------------------------------------------------------------------
# Status file
# ---------------------------------------------------------------------------

def set_status(koan_root: str, message: str):
    """Write loop status for /status and dashboard."""
    try:
        atomic_write(Path(koan_root, ".koan-status"), message)
    except Exception as e:
        log("error", f"Failed to write status: {e}")


def _build_startup_status(koan_root: str) -> str:
    """Build a human-readable status line for startup notification.

    Returns a status string like:
    - "✅ Active — ready to work"
    - "⏸️ Paused (quota) — resets 10am (Europe/Paris). Use /resume to unpause."
    - "⏸️ Paused (max_runs) — use /resume to unpause."
    """
    from app.pause_manager import get_pause_state

    if not Path(koan_root, ".koan-pause").exists():
        return "✅ Active — ready to work"

    state = get_pause_state(koan_root)
    if state and state.display:
        return f"⏸️ Paused ({state.reason}) — {state.display}. Use /resume to unpause."
    elif state:
        return f"⏸️ Paused ({state.reason}) — use /resume to unpause."
    else:
        return "⏸️ Paused — use /resume to unpause."


# ---------------------------------------------------------------------------
# Signal handling — double-tap CTRL-C
# ---------------------------------------------------------------------------

class SignalState:
    """Mutable state for SIGINT handler (double-tap pattern)."""
    task_running: bool = False
    first_ctrl_c: float = 0
    claude_proc: Optional[subprocess.Popen] = None
    timeout: int = 10
    phase: str = ""  # Human-readable description of current activity


_sig = SignalState()


class protected_phase:
    """Context manager that activates double-tap CTRL-C protection.

    Usage:
        with protected_phase("Running morning ritual"):
            subprocess.run(...)

    First CTRL-C warns with the phase name.
    Second CTRL-C within timeout raises KeyboardInterrupt.
    """

    def __init__(self, phase_name: str):
        self.phase_name = phase_name
        self.prev_phase = ""
        self.prev_task_running = False

    def __enter__(self):
        self.prev_phase = _sig.phase
        self.prev_task_running = _sig.task_running
        _sig.phase = self.phase_name
        _sig.task_running = True
        _sig.first_ctrl_c = 0
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        _sig.phase = self.prev_phase
        _sig.task_running = self.prev_task_running
        _sig.first_ctrl_c = 0
        return False  # Don't suppress exceptions


def _on_sigint(signum, frame):
    """SIGINT handler: first press warns, second press aborts."""
    if not _sig.task_running:
        raise KeyboardInterrupt

    now = time.time()
    if _sig.first_ctrl_c > 0:
        elapsed = now - _sig.first_ctrl_c
        if elapsed <= _sig.timeout:
            # Second CTRL-C within timeout — abort
            print()
            log("koan", "Confirmed. Aborting...")
            if _sig.claude_proc and _sig.claude_proc.poll() is None:
                _sig.claude_proc.terminate()
            _sig.first_ctrl_c = 0
            _sig.task_running = False
            raise KeyboardInterrupt

    # First CTRL-C (or timeout expired)
    _sig.first_ctrl_c = now
    print()
    phase_hint = f" ({_sig.phase})" if _sig.phase else ""
    log("koan", f"⚠️  Press CTRL-C again within {_sig.timeout}s to abort.{phase_hint}")


# ---------------------------------------------------------------------------
# Claude subprocess execution
# ---------------------------------------------------------------------------

def run_claude_task(
    cmd: list,
    stdout_file: str,
    stderr_file: str,
    cwd: str,
) -> int:
    """Run Claude CLI as a subprocess with SIGINT isolation.

    The child process ignores SIGINT (via preexec_fn) so the double-tap
    pattern works: first CTRL-C only warns the user, second kills the child.

    Returns the child exit code.
    """
    _sig.task_running = True
    _sig.first_ctrl_c = 0

    from app.cli_exec import popen_cli

    with open(stdout_file, "w") as out_f, open(stderr_file, "w") as err_f:
        proc, cleanup = popen_cli(
            cmd,
            stdout=out_f,
            stderr=err_f,
            cwd=cwd,
            start_new_session=True,
        )
        _sig.claude_proc = proc

        try:
            # Wait for child, handling SIGINT interruptions gracefully
            while True:
                try:
                    proc.wait()
                    break
                except (KeyboardInterrupt, InterruptedError):
                    # If task_running was cleared by on_sigint (double-tap),
                    # the child was terminated — wait for it to finish
                    if not _sig.task_running:
                        try:
                            proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait()
                        break
                    # Single CTRL-C — keep waiting
                    continue
        finally:
            cleanup()

    exit_code = proc.returncode
    _sig.claude_proc = None
    _sig.task_running = False
    _sig.first_ctrl_c = 0
    return exit_code


# ---------------------------------------------------------------------------
# Project configuration
# ---------------------------------------------------------------------------

def parse_projects() -> list:
    """Parse project configuration with validation.

    Delegates to get_known_projects() which checks:
    1. projects.yaml (if exists)
    2. KOAN_PROJECTS env var (fallback)

    Returns list of (name, path) tuples. Exits on error.
    """
    from app.utils import get_known_projects
    projects = get_known_projects()

    if not projects:
        log("error", "No projects configured. Create projects.yaml or set KOAN_PROJECTS env var.")
        sys.exit(1)

    if len(projects) > 50:
        log("error", f"Max 50 projects allowed. You have {len(projects)}.")
        sys.exit(1)

    for name, path in projects:
        if not Path(path).is_dir():
            log("error", f"Project '{name}' path does not exist: {path}")
            sys.exit(1)

    return projects


# ---------------------------------------------------------------------------
# Startup sequence
# ---------------------------------------------------------------------------

def run_startup(koan_root: str, instance: str, projects: list):
    """Run all startup tasks (crash recovery, health, sync, etc.)."""
    from app.banners import print_agent_banner
    from app.github_auth import setup_github_auth
    from app.git_sync import GitSync
    from app.health_check import check_and_alert
    from app.recover import recover_missions
    from app.utils import (
        get_branch_prefix,
        get_cli_binary_for_shell,
        get_interval_seconds,
        get_max_runs,
        get_start_on_pause,
    )

    # Load config
    max_runs = get_max_runs()
    interval = get_interval_seconds()
    branch_prefix = get_branch_prefix()
    cli_provider = get_cli_binary_for_shell()

    # Print banner
    try:
        print_agent_banner(f"agent loop — {cli_provider}")
    except Exception as e:
        log("error", f"Banner display failed: {e}")

    with protected_phase("Startup checks"):
        # Crash recovery
        log("health", "Checking for interrupted missions...")
        try:
            recover_missions(instance)
        except Exception as e:
            log("error", f"Crash recovery failed: {e}")

        # Auto-migrate env vars to projects.yaml (one-shot, idempotent)
        try:
            from app.projects_migration import run_migration
            migration_msgs = run_migration(koan_root)
            for msg in migration_msgs:
                log("init", f"[migration] {msg}")
        except Exception as e:
            log("error", f"Projects migration failed: {e}")

        # Auto-populate github_url in projects.yaml from git remotes
        try:
            from app.projects_config import ensure_github_urls
            gh_msgs = ensure_github_urls(koan_root)
            for msg in gh_msgs:
                log("init", f"[github-urls] {msg}")
        except Exception as e:
            log("error", f"GitHub URL population failed: {e}")

        # Initialize workspace + yaml merged project registry
        try:
            from app.projects_merged import (
                refresh_projects, 
                get_warnings, 
                populate_workspace_github_urls,
                get_yaml_project_names
            )
            
            projects = refresh_projects(koan_root)
            
            # Count workspace projects (not in projects.yaml)
            yaml_project_names = get_yaml_project_names(koan_root)
            ws_count = sum(1 for name, _ in projects if name not in yaml_project_names)
            if ws_count:
                log("init", f"[workspace] Discovered {ws_count} project(s) from workspace/")
            
            # Populate github_url cache for workspace projects
            gh_count = populate_workspace_github_urls(koan_root)
            if gh_count:
                log("init", f"[workspace] Cached {gh_count} github_url(s) from git remotes")
            
            # Log any warnings from merge
            for warning in get_warnings():
                log("warn", f"[workspace] {warning}")
        except Exception as e:
            log("error", f"Workspace discovery failed: {e}")

        # Sanity checks (all modules in koan/sanity/, alphabetical order)
        log("health", "Running sanity checks...")
        try:
            from sanity import run_all
            for name, modified, changes in run_all(instance):
                if modified:
                    for change in changes:
                        log("health", f"  [{name}] {change}")
        except Exception as e:
            log("error", f"Sanity checks failed: {e}")

        # Memory cleanup
        log("health", "Running memory cleanup...")
        try:
            from app.memory_manager import run_cleanup
            run_cleanup(instance)
        except Exception as e:
            log("error", f"Memory cleanup failed: {e}")

        # Mission history cleanup
        try:
            from app.mission_history import cleanup_old_entries
            cleanup_old_entries(instance)
        except Exception as e:
            log("error", f"Mission history cleanup failed: {e}")

        # Health check
        log("health", "Checking Telegram bridge health...")
        try:
            check_and_alert(koan_root, max_age=120)
        except Exception as e:
            log("error", f"Health check failed: {e}")

    with protected_phase("Self-reflection check"):
        log("health", "Checking self-reflection trigger...")
        try:
            from app.self_reflection import (
                should_reflect, run_reflection, save_reflection, notify_outbox,
            )
            inst_path = Path(instance)
            if should_reflect(inst_path):
                observations = run_reflection(inst_path)
                if observations:
                    save_reflection(inst_path, observations)
                    notify_outbox(inst_path, observations)
        except Exception as e:
            log("error", f"Self-reflection check failed: {e}")

    # Start on pause
    if get_start_on_pause():
        # Remove stale reason file to prevent auto-resume from a previous
        # session's quota/max_runs pause.  Without this, handle_pause() →
        # check_and_resume() reads the old reason, finds the cooldown
        # elapsed, and immediately resumes — bypassing start_on_pause.
        koan_root_path = Path(koan_root)
        (koan_root_path / ".koan-pause-reason").unlink(missing_ok=True)
        if not (koan_root_path / ".koan-pause").exists():
            log("pause", "start_on_pause=true in config. Entering pause mode.")
            (koan_root_path / ".koan-pause").touch()

    # Git identity
    koan_email = os.environ.get("KOAN_EMAIL", "")
    if koan_email:
        os.environ["GIT_AUTHOR_NAME"] = "Kōan"
        os.environ["GIT_AUTHOR_EMAIL"] = koan_email
        os.environ["GIT_COMMITTER_NAME"] = "Kōan"
        os.environ["GIT_COMMITTER_EMAIL"] = koan_email

    # GitHub auth
    if os.environ.get("GITHUB_USER"):
        success = setup_github_auth()
        if success:
            log("init", f"GitHub CLI authenticated as {os.environ['GITHUB_USER']}")
        else:
            log("init", f"Warning: GitHub auth failed for {os.environ['GITHUB_USER']}")

    # Startup notification
    log("init", f"Starting. Max runs: {max_runs}, interval: {interval}s")

    project_list = "\n".join(f"  • {n}" for n, _ in sorted(projects))
    status_line = _build_startup_status(koan_root)
    set_status(koan_root, status_line)
    _notify(instance, (
        f"Kōan starting — {max_runs} max runs, {interval}s interval.\n"
        f"Projects:\n{project_list}\n"
        f"Current: {projects[0][0]}.\n"
        f"Status: {status_line}"
    ))

    with protected_phase("Git sync"):
        log("git", "Running git sync...")
        for name, path in projects:
            try:
                gs = GitSync(instance, name, path)
                gs.sync_and_report()
            except Exception as e:
                log("error", f"Git sync failed for {name}: {e}")

    # Daily report
    try:
        from app.daily_report import send_daily_report
        send_daily_report()
    except Exception as e:
        log("error", f"Daily report failed: {e}")

    with protected_phase("Morning ritual"):
        log("init", "Running morning ritual...")
        try:
            from app.rituals import run_ritual
            run_ritual("morning", Path(instance))
        except Exception as e:
            log("error", f"Morning ritual failed: {e}")

    return max_runs, interval, branch_prefix


# ---------------------------------------------------------------------------
# Notify helper
# ---------------------------------------------------------------------------

def _notify(instance: str, message: str):
    """Send a formatted notification to Telegram."""
    try:
        from app.notify import format_and_send
        format_and_send(message, instance_dir=instance)
    except Exception as e:
        log("error", f"Notification failed: {e}")


def _notify_mission_end(
    instance: str,
    project_name: str,
    run_num: int,
    max_runs: int,
    exit_code: int,
    mission_title: str = "",
):
    """Send a notification when a mission or autonomous run completes.

    Always sends — both on success and failure — so the human always
    gets a status update. Uses unicode prefix: ✅ for success, ❌ for failure.
    On success, appends a brief journal summary when available.
    """
    if exit_code == 0:
        prefix = "✅"
        label = mission_title if mission_title else f"Autonomous run on {project_name}"
        msg = f"{prefix} Run {run_num}/{max_runs} — [{project_name}] {label}"
        # Try to attach a brief summary from the journal
        try:
            from app.mission_summary import get_mission_summary
            summary = get_mission_summary(instance, project_name, max_chars=300)
            if summary:
                msg += f"\n\n{summary}"
        except Exception as e:
            log("error", f"Mission summary extraction failed: {e}")
    else:
        prefix = "❌"
        label = mission_title if mission_title else "Run"
        msg = f"{prefix} Run {run_num}/{max_runs} — [{project_name}] Failed: {label}"

    _notify(instance, msg)


# ---------------------------------------------------------------------------
# Instance commit helper
# ---------------------------------------------------------------------------

def _commit_instance(instance: str, message: str = ""):
    """Commit instance changes and push."""
    if not message:
        message = f"koan: {time.strftime('%Y-%m-%d-%H:%M')}"
    try:
        subprocess.run(["git", "add", "-A"], cwd=instance, capture_output=True, timeout=10)
        diff = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=instance, capture_output=True, timeout=10,
        )
        if diff.returncode != 0:
            subprocess.run(
                ["git", "commit", "-m", message],
                cwd=instance, capture_output=True, timeout=30,
            )
            subprocess.run(
                ["git", "push", "origin", "main"],
                cwd=instance, capture_output=True, timeout=30,
            )
    except Exception as e:
        log("error", f"Instance commit/push failed: {e}")


# ---------------------------------------------------------------------------
# Pause mode handler
# ---------------------------------------------------------------------------

def handle_pause(
    koan_root: str, instance: str, projects: list, max_runs: int,
) -> Optional[str]:
    """Handle pause mode. Returns "resume" if resumed, None to stay paused."""
    set_status(koan_root, f"Paused ({time.strftime('%H:%M')})")
    log("pause", f"Paused. Contemplative mode. ({time.strftime('%H:%M')})")

    # Check auto-resume
    try:
        from app.pause_manager import check_and_resume
        resume_msg = check_and_resume(koan_root)
        if resume_msg:
            log("pause", f"Auto-resume: {resume_msg}")
            _reset_usage_session(instance)
            _notify(instance, f"🔄 Kōan auto-resumed: {resume_msg}. Starting fresh (0/{max_runs} runs).")
            return "resume"
    except Exception as e:
        log("error", f"Auto-resume check failed: {e}")

    # Manual resume (pause file already removed — /resume handler already
    # resets session counters for quota pauses, but we reset here too as
    # a safety net for any resume path)
    if not Path(koan_root, ".koan-pause").exists():
        log("pause", "Manual resume detected")
        _reset_usage_session(instance)
        return "resume"

    # Contemplative session (~50% chance, skip in focus mode)
    import random
    roll = random.randint(0, 99)
    in_focus = False
    try:
        from app.focus_manager import check_focus
        in_focus = check_focus(koan_root) is not None
    except Exception as e:
        log("error", f"Focus mode check failed in pause: {e}")

    # Find first exploration-enabled project for contemplation
    exploration_project = None
    try:
        from app.projects_config import load_projects_config, get_project_exploration
        config = load_projects_config(koan_root)
        if config is not None:
            for name, path in projects:
                if get_project_exploration(config, name):
                    exploration_project = (name, path)
                    break
        else:
            exploration_project = projects[0] if projects else None
    except Exception as e:
        log("error", f"Exploration config check failed in pause: {e}")
        exploration_project = projects[0] if projects else None

    if roll < 50 and not in_focus and exploration_project is not None:
        log("pause", "A thought stirs...")
        project_name, project_path = exploration_project
        atomic_write(Path(koan_root, ".koan-project"), project_name)

        log("pause", "Running contemplative session...")
        try:
            from app.contemplative_runner import build_contemplative_command
            cmd = build_contemplative_command(
                instance=instance,
                project_name=project_name,
                session_info="Pause mode. Run loop paused.",
            )
            exit_code = run_claude_task(
                cmd=cmd,
                stdout_file=os.devnull,
                stderr_file=os.devnull,
                cwd=koan_root,
            )
            log("pause", "Contemplative session ended.")
        except KeyboardInterrupt:
            raise
        except Exception as e:
            log("error", f"Contemplative session error: {e}")

    # Sleep 5 min in 5s increments — check for resume/restart
    with protected_phase("Paused — waiting for resume"):
        for _ in range(60):
            if not Path(koan_root, ".koan-pause").exists():
                return "resume"
            if Path(koan_root, ".koan-restart").exists():
                break
            time.sleep(5)

    return None


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main_loop():
    """The Kōan main loop."""
    _init_colors()

    # Validate environment
    koan_root = os.environ.get("KOAN_ROOT", "")
    if not koan_root:
        log("error", "KOAN_ROOT environment variable not set.")
        sys.exit(1)

    instance = os.path.join(koan_root, "instance")
    if not Path(instance).is_dir():
        log("error", "No instance/ directory found. Run: cp -r instance.example instance")
        sys.exit(1)

    # Run pending data migrations (e.g. French→English header conversion)
    from app.migration_runner import run_pending_migrations
    applied = run_pending_migrations()
    if applied:
        log("init", f"Applied {len(applied)} migration(s)")

    # Set PYTHONPATH
    os.environ["PYTHONPATH"] = os.path.join(koan_root, "koan")

    # Parse projects (projects.yaml > KOAN_PROJECTS)
    projects = parse_projects()

    # Record startup time
    start_time = time.time()

    # Acquire PID (flock-based exclusive lock)
    pidfile_lock = acquire_pidfile(Path(koan_root), "run")

    # Clear stale signal files from a previous session.
    # If `make stop` or `/stop` ran while run.py was NOT running, the signal
    # file persists and would cause an immediate exit on next startup.
    Path(koan_root, ".koan-stop").unlink(missing_ok=True)
    Path(koan_root, ".koan-shutdown").unlink(missing_ok=True)

    # Install SIGINT handler
    signal.signal(signal.SIGINT, _on_sigint)

    # Initialize project state
    atomic_write(Path(koan_root, ".koan-project"), projects[0][0])
    os.environ["KOAN_CURRENT_PROJECT"] = projects[0][0]
    os.environ["KOAN_CURRENT_PROJECT_PATH"] = projects[0][1]

    count = 0
    consecutive_errors = 0
    try:
        # Startup sequence
        max_runs, interval, branch_prefix = run_startup(koan_root, instance, projects)

        git_sync_interval = int(os.environ.get("KOAN_GIT_SYNC_INTERVAL", "5"))

        while True:
            # --- Stop check ---
            stop_file = Path(koan_root, ".koan-stop")
            if stop_file.exists():
                log("koan", "Stop requested.")
                stop_file.unlink(missing_ok=True)
                current = Path(koan_root, ".koan-project").read_text().strip()
                _notify(instance, f"Kōan stopped on request after {count} runs. Last project: {current}.")
                break

            # --- Shutdown check (stops both agent loop and bridge) ---
            if is_shutdown_requested(koan_root, start_time):
                log("koan", "Shutdown requested. Exiting.")
                clear_shutdown(koan_root)
                current = Path(koan_root, ".koan-project").read_text().strip()
                _notify(instance, f"Kōan shutdown after {count} runs. Last project: {current}.")
                break

            # --- Restart check ---
            restart_file = Path(koan_root, ".koan-restart")
            if restart_file.exists():
                try:
                    mtime = restart_file.stat().st_mtime
                    if mtime > start_time:
                        log("koan", "Restart requested. Exiting for re-launch...")
                        sys.exit(42)
                except Exception as e:
                    log("error", f"Restart signal check failed: {e}")

            # --- Pause mode ---
            if Path(koan_root, ".koan-pause").exists():
                result = handle_pause(koan_root, instance, projects, max_runs)
                if result == "resume":
                    count = 0
                    consecutive_errors = 0
                continue

            # --- Iteration body (exception-protected) ---
            try:
                _run_iteration(
                    koan_root=koan_root,
                    instance=instance,
                    projects=projects,
                    count=count,
                    max_runs=max_runs,
                    interval=interval,
                    git_sync_interval=git_sync_interval,
                )
                consecutive_errors = 0
                count += 1
            except KeyboardInterrupt:
                raise
            except SystemExit:
                raise
            except Exception as e:
                consecutive_errors += 1
                _handle_iteration_error(
                    e, consecutive_errors, koan_root, instance,
                )

    except KeyboardInterrupt:
        current = "unknown"
        try:
            current = Path(koan_root, ".koan-project").read_text().strip()
        except Exception as e:
            log("error", f"Failed to read last project: {e}")
        _notify(instance, f"Kōan interrupted after {count} runs. Last project: {current}.")
    finally:
        # Cleanup
        Path(koan_root, ".koan-status").unlink(missing_ok=True)
        release_pidfile(pidfile_lock, Path(koan_root), "run")
        log("koan", f"Shutdown. {count} runs executed.")
        _reset_terminal()


# ---------------------------------------------------------------------------
# Iteration body (extracted for exception isolation)
# ---------------------------------------------------------------------------

def _run_iteration(
    koan_root: str,
    instance: str,
    projects: list,
    count: int,
    max_runs: int,
    interval: int,
    git_sync_interval: int,
):
    """Execute a single iteration of the main loop.

    Called from main_loop() within a try/except block that catches
    unexpected exceptions without killing the process.

    Exceptions:
        KeyboardInterrupt: Propagates to caller (user abort)
        SystemExit: Propagates to caller (restart signal)
        Exception: Caught by caller for recovery

    Note: Count is incremented by the caller on success.
    """
    run_num = count + 1
    set_status(koan_root, f"Run {run_num}/{max_runs} — preparing")
    print()
    print(bold_cyan(f"=== Run {run_num}/{max_runs} — {time.strftime('%Y-%m-%d %H:%M:%S')} ==="))

    # Plan iteration (delegated to iteration_manager)
    last_project = ""
    try:
        last_project = Path(koan_root, ".koan-project").read_text().strip()
    except Exception as e:
        log("error", f"Failed to read last project file: {e}")
    plan = plan_iteration(
        instance_dir=instance,
        koan_root=koan_root,
        run_num=run_num,
        count=count,
        projects=projects,
        last_project=last_project,
    )

    # --- Iteration decision summary (always visible in logs) ---
    log("koan", f"Iteration plan: action={plan['action']}, "
        f"project={plan['project_name']}, mode={plan['autonomous_mode']}, "
        f"budget={plan['available_pct']}%"
        f"{', mission=' + plan['mission_title'][:60] if plan['mission_title'] else ''}")
    if plan.get("error"):
        log("error", f"Iteration plan error: {plan['error']}")

    # Display usage
    log("quota", "Usage Status:")
    if plan["display_lines"]:
        for line in plan["display_lines"]:
            print(f"  {line}")
    else:
        print("  [No usage data available - using fallback mode]")
    print(f"  Safety margin: 10% → Available: {plan['available_pct']}%")
    print()

    # Log recurring injections
    for line in plan.get("recurring_injected", []):
        log("mission", line)

    # --- Handle special actions ---
    action = plan["action"]
    project_name = plan["project_name"]
    project_path = plan["project_path"]

    if action == "error":
        log("error", plan.get("error", "Unknown error"))
        _notify(instance, f"Mission error: {plan.get('error', 'Unknown')}")
        # Don't kill the process — raise so the caller can recover
        raise RuntimeError(f"Mission error: {plan.get('error', 'Unknown')}")

    if action == "contemplative":
        log("pause", f"Decision: CONTEMPLATIVE mode (random reflection)")
        print("  Action: Running contemplative session instead of autonomous work")
        print()
        _notify(instance, f"🪷 Run {run_num}/{max_runs} — Contemplative mode on {project_name}")

        log("pause", "Running contemplative session...")
        try:
            from app.contemplative_runner import build_contemplative_command
            cmd = build_contemplative_command(
                instance=instance,
                project_name=project_name,
                session_info=f"Run {run_num}/{max_runs} on {project_name}. Mode: {plan['autonomous_mode']}.",
            )
            run_claude_task(cmd, os.devnull, os.devnull, cwd=koan_root)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            log("error", f"Contemplative error: {e}")
        log("pause", "Contemplative session ended.")

        # Check for pending before sleeping
        if check_pending_missions(instance):
            log("koan", "Pending missions found after contemplation — skipping sleep")
        else:
            set_status(koan_root, f"Idle — post-contemplation sleep ({time.strftime('%H:%M')})")
            log("pause", f"Contemplative session complete. Sleeping {interval}s...")
            with protected_phase("Sleeping between runs"):
                wake = interruptible_sleep(interval, koan_root, instance)
            if wake == "mission":
                log("koan", "New mission detected during sleep — waking up early")
        return

    # Idle wait actions — all follow the same sleep-and-check pattern
    _IDLE_WAIT_CONFIG = {
        "focus_wait": lambda p: (
            f"Focus mode active ({p.get('focus_remaining', 'unknown')} remaining) — no missions pending, sleeping",
            f"Focus mode — waiting for missions ({p.get('focus_remaining', 'unknown')} remaining)",
        ),
        "schedule_wait": lambda _: (
            "Work hours active — waiting for missions (exploration suppressed)",
            f"Work hours — waiting for missions ({time.strftime('%H:%M')})",
        ),
        "exploration_wait": lambda _: (
            "All projects have exploration disabled — waiting for missions",
            f"Exploration disabled — waiting for missions ({time.strftime('%H:%M')})",
        ),
        "pr_limit_wait": lambda _: (
            "PR limit reached for all projects — waiting for reviews",
            f"PR limit reached — waiting for reviews ({time.strftime('%H:%M')})",
        ),
    }
    if action in _IDLE_WAIT_CONFIG:
        log_msg, status_msg = _IDLE_WAIT_CONFIG[action](plan)
        log("koan", log_msg)
        set_status(koan_root, status_msg)
        with protected_phase(status_msg):
            wake = interruptible_sleep(interval, koan_root, instance)
        if wake == "mission":
            log("koan", f"New mission detected during {action} — waking up")
        return

    if action == "wait_pause":
        log("quota", "Decision: WAIT mode (budget exhausted)")
        print(f"  Reason: {plan['decision_reason']}")
        print("  Action: Entering pause mode (will auto-resume when quota resets)")
        print()
        try:
            from app.send_retrospective import create_retrospective
            create_retrospective(Path(instance), project_name)
        except Exception as e:
            log("error", f"Retrospective sending failed: {e}")
        # Compute a proper future reset timestamp to avoid instant auto-resume
        reset_ts, reset_display = _compute_quota_reset_ts(instance)
        from app.pause_manager import create_pause
        create_pause(koan_root, "quota", reset_ts, reset_display)

        # Build quota detail string for the notification
        quota_details = plan['decision_reason']
        if plan["display_lines"]:
            quota_details += "\n" + "\n".join(plan["display_lines"])

        _notify(instance, (
            f"⏸️ Kōan paused: budget exhausted after {count} runs on [{project_name}].\n"
            f"{quota_details}\n"
            f"Auto-resume when session resets or use /resume."
        ))
        return

    # --- Pre-flight quota check ---
    if action in ("mission", "autonomous"):
        try:
            from app.preflight import preflight_quota_check
            pf_ok, pf_error = preflight_quota_check(
                project_path=project_path,
                instance_dir=instance,
                project_name=project_name,
            )
            if not pf_ok:
                log("quota", "Pre-flight probe detected quota exhaustion")
                pf_reset_ts, pf_reset_display = _compute_preflight_reset_ts(pf_error)
                from app.pause_manager import create_pause
                create_pause(koan_root, "quota", pf_reset_ts, pf_reset_display)
                label = plan["mission_title"] if plan["mission_title"] else "autonomous run"
                _notify(instance, (
                    f"⏸️ Pre-flight quota check failed before [{project_name}] {label}.\n"
                    f"Pausing until quota resets. Use /resume to restart manually."
                ))
                # Mission stays In Progress — crash recovery will
                # move it back to Pending on next startup.
                return
        except Exception as e:
            log("error", f"Pre-flight quota check error: {e}")
            # Proceed optimistically on error

    # --- Execute mission or autonomous run ---
    mission_title = plan["mission_title"]
    autonomous_mode = plan["autonomous_mode"]
    focus_area = plan["focus_area"]
    available_pct = plan["available_pct"]

    # --- Dedup guard ---
    if mission_title:
        try:
            from app.mission_history import should_skip_mission
            if should_skip_mission(instance, mission_title, max_executions=3):
                log("mission", f"Skipping repeated mission (3+ attempts): {mission_title[:60]}")
                _update_mission_in_file(instance, mission_title, failed=True)
                _notify(instance, f"⚠️ Mission failed 3+ times, moved to Failed: {mission_title[:60]}")
                _commit_instance(instance)
                return
        except (OSError, ValueError) as e:
            log("error", f"Dedup guard error: {e}")

    # Set project state
    atomic_write(Path(koan_root, ".koan-project"), project_name)
    os.environ["KOAN_CURRENT_PROJECT"] = project_name
    os.environ["KOAN_CURRENT_PROJECT_PATH"] = project_path

    print(bold_green(f">>> Current project: {project_name}") + f" ({project_path})")
    print()

    # --- Mark mission as In Progress ---
    if mission_title:
        _start_mission_in_file(instance, mission_title)

    # --- Check for skill-dispatched mission ---
    # Missions starting with /command (e.g. "/plan Add dark mode")
    # are dispatched directly to the skill's CLI runner, bypassing
    # the Claude agent.
    if mission_title:
        from app.debug import debug_log as _debug_log
        preview = f"{mission_title[:100]}..." if len(mission_title) > 100 else mission_title
        _debug_log(f"[run] checking skill dispatch for: {preview}")
        from app.skill_dispatch import dispatch_skill_mission, is_skill_mission
        skill_cmd = dispatch_skill_mission(
            mission_text=mission_title,
            project_name=project_name,
            project_path=project_path,
            koan_root=koan_root,
            instance_dir=instance,
        )
        if skill_cmd:
            _debug_log(f"[run] skill dispatch matched: {' '.join(skill_cmd[:5])}")
            log("mission", "Decision: SKILL DISPATCH (direct runner)")
            print(f"  Mission: {mission_title}")
            print(f"  Project: {project_name}")
            print(f"  Runner: {' '.join(skill_cmd[:4])}...")
            print()
            set_status(koan_root, f"Run {run_num}/{max_runs} — skill dispatch on {project_name}")
            _notify(instance, f"🚀 Run {run_num}/{max_runs} — [{project_name}] Skill: {mission_title}")

            with protected_phase(f"Skill: {mission_title[:50]}"):
                exit_code = _run_skill_mission(
                    skill_cmd=skill_cmd,
                    koan_root=koan_root,
                    instance=instance,
                    project_name=project_name,
                    project_path=project_path,
                    run_num=run_num,
                    mission_title=mission_title,
                    autonomous_mode=autonomous_mode,
                )

            if exit_code == 0:
                log("mission", f"Run {run_num}/{max_runs} — [{project_name}] skill completed")
            _notify_mission_end(
                instance, project_name, run_num, max_runs,
                exit_code, mission_title,
            )

            _finalize_mission(instance, mission_title, project_name, exit_code)
            _commit_instance(instance)

            if check_pending_missions(instance):
                log("koan", "Pending missions — skipping sleep")
            else:
                set_status(koan_root, f"Idle — sleeping ({time.strftime('%H:%M')})")
                with protected_phase("Sleeping between runs"):
                    wake = interruptible_sleep(interval, koan_root, instance)
                if wake == "mission":
                    log("koan", "New mission detected during sleep — waking up early")
            return
        elif is_skill_mission(mission_title):
            # Skill mission but no runner matched — fail it instead
            # of falling through to Claude (which would re-queue it).
            # Note: is_skill_mission() is called again intentionally —
            # dispatch returns None for both "not a skill" and "unknown
            # runner", and we need to distinguish the two cases here.
            _debug_log(f"[run] skill mission unhandled, failing: {mission_title[:200]}")
            log("warning", f"Skill mission has no runner, failing: {mission_title[:80]}")
            _notify(instance, f"⚠️ [{project_name}] Unknown skill command: {mission_title[:80]}")
            _finalize_mission(instance, mission_title, project_name, exit_code=1)
            _commit_instance(instance)
            return

    # Lifecycle notification
    if mission_title:
        log("mission", "Decision: MISSION mode (assigned)")
        print(f"  Mission: {mission_title}")
        print(f"  Project: {project_name}")
        print()
        _notify(instance, f"🚀 Run {run_num}/{max_runs} — [{project_name}] Mission taken: {mission_title}")
    else:
        mode_upper = autonomous_mode.upper()
        log("mission", f"Decision: {mode_upper} mode (estimated cost: 5.0% session)")
        print(f"  Reason: {plan['decision_reason']}")
        print(f"  Project: {project_name}")
        print(f"  Focus: {focus_area}")
        print()
        _notify(instance, f"🚀 Run {run_num}/{max_runs} — Autonomous: {autonomous_mode} mode on {project_name}")

    # Build prompt
    from app.prompt_builder import build_agent_prompt
    prompt = build_agent_prompt(
        instance=instance,
        project_name=project_name,
        project_path=project_path,
        run_num=run_num,
        max_runs=max_runs,
        autonomous_mode=autonomous_mode or "implement",
        focus_area=focus_area or "General autonomous work",
        available_pct=available_pct or 50,
        mission_title=mission_title,
    )

    # Create pending.md
    from app.loop_manager import create_pending_file
    try:
        create_pending_file(
            instance_dir=instance,
            project_name=project_name,
            run_num=run_num,
            max_runs=max_runs,
            autonomous_mode=autonomous_mode or "implement",
            mission_title=mission_title,
        )
    except Exception as e:
        log("error", f"Failed to create pending.md: {e}")

    # Execute Claude
    if mission_title:
        set_status(koan_root, f"Run {run_num}/{max_runs} — executing mission on {project_name}")
    else:
        set_status(koan_root, f"Run {run_num}/{max_runs} — {autonomous_mode.upper()} on {project_name}")

    mission_start = int(time.time())
    fd_out, stdout_file = tempfile.mkstemp(prefix="koan-out-")
    fd_err, stderr_file = tempfile.mkstemp(prefix="koan-err-")
    os.close(fd_out)
    os.close(fd_err)
    try:
        # Build CLI command (provider-agnostic with per-project overrides)
        from app.mission_runner import build_mission_command
        from app.debug import debug_log as _debug_log
        cmd = build_mission_command(
            prompt=prompt,
            autonomous_mode=autonomous_mode,
            extra_flags="",
            project_name=project_name,
        )

        cmd_display = [c[:100] + '...' if len(c) > 100 else c for c in cmd[:6]]
        _debug_log(f"[run] cli: cmd={' '.join(cmd_display)}... cwd={project_path}")
        claude_exit = run_claude_task(cmd, stdout_file, stderr_file, cwd=project_path)
        _debug_log(f"[run] cli: exit_code={claude_exit}")

        # Parse and display output
        try:
            from app.mission_runner import parse_claude_output
            with open(stdout_file) as f:
                raw = f.read()
            text = parse_claude_output(raw)
            print(text)
        except Exception as e:
            try:
                with open(stdout_file) as f:
                    print(f.read())
            except Exception as e2:
                log("error", f"Failed to read CLI output: {e}, {e2}")
        _reset_terminal()

        # Complete/fail mission in missions.md (safety net — idempotent if Claude already did it)
        # Done BEFORE post-mission pipeline so quota exhaustion can't skip it.
        if mission_title:
            _finalize_mission(instance, mission_title, project_name, claude_exit)

        # Post-mission pipeline
        set_status(koan_root, f"Run {run_num}/{max_runs} — post-mission processing")
        try:
            from app.mission_runner import run_post_mission
            post_result = run_post_mission(
                instance_dir=instance,
                project_name=project_name,
                project_path=project_path,
                run_num=run_num,
                exit_code=claude_exit,
                stdout_file=stdout_file,
                stderr_file=stderr_file,
                mission_title=mission_title,
                autonomous_mode=autonomous_mode or "implement",
                start_time=mission_start,
            )

            if post_result.get("pending_archived"):
                log("health", "pending.md archived to journal (Claude didn't clean up)")
            if post_result.get("auto_merge_branch"):
                log("git", f"Auto-merge checked for {post_result['auto_merge_branch']}")

            if post_result.get("quota_exhausted"):
                # quota_info is a (reset_display, resume_message) tuple
                quota_info = post_result.get("quota_info")
                if quota_info and isinstance(quota_info, (list, tuple)) and len(quota_info) >= 2:
                    reset_display, resume_msg = quota_info[0], quota_info[1]
                else:
                    reset_display, resume_msg = "", "Auto-resume in ~5h"
                log("quota", f"Quota reached. {reset_display}")
                _commit_instance(instance, f"koan: quota exhausted {time.strftime('%Y-%m-%d-%H:%M')}")
                _notify(instance, (
                    f"⚠️ Claude quota exhausted. {reset_display}\n\n"
                    f"Kōan paused after {count} runs. {resume_msg} or use /resume to restart manually."
                ))
                return
        except Exception as e:
            log("error", f"Post-mission processing error: {e}")
    finally:
        _cleanup_temp(stdout_file, stderr_file)

    # Report result — always notify on completion (success or failure)
    if claude_exit == 0:
        log("mission", f"Run {run_num}/{max_runs} — [{project_name}] completed successfully")
    _notify_mission_end(
        instance, project_name, run_num, max_runs,
        claude_exit, mission_title,
    )

    # Commit instance
    _commit_instance(instance)

    # Periodic git sync
    if (count + 1) % git_sync_interval == 0:
        with protected_phase("Git sync"):
            log("git", f"Periodic git sync (run {count + 1})...")
            from app.git_sync import GitSync
            for name, path in projects:
                try:
                    gs = GitSync(instance, name, path)
                    gs.sync_and_report()
                except Exception as e:
                    log("error", f"Periodic git sync failed for {name}: {e}")

    # Max runs check
    if count + 1 >= max_runs:
        log("koan", f"Max runs ({max_runs}) reached. Running evening ritual before pause.")
        with protected_phase("Evening ritual"):
            try:
                from app.rituals import run_ritual
                run_ritual("evening", Path(instance))
            except Exception as e:
                log("error", f"Evening ritual failed: {e}")
        log("pause", "Entering pause mode (auto-resume in 5h).")
        from app.pause_manager import create_pause
        create_pause(koan_root, "max_runs")
        _notify(instance, (
            f"⏸️ Kōan paused: {max_runs} runs completed. "
            "Auto-resume in 5h or use /resume to restart."
        ))
        return

    # Sleep between runs (skip if pending missions)
    if check_pending_missions(instance):
        log("koan", "Pending missions found — skipping sleep, starting next run immediately")
        set_status(koan_root, f"Run {run_num}/{max_runs} — done, next run starting")
    else:
        set_status(koan_root, f"Idle — sleeping {interval}s ({time.strftime('%H:%M')})")
        log("koan", f"Sleeping {interval}s (checking for new missions every 10s)...")
        with protected_phase("Sleeping between runs"):
            wake = interruptible_sleep(interval, koan_root, instance)
        if wake == "mission":
            log("koan", "New mission detected during sleep — waking up early")
            set_status(koan_root, f"Run {run_num}/{max_runs} — done, new mission detected")


# ---------------------------------------------------------------------------
# Error recovery
# ---------------------------------------------------------------------------

def _handle_iteration_error(
    error: Exception,
    consecutive_errors: int,
    koan_root: str,
    instance: str,
):
    """Handle an exception from _run_iteration.

    Logs the error, backs off with increasing sleep, and enters
    pause mode after MAX_CONSECUTIVE_ERRORS to avoid thrashing.
    """
    tb = traceback.format_exc()
    log("error", f"Iteration failed ({consecutive_errors}/{MAX_CONSECUTIVE_ERRORS}): {error}")
    log("error", f"Traceback:\n{tb}")
    set_status(koan_root, f"Error recovery ({consecutive_errors}/{MAX_CONSECUTIVE_ERRORS})")

    # Notify on first error and periodically
    if _should_notify_error(consecutive_errors):
        _notify(instance, (
            f"⚠️ Run loop error ({consecutive_errors}/{MAX_CONSECUTIVE_ERRORS}): "
            f"{type(error).__name__}: {error}"
        ))

    if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
        log("error", f"Too many consecutive errors ({consecutive_errors}). Entering pause mode.")
        _notify(instance, (
            f"🛑 Kōan entering pause mode after {consecutive_errors} consecutive errors.\n"
            f"Last error: {type(error).__name__}: {error}\n"
            f"Use /resume to restart."
        ))
        from app.pause_manager import create_pause
        create_pause(koan_root, "errors")
        return

    # Backoff with increasing delay
    backoff = _calculate_backoff(consecutive_errors, MAX_BACKOFF_ITERATION)
    log("koan", f"Recovering in {backoff}s...")
    time.sleep(backoff)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compute_quota_reset_ts(instance: str):
    """Compute quota reset timestamp and display string.

    Returns (reset_ts: int, reset_display: str). Falls back to
    QUOTA_RETRY_SECONDS from now if estimation fails.
    """
    reset_ts = None
    reset_display = ""
    try:
        from app.usage_estimator import cmd_reset_time, _estimate_reset_time, _load_state
        usage_state_path = Path(instance, "usage_state.json")
        reset_ts = cmd_reset_time(usage_state_path)
        state = _load_state(usage_state_path)
        reset_display = f"session reset in ~{_estimate_reset_time(state.get('session_start', ''), 5)}"
    except Exception as e:
        log("error", f"Reset time estimation failed: {e}")
    if reset_ts is None:
        from app.pause_manager import QUOTA_RETRY_SECONDS
        reset_ts = int(time.time()) + QUOTA_RETRY_SECONDS
    return reset_ts, reset_display


def _compute_preflight_reset_ts(error_output: str):
    """Compute quota reset timestamp from preflight probe error output.

    Returns (reset_ts: int, reset_display: str). Falls back to
    QUOTA_RETRY_SECONDS from now if extraction fails.
    """
    reset_ts = None
    reset_display = ""
    try:
        from app.quota_handler import extract_reset_info, parse_reset_time, compute_resume_info
        reset_info = extract_reset_info(error_output or "")
        reset_ts, reset_display = parse_reset_time(reset_info)
        reset_ts, _ = compute_resume_info(reset_ts, reset_display)
    except Exception as e:
        log("error", f"Pre-flight reset time extraction failed: {e}")
    if reset_ts is None:
        from app.pause_manager import QUOTA_RETRY_SECONDS
        reset_ts = int(time.time()) + QUOTA_RETRY_SECONDS
    return reset_ts, reset_display


def _reset_usage_session(instance: str):
    """Reset internal usage session counters after resume.

    Ensures the usage estimator starts fresh so it doesn't
    re-pause immediately with stale high usage from the
    exhausted session.
    """
    try:
        from app.usage_estimator import cmd_reset_session
        usage_state = Path(instance, "usage_state.json")
        usage_md = Path(instance, "usage.md")
        cmd_reset_session(usage_state, usage_md)
        log("health", "Usage session counters reset after resume")
    except Exception as e:
        log("error", f"Usage session reset failed: {e}")


def _start_mission_in_file(instance: str, mission_title: str):
    """Move mission from Pending to In Progress via locked write."""
    try:
        from app.missions import start_mission
        from app.utils import modify_missions_file
        missions_path = Path(instance, "missions.md")
        if not missions_path.exists():
            return
        modify_missions_file(missions_path, lambda c: start_mission(c, mission_title))
    except Exception as e:
        log("error", f"Could not start mission in missions.md: {e}")


def _update_mission_in_file(instance: str, mission_title: str, *, failed: bool = False):
    """Move mission from Pending/In Progress to Done/Failed via locked write."""
    try:
        from app.missions import complete_mission, fail_mission
        from app.utils import modify_missions_file
        missions_path = Path(instance, "missions.md")
        if not missions_path.exists():
            return
        transform = fail_mission if failed else complete_mission
        before = [None]

        def tracked(content):
            before[0] = content
            return transform(content, mission_title)

        after = modify_missions_file(missions_path, tracked)
        if before[0] is not None and after == before[0]:
            log("warning", f"Mission not found (no change): {mission_title[:80]}")
    except Exception as e:
        label = "fail" if failed else "complete"
        log("error", f"Could not {label} mission in missions.md: {e}")


def _finalize_mission(instance: str, mission_title: str, project_name: str, exit_code: int):
    """Complete or fail a mission and record execution history."""
    _update_mission_in_file(instance, mission_title, failed=(exit_code != 0))
    try:
        from app.mission_history import record_execution
        record_execution(instance, mission_title, project_name, exit_code)
    except (OSError, ValueError) as e:
        log("error", f"Mission history recording error: {e}")


def _get_koan_branch(koan_root: str) -> str:
    """Get the current branch of the koan repository.

    Returns the branch name, or "" on error.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=koan_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def _restore_koan_branch(koan_root: str, expected_branch: str):
    """Restore the koan repo to the expected branch if it drifted.

    Skills like /rebase and /recreate do git checkouts on their
    project_path.  When project_path is the koan repo itself, a
    crash in the skill can leave the working tree on the wrong
    branch, breaking all subsequent module lookups.
    """
    if not expected_branch:
        return
    current = _get_koan_branch(koan_root)
    if current and current != expected_branch:
        from app.debug import debug_log
        debug_log(
            f"[run] koan branch drifted: {current} -> restoring {expected_branch}"
        )
        log("git", f"Restoring koan branch: {current} -> {expected_branch}")
        try:
            subprocess.run(
                ["git", "checkout", expected_branch],
                cwd=koan_root,
                capture_output=True,
                timeout=10,
            )
        except Exception as e:
            log("error", f"Failed to restore koan branch: {e}")


def _run_skill_mission(
    skill_cmd: list,
    koan_root: str,
    instance: str,
    project_name: str,
    project_path: str,
    run_num: int,
    mission_title: str,
    autonomous_mode: str,
) -> int:
    """Execute a skill-dispatched mission directly via subprocess.

    Returns the process exit code (0 = success).
    """
    from app.debug import debug_log

    mission_start = int(time.time())
    koan_pkg_dir = os.path.join(koan_root, "koan")

    # Explicitly set PYTHONPATH so the subprocess can always resolve
    # app.* modules even if the working tree changes (e.g. skill does
    # a git checkout on the koan repo itself).
    skill_env = {**os.environ, "PYTHONPATH": koan_pkg_dir}

    # Record the koan repo's HEAD before execution.  Skills like
    # /rebase and /recreate do git checkouts on project_path which
    # may be the koan repo itself — if they crash without restoring
    # the branch, subsequent runs break.
    koan_branch_before = _get_koan_branch(koan_root)

    debug_log(f"[run] skill exec: cmd={' '.join(skill_cmd)}")
    debug_log(f"[run] skill exec: cwd={koan_pkg_dir}")
    skill_stdout = ""
    skill_stderr = ""
    try:
        result = subprocess.run(
            skill_cmd,
            stdin=subprocess.DEVNULL,
            cwd=koan_pkg_dir,
            env=skill_env,
            capture_output=True,
            text=True,
            timeout=600,
            start_new_session=True,
        )
        exit_code = result.returncode
        skill_stdout = result.stdout or ""
        skill_stderr = result.stderr or ""
        debug_log(
            f"[run] skill exec: exit_code={exit_code} "
            f"stdout_len={len(skill_stdout)} stderr_len={len(skill_stderr)}"
        )
        if exit_code != 0:
            if skill_stdout:
                debug_log(f"[run] skill stdout: {skill_stdout[:2000]}")
            if skill_stderr:
                debug_log(f"[run] skill stderr: {skill_stderr[:2000]}")
        if skill_stdout:
            print(skill_stdout)
        if skill_stderr:
            print(skill_stderr, file=sys.stderr)
    except subprocess.TimeoutExpired:
        log("error", "Skill runner timed out (10min)")
        debug_log("[run] skill exec: TIMEOUT (600s)")
        exit_code = 1
    except Exception as e:
        log("error", f"Skill runner failed: {e}")
        debug_log(f"[run] skill exec: EXCEPTION {e}")
        exit_code = 1
    finally:
        _reset_terminal()
        # Restore koan repo branch if it was changed by the skill.
        _restore_koan_branch(koan_root, koan_branch_before)

    # Write output to temp files for post-mission processing
    fd_out, stdout_file = tempfile.mkstemp(prefix="koan-out-")
    fd_err, stderr_file = tempfile.mkstemp(prefix="koan-err-")
    try:
        os.write(fd_out, skill_stdout.encode('utf-8'))
        os.write(fd_err, skill_stderr.encode('utf-8'))
    finally:
        os.close(fd_out)
        os.close(fd_err)

    try:
        from app.mission_runner import run_post_mission
        run_post_mission(
            instance_dir=instance,
            project_name=project_name,
            project_path=project_path,
            run_num=run_num,
            exit_code=exit_code,
            stdout_file=stdout_file,
            stderr_file=stderr_file,
            mission_title=mission_title,
            autonomous_mode=autonomous_mode or "implement",
            start_time=mission_start,
        )
    except Exception as e:
        log("error", f"Post-mission error: {e}")

    _cleanup_temp(stdout_file, stderr_file)
    duration = int(time.time()) - mission_start
    debug_log(f"[run] skill exec: done in {duration}s, exit_code={exit_code}")
    return exit_code


def _cleanup_temp(*files):
    """Remove temporary files."""
    for f in files:
        try:
            Path(f).unlink(missing_ok=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Entry point with restart wrapper
# ---------------------------------------------------------------------------

def main():
    """Entry point with restart wrapper (replaces bash outer loop).

    Handles four exit modes:
    - Normal exit (break)
    - CTRL-C (KeyboardInterrupt → break)
    - Restart signal (SystemExit(42) → restart)
    - Unexpected crash (Exception → restart with backoff)
    """
    crash_count = 0
    while True:
        try:
            main_loop()
            break  # Normal exit
        except KeyboardInterrupt:
            break
        except SystemExit as e:
            if e.code == 42:
                # Restart signal
                crash_count = 0
                print("[koan] Restarting run loop...")
                time.sleep(1)
                continue
            raise
        except Exception:
            crash_count += 1
            tb = traceback.format_exc()
            print(f"[koan] Unexpected crash ({crash_count}/{MAX_MAIN_CRASHES}): {tb}", file=sys.stderr)

            if crash_count >= MAX_MAIN_CRASHES:
                print(f"[koan] Too many crashes ({MAX_MAIN_CRASHES}). Giving up.", file=sys.stderr)
                break

            backoff = _calculate_backoff(crash_count, MAX_BACKOFF_MAIN)
            print(f"[koan] Restarting in {backoff}s...", file=sys.stderr)
            time.sleep(backoff)

    _reset_terminal()


if __name__ == "__main__":
    main()
