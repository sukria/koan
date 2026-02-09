"""Kōan — Main agent loop.

Manages the agent loop: mission picking, Claude CLI execution,
post-mission processing, pause/resume, signal handling, and
lifecycle notifications.

Usage:
    python -m app.run              # Normal start
    python -m app.run --restart    # Re-exec after restart signal (exit 42)

Features:
- Double-tap CTRL-C protection (first warns, second aborts)
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
from pathlib import Path
from typing import Optional

from app.contemplative_runner import should_run_contemplative
from app.loop_manager import resolve_focus_area
from app.pid_manager import acquire_pid, release_pid
from app.utils import get_contemplative_chance


# ---------------------------------------------------------------------------
# Colored logging
# ---------------------------------------------------------------------------

_COLORS = {}


def _init_colors():
    """Initialize ANSI color codes based on TTY detection."""
    global _COLORS
    if sys.stdout.isatty():
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


def bold_cyan(text: str) -> str:
    if not _COLORS:
        _init_colors()
    return f"{_COLORS.get('bold', '')}{_COLORS.get('cyan', '')}{text}{_COLORS.get('reset', '')}"


def bold_green(text: str) -> str:
    if not _COLORS:
        _init_colors()
    return f"{_COLORS.get('bold', '')}{_COLORS.get('green', '')}{text}{_COLORS.get('reset', '')}"


# ---------------------------------------------------------------------------
# Status file
# ---------------------------------------------------------------------------

def set_status(koan_root: str, message: str):
    """Write loop status for /status and dashboard."""
    try:
        Path(koan_root, ".koan-status").write_text(message)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Signal handling — double-tap CTRL-C
# ---------------------------------------------------------------------------

class SignalState:
    """Mutable state for SIGINT handler (double-tap pattern)."""
    task_running: bool = False
    first_ctrl_c: float = 0
    claude_proc: Optional[subprocess.Popen] = None
    timeout: int = 10


_sig = SignalState()


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
            log("koan", "Confirmed. Aborting task...")
            if _sig.claude_proc and _sig.claude_proc.poll() is None:
                _sig.claude_proc.terminate()
            _sig.first_ctrl_c = 0
            _sig.task_running = False
            raise KeyboardInterrupt

    # First CTRL-C (or timeout expired)
    _sig.first_ctrl_c = now
    print()
    log("koan", f"⚠️  A task is running. Press CTRL-C again within {_sig.timeout}s to abort.")


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

    with open(stdout_file, "w") as out_f, open(stderr_file, "w") as err_f:
        proc = subprocess.Popen(
            cmd,
            stdout=out_f,
            stderr=err_f,
            cwd=cwd,
            preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN),
        )
        _sig.claude_proc = proc

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
    except Exception:
        pass

    # Crash recovery
    log("health", "Checking for interrupted missions...")
    try:
        recover_missions(instance)
    except Exception:
        pass

    # Auto-migrate env vars to projects.yaml (one-shot, idempotent)
    try:
        from app.projects_migration import run_migration
        migration_msgs = run_migration(koan_root)
        for msg in migration_msgs:
            log("init", f"[migration] {msg}")
    except Exception:
        pass

    # Sanity checks (all modules in koan/sanity/, alphabetical order)
    log("health", "Running sanity checks...")
    try:
        from sanity import run_all
        for name, modified, changes in run_all(instance):
            if modified:
                for change in changes:
                    log("health", f"  [{name}] {change}")
    except Exception:
        pass

    # Memory cleanup
    log("health", "Running memory cleanup...")
    try:
        from app.memory_manager import run_cleanup
        run_cleanup(instance)
    except Exception:
        pass

    # Health check
    log("health", "Checking Telegram bridge health...")
    try:
        check_and_alert(koan_root, max_age=120)
    except Exception:
        pass

    # Self-reflection
    log("health", "Checking self-reflection trigger...")
    try:
        subprocess.run(
            [sys.executable, Path(koan_root, "koan/app/self_reflection.py").as_posix(),
             instance, "--notify"],
            capture_output=True, timeout=60,
        )
    except Exception:
        pass

    # Start on pause
    if get_start_on_pause() and not Path(koan_root, ".koan-pause").exists():
        log("pause", "start_on_pause=true in config. Entering pause mode.")
        Path(koan_root, ".koan-pause").touch()

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
    set_status(koan_root, "Starting up")
    log("init", f"Starting. Max runs: {max_runs}, interval: {interval}s")

    project_list = "\n".join(f"  • {n}" for n, _ in sorted(projects))
    pause_note = " Currently PAUSED." if Path(koan_root, ".koan-pause").exists() else ""
    _notify(instance, (
        f"Kōan starting — {max_runs} max runs, {interval}s interval.\n"
        f"Projects:\n{project_list}\n"
        f"Current: {projects[0][0]}.{pause_note}"
    ))

    # Git sync
    log("git", "Running git sync...")
    for name, path in projects:
        try:
            gs = GitSync(instance, name, path)
            gs.sync_and_report()
        except Exception:
            pass

    # Daily report
    try:
        subprocess.run(
            [sys.executable, Path(koan_root, "koan/app/daily_report.py").as_posix()],
            capture_output=True, timeout=60,
        )
    except Exception:
        pass

    # Morning ritual
    log("init", "Running morning ritual...")
    try:
        from app.rituals import run_ritual
        run_ritual("morning", Path(instance))
    except Exception:
        pass

    return max_runs, interval, branch_prefix


# ---------------------------------------------------------------------------
# Notify helper
# ---------------------------------------------------------------------------

def _notify(instance: str, message: str):
    """Send a formatted notification to Telegram."""
    try:
        from app.notify import format_and_send
        format_and_send(message, instance_dir=instance)
    except Exception:
        pass


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
    except Exception:
        pass


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
    except Exception:
        pass

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
        result = subprocess.run(
            [sys.executable, "-m", "app.focus_manager", "check", koan_root],
            capture_output=True, text=True, timeout=5,
        )
        in_focus = result.returncode == 0
    except Exception:
        pass

    if roll < 50 and not in_focus:
        log("pause", "A thought stirs...")
        project_name, project_path = projects[0]
        Path(koan_root, ".koan-project").write_text(project_name)

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
                cwd=instance,
            )
            log("pause", "Contemplative session ended.")
        except KeyboardInterrupt:
            raise
        except Exception as e:
            log("error", f"Contemplative session error: {e}")

    # Sleep 5 min in 5s increments — check for resume/restart
    for _ in range(60):
        if not Path(koan_root, ".koan-pause").exists():
            return "resume"
        if Path(koan_root, ".koan-restart").exists():
            break
        time.sleep(5)

    return None


# ---------------------------------------------------------------------------
# Iteration planning
# ---------------------------------------------------------------------------

def plan_iteration(
    instance: str, koan_root: str, projects: list,
    run_num: int, count: int, max_runs: int,
) -> dict:
    """Plan the next iteration: usage, mode, mission picking.

    Returns a dict with keys:
        action: mission|autonomous|contemplative|focus_wait|wait_pause|error
        project_name, project_path, mission_title, autonomous_mode,
        focus_area, available_pct, decision_reason, display_lines, etc.
    """
    from app.missions import count_pending
    from app.recurring import check_and_inject

    koan_projects = os.environ.get("KOAN_PROJECTS", "")
    usage_state = Path(instance, "usage_state.json").as_posix()
    usage_md = Path(instance, "usage.md")

    # Always refresh usage — critical after auto-resume so stale usage.md
    # is cleared and session resets are detected.
    try:
        subprocess.run(
            [sys.executable, Path(koan_root, "koan/app/usage_estimator.py").as_posix(),
             "refresh", usage_state, usage_md.as_posix()],
            capture_output=True, timeout=15,
        )
    except Exception:
        pass

    # Parse usage and decide mode
    autonomous_mode = "implement"
    available_pct = 50
    decision_reason = "Default mode"
    recommended_idx = 0
    display_lines = []

    try:
        result = subprocess.run(
            [sys.executable, Path(koan_root, "koan/app/usage_tracker.py").as_posix(),
             usage_md.as_posix(), str(count), koan_projects],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split(":")
            if len(parts) >= 4:
                autonomous_mode = parts[0]
                available_pct = int(parts[1])
                decision_reason = parts[2]
                recommended_idx = int(parts[3])
    except Exception:
        pass

    # Display usage
    if usage_md.exists():
        try:
            content = usage_md.read_text()
            for line in content.splitlines():
                if "session" in line.lower() or "weekly" in line.lower():
                    display_lines.append(line.strip())
                    if len(display_lines) >= 2:
                        break
        except Exception:
            pass

    # Recurring missions
    recurring_injected = []
    try:
        recurring_path = Path(instance, "recurring.json")
        missions_path = Path(instance, "missions.md")
        if recurring_path.exists():
            recurring_injected = check_and_inject(recurring_path, missions_path)
    except Exception:
        pass

    # Pick mission
    last_project = ""
    try:
        last_project = Path(koan_root, ".koan-project").read_text().strip()
    except Exception:
        pass

    mission_title = ""
    project_name = ""
    project_path = ""

    try:
        result = subprocess.run(
            [sys.executable, Path(koan_root, "koan/app/pick_mission.py").as_posix(),
             instance, koan_projects, str(run_num), autonomous_mode, last_project],
            capture_output=True, text=True, timeout=120,
        )
        if result.stderr:
            log("mission", f"Mission picker stderr: {result.stderr.strip()}")
        pick_result = result.stdout.strip()
        log("mission", f"Picker result: '{pick_result or '<empty>'}'")

        if pick_result and ":" in pick_result:
            project_name = pick_result.split(":")[0]
            mission_title = pick_result.split(":", 1)[1]
    except Exception as e:
        log("error", f"Mission picker failed: {e}")

    # Resolve project
    if mission_title and project_name:
        # Find path for picked project
        found = False
        for name, path in projects:
            if name == project_name:
                project_path = path
                found = True
                break

        if not found:
            known = "\n".join(f"  • {n}" for n, _ in sorted(projects))
            return {
                "action": "error",
                "error": f"Mission references unknown project: {project_name}\nKnown projects:\n{known}",
                "project_name": project_name,
                "project_path": "",
                "mission_title": mission_title,
                "autonomous_mode": autonomous_mode,
                "focus_area": "",
                "available_pct": available_pct,
                "decision_reason": decision_reason,
                "display_lines": display_lines,
                "recurring_injected": recurring_injected,
            }
    else:
        # Autonomous mode — use recommended project
        idx = min(recommended_idx, len(projects) - 1)
        project_name = projects[idx][0]
        project_path = projects[idx][1]
        mission_title = ""

    # Check contemplative mode (no mission, budget available)
    if not mission_title and autonomous_mode in ("deep", "implement"):
        contemplative_chance = get_contemplative_chance()

        # Check focus mode
        in_focus = False
        focus_remaining = ""
        try:
            result = subprocess.run(
                [sys.executable, "-m", "app.focus_manager", "check", koan_root],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                in_focus = True
                focus_remaining = result.stdout.strip()
        except Exception:
            pass

        if not in_focus:
            if should_run_contemplative(contemplative_chance):
                return {
                    "action": "contemplative",
                    "project_name": project_name,
                    "project_path": project_path,
                    "mission_title": "",
                    "autonomous_mode": autonomous_mode,
                    "focus_area": "",
                    "available_pct": available_pct,
                    "decision_reason": decision_reason,
                    "display_lines": display_lines,
                    "recurring_injected": recurring_injected,
                }

        # Focus mode active — wait for missions
        if in_focus:
            return {
                "action": "focus_wait",
                "project_name": project_name,
                "project_path": project_path,
                "mission_title": "",
                "autonomous_mode": autonomous_mode,
                "focus_area": "",
                "available_pct": available_pct,
                "decision_reason": decision_reason,
                "display_lines": display_lines,
                "recurring_injected": recurring_injected,
                "focus_remaining": focus_remaining,
            }

    # Handle WAIT mode
    if not mission_title and autonomous_mode == "wait":
        return {
            "action": "wait_pause",
            "project_name": project_name,
            "project_path": project_path,
            "mission_title": "",
            "autonomous_mode": autonomous_mode,
            "focus_area": "",
            "available_pct": available_pct,
            "decision_reason": decision_reason,
            "display_lines": display_lines,
            "recurring_injected": recurring_injected,
        }

    # Resolve focus area
    has_mission = bool(mission_title)
    focus_area = resolve_focus_area(autonomous_mode, has_mission)

    action = "mission" if mission_title else "autonomous"
    return {
        "action": action,
        "project_name": project_name,
        "project_path": project_path,
        "mission_title": mission_title,
        "autonomous_mode": autonomous_mode,
        "focus_area": focus_area,
        "available_pct": available_pct,
        "decision_reason": decision_reason,
        "display_lines": display_lines,
        "recurring_injected": recurring_injected,
    }


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

    # Acquire PID
    acquire_pid(Path(koan_root), "run", os.getpid())

    # Install SIGINT handler
    signal.signal(signal.SIGINT, _on_sigint)

    # Initialize project state
    Path(koan_root, ".koan-project").write_text(projects[0][0])
    os.environ["KOAN_CURRENT_PROJECT"] = projects[0][0]
    os.environ["KOAN_CURRENT_PROJECT_PATH"] = projects[0][1]

    count = 0
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

            # --- Restart check ---
            restart_file = Path(koan_root, ".koan-restart")
            if restart_file.exists():
                try:
                    mtime = restart_file.stat().st_mtime
                    if mtime > start_time:
                        log("koan", "Restart requested. Exiting for re-launch...")
                        sys.exit(42)
                except Exception:
                    pass

            # --- Pause mode ---
            if Path(koan_root, ".koan-pause").exists():
                result = handle_pause(koan_root, instance, projects, max_runs)
                if result == "resume":
                    count = 0
                continue

            # --- Active run ---
            run_num = count + 1
            set_status(koan_root, f"Run {run_num}/{max_runs} — preparing")
            print()
            print(bold_cyan(f"=== Run {run_num}/{max_runs} — {time.strftime('%Y-%m-%d %H:%M:%S')} ==="))

            # Plan iteration
            plan = plan_iteration(instance, koan_root, projects, run_num, count, max_runs)

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
                sys.exit(1)

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
                    run_claude_task(cmd, os.devnull, os.devnull, cwd=instance)
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    log("error", f"Contemplative error: {e}")
                log("pause", "Contemplative session ended.")

                count += 1
                # Check for pending before sleeping
                if _has_pending_missions(instance):
                    log("koan", "Pending missions found after contemplation — skipping sleep")
                else:
                    set_status(koan_root, f"Idle — post-contemplation sleep ({time.strftime('%H:%M')})")
                    log("pause", f"Contemplative session complete. Sleeping {interval}s...")
                    wake = _interruptible_sleep(interval, koan_root, instance)
                    if wake == "mission":
                        log("koan", "New mission detected during sleep — waking up early")
                continue

            if action == "focus_wait":
                remaining = plan.get("focus_remaining", "unknown")
                log("koan", f"Focus mode active ({remaining} remaining) — no missions pending, sleeping")
                set_status(koan_root, f"Focus mode — waiting for missions ({remaining} remaining)")
                wake = _interruptible_sleep(interval, koan_root, instance)
                if wake == "mission":
                    log("koan", "New mission detected during focus sleep — waking up")
                continue

            if action == "wait_pause":
                log("quota", "Decision: WAIT mode (budget exhausted)")
                print(f"  Reason: {plan['decision_reason']}")
                print("  Action: Entering pause mode (will auto-resume when quota resets)")
                print()
                try:
                    subprocess.run(
                        [sys.executable, Path(koan_root, "koan/app/send_retrospective.py").as_posix(),
                         instance, project_name],
                        capture_output=True, timeout=120,
                    )
                except Exception:
                    pass
                # Compute a proper future reset timestamp to avoid instant auto-resume
                reset_ts = None
                reset_display = ""
                try:
                    from app.usage_estimator import cmd_reset_time, _estimate_reset_time, _load_state
                    usage_state_path = Path(instance, "usage_state.json")
                    reset_ts = cmd_reset_time(usage_state_path)
                    # Build display info for the pause reason file
                    state = _load_state(usage_state_path)
                    reset_display = f"session reset in ~{_estimate_reset_time(state.get('session_start', ''), 5)}"
                except Exception:
                    pass
                if reset_ts is None:
                    reset_ts = int(time.time()) + 5 * 3600  # fallback: now + 5h
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
                continue

            # --- Execute mission or autonomous run ---
            mission_title = plan["mission_title"]
            autonomous_mode = plan["autonomous_mode"]
            focus_area = plan["focus_area"]
            available_pct = plan["available_pct"]

            # Set project state
            Path(koan_root, ".koan-project").write_text(project_name)
            os.environ["KOAN_CURRENT_PROJECT"] = project_name
            os.environ["KOAN_CURRENT_PROJECT_PATH"] = project_path

            print(bold_green(f">>> Current project: {project_name}") + f" ({project_path})")
            print()

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
            except Exception:
                pass

            # Execute Claude
            if mission_title:
                set_status(koan_root, f"Run {run_num}/{max_runs} — executing mission on {project_name}")
            else:
                set_status(koan_root, f"Run {run_num}/{max_runs} — {autonomous_mode.upper()} on {project_name}")

            mission_start = int(time.time())
            stdout_file = tempfile.mktemp(prefix="koan-out-")
            stderr_file = tempfile.mktemp(prefix="koan-err-")

            # Build CLI command
            from app.utils import get_claude_flags_for_role
            mission_flags_str = get_claude_flags_for_role("mission", autonomous_mode)
            cmd = ["claude", "-p", prompt,
                   "--allowedTools", "Bash,Read,Write,Glob,Grep,Edit",
                   "--output-format", "json"]
            if mission_flags_str:
                cmd.extend(mission_flags_str.split())

            claude_exit = run_claude_task(cmd, stdout_file, stderr_file, cwd=project_path)

            # Parse and display output
            try:
                from app.mission_runner import parse_claude_output
                with open(stdout_file) as f:
                    raw = f.read()
                text = parse_claude_output(raw)
                print(text)
            except Exception:
                try:
                    with open(stdout_file) as f:
                        print(f.read())
                except Exception:
                    pass

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
                    _cleanup_temp(stdout_file, stderr_file)
                    continue
            except Exception as e:
                log("error", f"Post-mission processing error: {e}")

            _cleanup_temp(stdout_file, stderr_file)

            # Report result
            if claude_exit == 0:
                log("mission", f"Run {run_num}/{max_runs} — [{project_name}] completed successfully")
            else:
                if mission_title:
                    _notify(instance, f"❌ Run {run_num}/{max_runs} — [{project_name}] Mission failed: {mission_title}")
                else:
                    _notify(instance, f"❌ Run {run_num}/{max_runs} — [{project_name}] Run failed")

            # Commit instance
            _commit_instance(instance)

            count += 1

            # Periodic git sync
            if count % git_sync_interval == 0:
                log("git", f"Periodic git sync (run {count})...")
                from app.git_sync import GitSync
                for name, path in projects:
                    try:
                        gs = GitSync(instance, name, path)
                        gs.sync_and_report()
                    except Exception:
                        pass

            # Max runs check
            if count >= max_runs:
                log("koan", f"Max runs ({max_runs}) reached. Running evening ritual before pause.")
                try:
                    from app.rituals import run_ritual
                    run_ritual("evening", Path(instance))
                except Exception:
                    pass
                log("pause", "Entering pause mode (auto-resume in 5h).")
                subprocess.run(
                    [sys.executable, "-m", "app.pause_manager", "create", koan_root, "max_runs"],
                    capture_output=True, timeout=10,
                )
                _notify(instance, (
                    f"⏸️ Kōan paused: {max_runs} runs completed. "
                    "Auto-resume in 5h or use /resume to restart."
                ))
                continue

            # Sleep between runs (skip if pending missions)
            if _has_pending_missions(instance):
                log("koan", "Pending missions found — skipping sleep, starting next run immediately")
                set_status(koan_root, f"Run {run_num}/{max_runs} — done, next run starting")
            else:
                set_status(koan_root, f"Idle — sleeping {interval}s ({time.strftime('%H:%M')})")
                log("koan", f"Sleeping {interval}s (checking for new missions every 10s)...")
                wake = _interruptible_sleep(interval, koan_root, instance)
                if wake == "mission":
                    log("koan", "New mission detected during sleep — waking up early")
                    set_status(koan_root, f"Run {run_num}/{max_runs} — done, new mission detected")

    except KeyboardInterrupt:
        current = "unknown"
        try:
            current = Path(koan_root, ".koan-project").read_text().strip()
        except Exception:
            pass
        _notify(instance, f"Kōan interrupted after {count} runs. Last project: {current}.")
    finally:
        # Cleanup
        Path(koan_root, ".koan-status").unlink(missing_ok=True)
        release_pid(Path(koan_root), "run")
        log("koan", f"Shutdown. {count} runs executed.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_pending_missions(instance: str) -> bool:
    """Quick check for pending missions."""
    try:
        from app.missions import count_pending
        missions_path = Path(instance, "missions.md")
        if not missions_path.exists():
            return False
        return count_pending(missions_path.read_text()) > 0
    except Exception:
        return False


def _interruptible_sleep(interval: int, koan_root: str, instance: str) -> str:
    """Sleep with interruption checks. Returns wake reason."""
    try:
        from app.loop_manager import interruptible_sleep
        return interruptible_sleep(interval, koan_root, instance)
    except Exception:
        time.sleep(min(interval, 30))
        return "timeout"


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
    except Exception:
        pass


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
    """Entry point with restart wrapper (replaces bash outer loop)."""
    while True:
        try:
            main_loop()
            break  # Normal exit
        except KeyboardInterrupt:
            break
        except SystemExit as e:
            if e.code == 42:
                # Restart signal
                print("[koan] Restarting run loop...")
                time.sleep(1)
                continue
            raise


if __name__ == "__main__":
    main()
