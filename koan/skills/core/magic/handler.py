"""Skill handler for /magic — creative project exploration via Claude."""

import os
import random
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple


def handle(ctx):
    """Explore a random project and suggest creative improvement ideas."""
    projects = _get_projects(ctx)
    if not projects:
        return "No projects configured."

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
        from app.utils import get_fast_reply_model
        fast_model = get_fast_reply_model()
        cmd = ["claude", "-p", prompt, "--max-turns", "1"]
        if fast_model:
            cmd.extend(["--model", fast_model])
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=90,
            cwd=path,
        )
        if result.returncode == 0 and result.stdout.strip():
            return _clean_response(result.stdout.strip())
        else:
            if result.stderr:
                print(f"[skill:magic] Claude stderr: {result.stderr[:500]}")
            return f"Couldn't generate ideas for {name}. Try again later."
    except subprocess.TimeoutExpired:
        return "Timeout exploring. Try again."
    except Exception as e:
        print(f"[skill:magic] Error: {e}")
        return "Error during exploration. Try again."


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
