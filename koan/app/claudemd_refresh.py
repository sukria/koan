"""
Koan -- CLAUDE.md refresh pipeline.

Analyzes recent commits in a project and updates (or creates) the CLAUDE.md
file with architecturally significant changes.

Usage:
    python -m app.claudemd_refresh <project_path> [--project-name NAME]

Pipeline:
1. Detect whether CLAUDE.md exists in the project
2. Gather git context (commits since last CLAUDE.md modification)
3. Build a selective prompt focused on architectural changes
4. Invoke Claude CLI to update/create CLAUDE.md
"""

import argparse
import subprocess
import sys
from pathlib import Path


def _git_last_modified(project_path: str, filepath: str) -> str:
    """Get the ISO date of the last commit that modified a file.

    Returns empty string if the file has never been committed.
    """
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%aI", "--", filepath],
            capture_output=True, text=True, cwd=project_path, timeout=30,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _git_log_since(project_path: str, since_date: str, max_commits: int = 50) -> str:
    """Get git log since a date, formatted for context.

    Returns a concise log of commits with their short descriptions.
    """
    cmd = [
        "git", "log",
        f"--since={since_date}",
        f"-n{max_commits}",
        "--format=%h %s",
        "--no-merges",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=project_path, timeout=30,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _git_diff_stat_since(project_path: str, since_date: str) -> str:
    """Get diffstat of changes since a date (files changed, not content).

    Returns the --stat output showing which files were touched.
    """
    # Find the oldest commit since that date
    cmd = [
        "git", "log",
        f"--since={since_date}",
        "--format=%H",
        "--reverse",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=project_path, timeout=30,
        )
        commits = result.stdout.strip().splitlines()
        if not commits:
            return ""
        oldest = commits[0]
        # Get diffstat from that commit's parent to HEAD
        stat_result = subprocess.run(
            ["git", "diff", "--stat", f"{oldest}~1..HEAD"],
            capture_output=True, text=True, cwd=project_path, timeout=30,
        )
        return stat_result.stdout.strip()
    except Exception:
        return ""


def _git_log_full(project_path: str, max_commits: int = 30) -> str:
    """Get recent git log for INIT mode (no CLAUDE.md exists yet)."""
    cmd = [
        "git", "log",
        f"-n{max_commits}",
        "--format=%h %s",
        "--no-merges",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=project_path, timeout=30,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def build_git_context(project_path: str, claude_md_exists: bool) -> str:
    """Build the git context section for the prompt."""
    if not claude_md_exists:
        recent = _git_log_full(project_path)
        if recent:
            return f"No CLAUDE.md exists yet. Recent project history:\n\n```\n{recent}\n```"
        return "No CLAUDE.md exists yet. No git history available."

    last_modified = _git_last_modified(project_path, "CLAUDE.md")
    if not last_modified:
        # CLAUDE.md exists but was never committed (maybe .gitignored or new)
        recent = _git_log_full(project_path, max_commits=20)
        return (
            "CLAUDE.md exists but has no git history (uncommitted or new).\n"
            f"Recent commits:\n\n```\n{recent}\n```"
        )

    commits = _git_log_since(project_path, last_modified)
    if not commits:
        return (
            f"CLAUDE.md was last updated: {last_modified}\n\n"
            "No new commits since then. CLAUDE.md is up to date."
        )

    diffstat = _git_diff_stat_since(project_path, last_modified)

    context = f"CLAUDE.md was last updated: {last_modified}\n\n"
    context += f"Commits since then ({len(commits.splitlines())} total):\n\n```\n{commits}\n```"
    if diffstat:
        context += f"\n\nFiles changed (diffstat):\n\n```\n{diffstat}\n```"
    return context


def run_refresh(project_path: str, project_name: str) -> int:
    """Run the CLAUDE.md refresh pipeline.

    Returns:
        0 on success, 1 on failure.
    """
    from app.claude_step import run_claude
    from app.cli_provider import build_full_command
    from app.prompts import load_skill_prompt
    from app.utils import get_model_config

    project_path = str(Path(project_path).resolve())
    claude_md = Path(project_path) / "CLAUDE.md"
    claude_md_exists = claude_md.exists()

    # Determine mode
    if claude_md_exists:
        mode = "UPDATE"
        mode_instructions = (
            "CLAUDE.md already exists. Analyze the recent commits to find "
            "architecturally significant changes and update the file accordingly. "
            "Make minimal, surgical edits. If nothing significant changed, say so."
        )
    else:
        mode = "INIT"
        mode_instructions = (
            "No CLAUDE.md exists yet. Explore the project structure, build system, "
            "and codebase to create a comprehensive but concise CLAUDE.md from scratch. "
            "Focus on: what the project does, how to build/test/run it, key architecture, "
            "and important conventions."
        )

    # Gather git context
    git_context = build_git_context(project_path, claude_md_exists)

    # Check if there's nothing to do
    if claude_md_exists and "No new commits since then" in git_context:
        print("CLAUDE.md is up to date — no new commits since last update.")
        return 0

    # Build prompt
    skill_dir = Path(__file__).parent.parent / "skills" / "core" / "claudemd"
    prompt = load_skill_prompt(
        skill_dir, "refresh-claude-md",
        MODE=mode,
        MODE_INSTRUCTIONS=mode_instructions,
        PROJECT_PATH=project_path,
        PROJECT_NAME=project_name,
        GIT_CONTEXT=git_context,
    )

    # Build CLI command
    models = get_model_config()
    cmd = build_full_command(
        prompt=prompt,
        allowed_tools=["Bash", "Read", "Write", "Edit", "Glob", "Grep"],
        model=models.get("mission", ""),
        fallback=models.get("fallback", ""),
        max_turns=10,
    )

    # Run Claude
    result = run_claude(cmd, project_path, timeout=300)

    if result["success"]:
        print(result["output"])
        return 0

    print(f"CLAUDE.md refresh failed: {result['error']}", file=sys.stderr)
    return 1


def main():
    parser = argparse.ArgumentParser(
        description="Refresh or create CLAUDE.md for a project",
    )
    parser.add_argument("project_path", help="Path to the project directory")
    parser.add_argument(
        "--project-name", default="",
        help="Project name (defaults to directory basename)",
    )
    args = parser.parse_args()

    project_name = args.project_name or Path(args.project_path).name

    sys.exit(run_refresh(args.project_path, project_name))


if __name__ == "__main__":
    main()
