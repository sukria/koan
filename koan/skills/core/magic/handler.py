"""Skill handler for /magic — creative project exploration via Claude."""

import os
import random
import subprocess
from pathlib import Path
from typing import List, Tuple

from app.bridge_log import log


def handle(ctx):
    """Explore a project and suggest creative improvement ideas.

    Picks a random project if no argument given, or targets a specific
    project when called as /magic <project>.
    """
    projects = _get_projects(ctx)
    if not projects:
        return "No projects configured."

    target = ctx.args.strip().lower() if ctx.args else ""
    if target:
        name, path = _resolve_project(projects, target)
        if name is None:
            known = ", ".join(n for n, _ in projects)
            return f"Unknown project '{target}'. Known: {known}"
    else:
        name, path = random.choice(projects)

    if ctx.send_message:
        ctx.send_message(f"Exploring {name}...")

    git_activity = _gather_git_activity(path)
    project_structure = _gather_project_structure(path)
    missions_context = _get_missions_context(ctx.instance_dir)

    soul = ""
    soul_path = ctx.instance_dir / "soul.md"
    if soul_path.exists():
        soul = soul_path.read_text()

    from app.prompts import load_skill_prompt
    prompt = load_skill_prompt(
        Path(__file__).parent,
        "magic-explore",
        SOUL=soul,
        PROJECT_NAME=name,
        GIT_ACTIVITY=git_activity,
        PROJECT_STRUCTURE=project_structure,
        MISSIONS_CONTEXT=missions_context,
    )

    try:
        from app.config import get_fast_reply_model
        from app.cli_provider import build_full_command
        fast_model = get_fast_reply_model()
        cmd = build_full_command(
            prompt=prompt,
            max_turns=1,
            model=fast_model or "",
        )
        from app.cli_exec import run_cli
        result = run_cli(
            cmd,
            capture_output=True, text=True, timeout=90,
            cwd=path,
        )
        if result.returncode == 0 and result.stdout.strip():
            return _clean_response(result.stdout.strip())
        else:
            if result.stderr:
                log("error", f"Magic Claude stderr: {result.stderr[:500]}")
            return f"Couldn't generate ideas for {name}. Try again later."
    except subprocess.TimeoutExpired:
        return "Timeout exploring. Try again."
    except Exception as e:
        log("error", f"Magic error: {e}")
        return "Error during exploration. Try again."


def _resolve_project(
    projects: List[Tuple[str, str]], target: str
) -> Tuple[str, str]:
    """Resolve a project by name. Returns (name, path) or (None, None)."""
    for name, path in projects:
        if name.lower() == target:
            return name, path
    return None, None


def _get_projects(ctx) -> List[Tuple[str, str]]:
    """Get list of (name, path) for project exploration."""
    from app.utils import get_known_projects
    try:
        return [(n, p) for n, p in get_known_projects() if Path(p).is_dir()]
    except Exception:
        return []


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


def _clean_response(text: str) -> str:
    """Clean Claude CLI output for Telegram delivery."""
    import re
    lines = text.splitlines()
    lines = [l for l in lines if not re.match(r'^Error:.*max turns', l, re.IGNORECASE)]
    cleaned = "\n".join(lines).strip()
    cleaned = cleaned.replace("```", "")
    cleaned = cleaned.replace("**", "")
    cleaned = cleaned.replace("__", "")
    cleaned = cleaned.replace("~~", "")
    cleaned = re.sub(r'^#{1,6}\s+', '', cleaned, flags=re.MULTILINE)
    if len(cleaned) > 2000:
        cleaned = cleaned[:1997] + "..."
    return cleaned.strip()
