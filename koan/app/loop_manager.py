"""
Koan -- Loop management utilities for the agent loop.

Data-processing and decision-making logic used by the main loop:
1. Project config validation and lookup
2. Autonomous mode focus area resolution
3. Pending.md file creation
4. Interruptible sleep logic with wake-on-mission

CLI interface:
    python -m app.loop_manager resolve-focus --mode <mode>
    python -m app.loop_manager create-pending --instance ... --project-name ...
    python -m app.loop_manager validate-projects
    python -m app.loop_manager lookup-project --name <name>
    python -m app.loop_manager interruptible-sleep --interval <seconds> --koan-root ...
"""

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.missions import count_pending


# --- Focus area resolution ---

# Maps autonomous mode to human-readable focus area description.
_FOCUS_AREAS = {
    "review": "Low-cost review: audit code, find issues, suggest improvements (READ-ONLY)",
    "implement": "Medium-cost implementation: prototype fixes, small improvements",
    "deep": "High-cost deep work: refactoring, architectural changes",
}


def resolve_focus_area(autonomous_mode: str, has_mission: bool = False) -> str:
    """Resolve the focus area description for a given autonomous mode.

    Args:
        autonomous_mode: Current mode (review/implement/deep/wait).
        has_mission: Whether a specific mission was assigned.

    Returns:
        Human-readable focus area string.
    """
    if has_mission:
        return "Execute assigned mission"
    return _FOCUS_AREAS.get(autonomous_mode, "General autonomous work")


# --- Project config validation and lookup ---


def validate_projects(
    projects: list, max_projects: int = 50
) -> Optional[str]:
    """Validate project configuration.

    Args:
        projects: List of (name, path) tuples.
        max_projects: Maximum allowed projects.

    Returns:
        Error message string if validation fails, None if valid.
    """
    if not projects:
        return "No projects configured. Create projects.yaml or set KOAN_PROJECTS env var."

    if len(projects) > max_projects:
        return f"Max {max_projects} projects allowed. You have {len(projects)}."

    for name, path in projects:
        if not os.path.isdir(path):
            return f"Project '{name}' path does not exist: {path}"

    return None


def lookup_project(project_name: str, projects: list) -> Optional[str]:
    """Find project path by name.

    Args:
        project_name: Name to look up.
        projects: List of (name, path) tuples.

    Returns:
        Project path if found, None otherwise.
    """
    for name, path in projects:
        if name == project_name:
            return path
    return None


def format_project_list(projects: list) -> str:
    """Format project names as a sorted bullet list.

    Args:
        projects: List of (name, path) tuples.

    Returns:
        Formatted string with bullet points, one per line.
    """
    return "\n".join(f"  \u2022 {name}" for name, _ in sorted(projects))


# --- Pending.md creation ---


def create_pending_file(
    instance_dir: str,
    project_name: str,
    run_num: int,
    max_runs: int,
    autonomous_mode: str,
    mission_title: str = "",
) -> str:
    """Create the pending.md progress journal file for a run.

    Args:
        instance_dir: Path to instance directory.
        project_name: Current project name.
        run_num: Current run number.
        max_runs: Maximum runs per session.
        autonomous_mode: Current autonomous mode.
        mission_title: Mission title (empty for autonomous runs).

    Returns:
        Path to the created pending.md file.
    """
    pending_path = Path(instance_dir) / "journal" / "pending.md"
    journal_dir = Path(instance_dir) / "journal" / datetime.now().strftime("%Y-%m-%d")
    journal_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if mission_title:
        header = f"# Mission: {mission_title}"
        mode = autonomous_mode if autonomous_mode else "mission"
    else:
        header = "# Autonomous run"
        mode = autonomous_mode

    content = f"""{header}
Project: {project_name}
Started: {now}
Run: {run_num}/{max_runs}
Mode: {mode}

---
"""
    pending_path.write_text(content)
    return str(pending_path)


# --- Interruptible sleep ---


def _check_signal_file(koan_root: str, filename: str) -> bool:
    """Check if a signal file (.koan-stop, .koan-pause, etc.) exists."""
    return os.path.isfile(os.path.join(koan_root, filename))


def _check_pending_missions(instance_dir: str) -> bool:
    """Check if there are pending missions in missions.md."""
    missions_path = Path(instance_dir) / "missions.md"
    if not missions_path.exists():
        return False
    try:
        return count_pending(missions_path.read_text()) > 0
    except Exception:
        return False


