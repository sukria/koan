"""
Koan -- Shared helpers for the CI/CD pipeline.

Git operations, Claude Code CLI invocation, and text utilities
used by pr_review.py, rebase_pr.py, and other pipeline modules.
"""

import subprocess
from typing import List, Optional

from app.cli_provider import build_full_command
from app.utils import get_model_config


def _run_git(cmd: list, cwd: str = None, timeout: int = 60) -> str:
    """Run a git command, raise on failure."""
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git failed: {' '.join(cmd)} — {result.stderr[:200]}")
    return result.stdout.strip()


def _rebase_onto_target(base: str, project_path: str) -> Optional[str]:
    """Rebase onto target branch, trying origin then upstream.

    Returns:
        Remote name used (e.g. "origin" or "upstream") on success, None on failure.
    """
    for remote in ("origin", "upstream"):
        try:
            _run_git(["git", "fetch", remote, base], cwd=project_path)
            _run_git(
                ["git", "rebase", "--autostash", f"{remote}/{base}"],
                cwd=project_path,
            )
            return remote
        except Exception:
            subprocess.run(
                ["git", "rebase", "--abort"],
                capture_output=True, cwd=project_path,
            )
    return None


def _truncate(text: str, max_chars: int) -> str:
    """Truncate text with indicator."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...(truncated)"


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

    tools = ["Bash", "Read", "Write", "Glob", "Grep", "Edit"]
    if use_skill:
        tools.append("Skill")

    cmd = build_full_command(
        prompt=prompt,
        allowed_tools=tools,
        model=models["mission"],
        fallback=models["fallback"],
        max_turns=max_turns,
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
