"""Skill handler for /ai — queue an AI exploration mission."""

import os
import random
import subprocess
from pathlib import Path
from typing import List, Tuple


def handle(ctx):
    """Queue an AI exploration mission for a project."""
    projects = _get_projects(ctx)
    if not projects:
        return "No projects configured."

    # Pick project: from args or random
    target = ctx.args.strip().lower() if ctx.args else ""
    name, path = _resolve_project(projects, target)
    if name is None:
        known = ", ".join(n for n, _ in projects)
        return f"Unknown project '{target}'. Known: {known}"

    # Gather context for the prompt
    git_activity = _gather_git_activity(path)
    project_structure = _gather_project_structure(path)
    missions_context = _get_missions_context(ctx.instance_dir)

    # Build the exploration prompt
    from app.prompts import load_skill_prompt
    prompt = load_skill_prompt(
        Path(__file__).parent,
        "ai-explore",
        PROJECT_NAME=name,
        GIT_ACTIVITY=git_activity,
        PROJECT_STRUCTURE=project_structure,
        MISSIONS_CONTEXT=missions_context,
    )

    # Queue as a mission
    from app.utils import insert_pending_mission
    mission_text = (
        f"[project:{name}] AI exploration — {prompt}"
    )
    missions_path = ctx.instance_dir / "missions.md"
    insert_pending_mission(missions_path, mission_text)

    return f"AI exploration queued for {name}"


def _get_projects(ctx) -> List[Tuple[str, str]]:
    """Get list of (name, path) for project exploration."""
    from app.utils import get_known_projects
    try:
        projects = get_known_projects()
        return [(name, path) for name, path in projects if Path(path).is_dir()]
    except Exception:
        pass

    project_path = os.environ.get("KOAN_PROJECT_PATH", "")
    if project_path and Path(project_path).is_dir():
        return [(Path(project_path).name, project_path)]

    return []


def _resolve_project(
    projects: List[Tuple[str, str]], target: str
) -> Tuple[str, str]:
    """Resolve a project by name or pick random.

    Returns (name, path) or (None, None) if target not found.
    """
    if not target:
        return random.choice(projects)

    for name, path in projects:
        if name.lower() == target:
            return name, path

    return None, None


def _get_missions_context(instance_dir: Path) -> str:
    """Get current missions context for the prompt."""
    missions_file = instance_dir / "missions.md"
    if not missions_file.exists():
        return "No active missions."

    from app.missions import parse_sections
    sections = parse_sections(missions_file.read_text())
    in_progress = sections.get("in_progress", [])
    pending = sections.get("pending", [])
    parts = []
    if in_progress:
        parts.append("In progress:\n" + "\n".join(in_progress[:5]))
    if pending:
        parts.append("Pending:\n" + "\n".join(pending[:5]))
    return "\n".join(parts) if parts else "No active missions."


def _gather_git_activity(project_path: str) -> str:
    """Gather recent git activity for a project."""
    parts = []
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "-15", "--no-merges"],
            capture_output=True, text=True, timeout=10,
            cwd=project_path,
        )
        if result.returncode == 0 and result.stdout.strip():
            parts.append("Recent commits:\n" + result.stdout.strip())

        result = subprocess.run(
            ["git", "branch", "-r", "--sort=-committerdate",
             "--format=%(refname:short)"],
            capture_output=True, text=True, timeout=10,
            cwd=project_path,
        )
        if result.returncode == 0 and result.stdout.strip():
            branches = result.stdout.strip().split("\n")[:10]
            parts.append("Active branches:\n" + "\n".join(branches))

        result = subprocess.run(
            ["git", "diff", "--stat", "HEAD~10", "HEAD"],
            capture_output=True, text=True, timeout=10,
            cwd=project_path,
        )
        if result.returncode == 0 and result.stdout.strip():
            parts.append("Recent changes:\n" + result.stdout.strip())

    except (subprocess.TimeoutExpired, Exception) as e:
        parts.append(f"(git activity unavailable: {e})")

    return "\n\n".join(parts) if parts else "No git activity available."


def _gather_project_structure(project_path: str) -> str:
    """Gather top-level project structure."""
    try:
        p = Path(project_path)
        entries = sorted(p.iterdir())
        dirs = [
            e.name + "/"
            for e in entries
            if e.is_dir() and not e.name.startswith(".")
        ]
        files = [
            e.name
            for e in entries
            if e.is_file() and not e.name.startswith(".")
        ]
        parts = []
        if dirs:
            parts.append("Directories: " + ", ".join(dirs[:20]))
        if files:
            parts.append("Files: " + ", ".join(files[:20]))
        return "\n".join(parts)
    except Exception:
        return "Structure unavailable."