def interruptible_sleep(
    interval: int,
    koan_root: str,
    instance_dir: str,
    check_interval: int = 10,
) -> str:
    """Sleep for a given interval, waking early on events.

    Checks for stop, pause, restart, shutdown files and pending missions
    every check_interval seconds.

    Args:
        interval: Total sleep duration in seconds.
        koan_root: Path to koan root directory.
        instance_dir: Path to instance directory.
        check_interval: How often to check for wake events (seconds).

    Returns:
        Reason for waking: "timeout", "mission", "stop", "pause", "restart", "shutdown".
    """
    elapsed = 0
    while elapsed < interval:
        time.sleep(check_interval)
        elapsed += check_interval

        if _check_pending_missions(instance_dir):
            return "mission"
        if _check_signal_file(koan_root, ".koan-stop"):
            return "stop"
        if _check_signal_file(koan_root, ".koan-pause"):
            return "pause"
        if _check_signal_file(koan_root, ".koan-restart"):
            return "restart"
        if _check_signal_file(koan_root, ".koan-shutdown"):
            return "shutdown"

    return "timeout"


# --- CLI interface ---


def _cli_resolve_focus(args: list) -> None:
    """CLI: python -m app.loop_manager resolve-focus --mode <mode> [--has-mission]"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True)
    parser.add_argument("--has-mission", action="store_true")
    parsed = parser.parse_args(args)

    print(resolve_focus_area(parsed.mode, parsed.has_mission))


def _cli_create_pending(args: list) -> None:
    """CLI: python -m app.loop_manager create-pending ..."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance", required=True)
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--run-num", type=int, required=True)
    parser.add_argument("--max-runs", type=int, required=True)
    parser.add_argument("--autonomous-mode", default="implement")
    parser.add_argument("--mission-title", default="")
    parsed = parser.parse_args(args)

    path = create_pending_file(
        instance_dir=parsed.instance,
        project_name=parsed.project_name,
        run_num=parsed.run_num,
        max_runs=parsed.max_runs,
        autonomous_mode=parsed.autonomous_mode,
        mission_title=parsed.mission_title,
    )
    print(path)


def _cli_validate_projects(args: list) -> None:
    """CLI: python -m app.loop_manager validate-projects"""
    from app.utils import get_known_projects

    projects = get_known_projects()
    if not projects:
        print("No projects configured.", file=sys.stderr)
        sys.exit(1)

    error = validate_projects(projects)
    if error:
        print(error, file=sys.stderr)
        sys.exit(1)

    for name, path in projects:
        print(f"{name}:{path}")


def _cli_lookup_project(args: list) -> None:
    """CLI: python -m app.loop_manager lookup-project --name <name>"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parsed = parser.parse_args(args)

    from app.utils import get_known_projects

    projects = get_known_projects()
    path = lookup_project(parsed.name, projects)
    if path is None:
        print(f"Unknown project: {parsed.name}", file=sys.stderr)
        print(format_project_list(projects), file=sys.stderr)
        sys.exit(1)

    print(path)


def _cli_interruptible_sleep(args: list) -> None:
    """CLI: python -m app.loop_manager interruptible-sleep ..."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, required=True)
    parser.add_argument("--koan-root", required=True)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--check-interval", type=int, default=10)
    parsed = parser.parse_args(args)

    reason = interruptible_sleep(
        interval=parsed.interval,
        koan_root=parsed.koan_root,
        instance_dir=parsed.instance,
        check_interval=parsed.check_interval,
    )
    print(reason)


def main() -> None:
    """CLI entry point."""
    if len(sys.argv) < 2:
        print(
            "Usage: loop_manager.py <resolve-focus|create-pending|validate-projects|"
            "lookup-project|interruptible-sleep> [args]",
            file=sys.stderr,
        )
        sys.exit(1)

    subcommand = sys.argv[1]
    remaining = sys.argv[2:]

    commands = {
        "resolve-focus": _cli_resolve_focus,
        "create-pending": _cli_create_pending,
        "validate-projects": _cli_validate_projects,
        "lookup-project": _cli_lookup_project,
        "interruptible-sleep": _cli_interruptible_sleep,
    }

    handler = commands.get(subcommand)
    if handler is None:
        print(f"Unknown subcommand: {subcommand}", file=sys.stderr)
        sys.exit(1)

    handler(remaining)


if __name__ == "__main__":
    main()
