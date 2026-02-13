"""
Kōan -- Shared helpers for the CI/CD pipeline.

Git operations, Claude Code CLI invocation, and text utilities
used by pr_review.py, rebase_pr.py, and other pipeline modules.
"""

import re
import subprocess
from typing import List, Optional

from app.cli_provider import build_full_command, run_command
from app.config import get_model_config
from app.git_utils import run_git_strict

# Backward-compatible alias — callers should import from app.cli_provider
run_claude_command = run_command


def _run_git(cmd: list, cwd: str = None, timeout: int = 60) -> str:
    """Run a git command, raise on failure.

    Thin wrapper around git_utils.run_git_strict() preserving the
    original interface where callers pass ["git", ...] as cmd.
    """
    # Strip leading "git" if present — run_git_strict prepends it
    args = cmd[1:] if cmd and cmd[0] == "git" else cmd
    return run_git_strict(*args, cwd=cwd, timeout=timeout)


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
                stdin=subprocess.DEVNULL,
                capture_output=True, cwd=project_path,
            )
    return None


def strip_cli_noise(text: str) -> str:
    """Strip Claude CLI error artifacts from output.

    The CLI appends lines like 'Error: Reached max turns (N)' to stdout
    even on successful runs. These pollute journal entries and reflections
    when the output is stored verbatim.

    Returns:
        Cleaned text with CLI noise removed.
    """
    lines = text.splitlines()
    lines = [l for l in lines if not re.match(r"^Error:.*max turns", l, re.IGNORECASE)]
    return "\n".join(lines).strip()


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
            stdin=subprocess.DEVNULL,
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

    _run_git(["git", "add", "-A"], cwd=project_path)
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
