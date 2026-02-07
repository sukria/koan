"""
Koan -- Shared helpers for invoking Claude Code CLI.

Extracted from pr_review.py for reuse by rebase_pr.py and other modules
that need to run Claude as a subprocess and commit the results.
"""

import subprocess
from typing import List

from app.utils import get_model_config, build_claude_flags


def _run_git(cmd: list, cwd: str = None, timeout: int = 60) -> str:
    """Run a git command, raise on failure."""
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git failed: {' '.join(cmd)} — {result.stderr[:200]}")
    return result.stdout.strip()


def run_claude(cmd: list, cwd: str, timeout: int = 600) -> dict:
    """Run a Claude Code CLI command.

    Returns:
        Dict with keys: success (bool), output (str), error (str).
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            timeout=timeout, cwd=cwd,
        )
        if result.returncode != 0:
            stderr_snippet = result.stderr[-500:] if result.stderr else "no stderr"
            return {
                "success": False,
                "output": result.stdout.strip(),
                "error": f"Exit code {result.returncode}: {stderr_snippet}",
            }
        return {
            "success": True,
            "output": result.stdout.strip(),
            "error": "",
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "output": "",
            "error": f"Timeout ({timeout}s)",
        }


def commit_if_changes(project_path: str, message: str) -> bool:
    """Stage all changes and commit if there are any.

    Returns True if a commit was created.
    """
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True, text=True, cwd=project_path,
    )
    if not status.stdout.strip():
        return False

    _run_git(["git", "add", "-u"], cwd=project_path)
    _run_git(["git", "commit", "-m", message], cwd=project_path)
    return True


def run_claude_step(
    prompt: str,
    project_path: str,
    commit_msg: str,
    success_label: str,
    failure_label: str,
    actions_log: List[str],
    max_turns: int = 20,
    timeout: int = 600,
    use_skill: bool = False,
) -> bool:
    """Run a Claude Code step: invoke CLI, commit changes, log result.

    Args:
        use_skill: If True, include the Skill tool in allowed tools
                   so Claude can invoke registered skills (e.g. /refactor).

    Returns True if the step produced a commit.
    """
    models = get_model_config()
    flags = build_claude_flags(
        model=models["mission"], fallback=models["fallback"]
    )

    tools = "Bash,Read,Write,Glob,Grep,Edit"
    if use_skill:
        tools += ",Skill"

    cmd = (
        ["claude", "-p", prompt,
         "--allowedTools", tools,
         "--max-turns", str(max_turns)]
        + flags
    )

    result = run_claude(cmd, project_path, timeout=timeout)
    if result["success"]:
        committed = commit_if_changes(project_path, commit_msg)
        if committed and success_label:
            actions_log.append(success_label)
            return True
    elif failure_label:
        actions_log.append(f"{failure_label}: {result['error'][:200]}")
    return False
