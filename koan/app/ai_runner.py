"""
Koan -- AI exploration runner.

Gathers project context and runs Claude to suggest creative improvements.
Extracted from the /ai skill handler so it can run as a queued mission
via run.py instead of inlining the full prompt into missions.md.

CLI:
    python3 -m app.ai_runner --project-path <path> --project-name <name> \
        --instance-dir <dir>
"""

from pathlib import Path
from typing import Optional, Tuple

from app.prompts import load_skill_prompt


def run_exploration(
    project_path: str,
    project_name: str,
    instance_dir: str,
    notify_fn=None,
    skill_dir: Optional[Path] = None,
) -> Tuple[bool, str]:
    """Execute an AI exploration of a project.

    Gathers git activity, project structure, and missions context, then
    runs Claude to suggest creative improvements.

    Returns:
        (success, summary) tuple.
    """
    if notify_fn is None:
        from app.notify import send_telegram
        notify_fn = send_telegram

    notify_fn(f"Exploring {project_name}...")

    # Gather context
    git_activity = _gather_git_activity(project_path)
    project_structure = _gather_project_structure(project_path)
    missions_context = _get_missions_context(Path(instance_dir))

    # Build prompt from skill template
    if skill_dir is None:
        skill_dir = (
            Path(__file__).resolve().parent.parent / "skills" / "core" / "ai"
        )

    prompt = load_skill_prompt(
        skill_dir,
        "ai-explore",
        PROJECT_NAME=project_name,
        GIT_ACTIVITY=git_activity,
        PROJECT_STRUCTURE=project_structure,
        MISSIONS_CONTEXT=missions_context,
    )

    # Run Claude
    try:
        from app.cli_provider import run_command
        result = run_command(
            prompt, project_path,
            allowed_tools=["Read", "Glob", "Grep", "Bash"],
            max_turns=5, timeout=600,
        )
    except Exception as e:
        return False, f"Exploration failed: {str(e)[:300]}"

    if not result:
        return False, "Claude returned an empty exploration result."

    # Send result to Telegram (truncated)
    cleaned = _clean_response(result)
    notify_fn(f"AI exploration of {project_name}:\n\n{cleaned}")

    return True, f"Exploration of {project_name} completed."


def _gather_git_activity(project_path: str) -> str:
    """Gather recent git activity for a project."""
    from app.git_sync import run_git

    parts = []

    commits = run_git(project_path, "log", "--oneline", "-15", "--no-merges")
    if commits:
        parts.append("Recent commits:\n" + commits)

    branches_out = run_git(
        project_path, "branch", "-r", "--sort=-committerdate",
        "--format=%(refname:short)",
    )
    if branches_out:
        branches = branches_out.split("\n")[:10]
        parts.append("Active branches:\n" + "\n".join(branches))

    diff_stat = run_git(project_path, "diff", "--stat", "HEAD~10", "HEAD")
    if diff_stat:
        parts.append("Recent changes:\n" + diff_stat)

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


def _clean_response(text: str) -> str:
    """Clean Claude CLI output for Telegram delivery."""
    import re

    lines = text.splitlines()
    lines = [
        line for line in lines
        if not re.match(r'^Error:.*max turns', line, re.IGNORECASE)
    ]
    cleaned = "\n".join(lines).strip()
    cleaned = cleaned.replace("```", "")
    cleaned = cleaned.replace("**", "")
    cleaned = cleaned.replace("__", "")
    cleaned = cleaned.replace("~~", "")
    cleaned = re.sub(r'^#{1,6}\s+', '', cleaned, flags=re.MULTILINE)
    if len(cleaned) > 2000:
        cleaned = cleaned[:1997] + "..."
    return cleaned.strip()


# ---------------------------------------------------------------------------
# CLI entry point -- python3 -m app.ai_runner
# ---------------------------------------------------------------------------

def main(argv=None):
    """CLI entry point for ai_runner.

    Returns exit code (0 = success, 1 = failure).
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Run AI exploration on a project and report findings."
    )
    parser.add_argument(
        "--project-path", required=True,
        help="Local path to the project repository",
    )
    parser.add_argument(
        "--project-name", required=True,
        help="Human-readable project name",
    )
    parser.add_argument(
        "--instance-dir", required=True,
        help="Path to the instance directory",
    )
    cli_args = parser.parse_args(argv)

    skill_dir = (
        Path(__file__).resolve().parent.parent / "skills" / "core" / "ai"
    )

    success, summary = run_exploration(
        project_path=cli_args.project_path,
        project_name=cli_args.project_name,
        instance_dir=cli_args.instance_dir,
        skill_dir=skill_dir,
    )
    print(summary)
    return 0 if success else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
