"""
Kōan -- Pre-mission git preparation.

Ensures a project starts each mission on a fresh, up-to-date base branch.
Called before every mission execution in the agent loop.

Two public functions:
- get_upstream_remote(): Determines the canonical remote for a project.
- prepare_project_branch(): Full pre-mission git state preparation.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from app.git_utils import run_git
from app.projects_config import (
    get_project_auto_merge,
    get_project_submit_to_repository,
    load_projects_config,
)

logger = logging.getLogger(__name__)


@dataclass
class PrepResult:
    """Result of pre-mission git preparation."""

    remote_used: str = "origin"
    base_branch: str = "main"
    stashed: bool = False
    previous_branch: str = ""
    success: bool = True
    error: Optional[str] = None


def get_upstream_remote(
    project_path: str, project_name: str, koan_root: str
) -> str:
    """Determine the canonical remote for a project.

    Resolution order:
    1. Explicit submit_to_repository.remote from projects.yaml
    2. 'upstream' remote if it exists (common fork pattern)
    3. 'origin' fallback (default for non-fork repos)
    """
    # 1. Check explicit config
    try:
        config = load_projects_config(koan_root)
        if config:
            submit_cfg = get_project_submit_to_repository(config, project_name)
            if submit_cfg.get("remote"):
                return submit_cfg["remote"]
    except Exception as e:
        logger.warning("config load error for remote: %s", e)

    # 2. Probe for 'upstream' remote
    rc, _, _ = run_git("remote", "get-url", "upstream", cwd=project_path)
    if rc == 0:
        return "upstream"

    # 3. Fall back to 'origin'
    return "origin"


def prepare_project_branch(
    project_path: str, project_name: str, koan_root: str
) -> PrepResult:
    """Prepare a project for mission execution.

    Fetches the latest refs, stashes dirty state, checks out the base
    branch, and fast-forwards it to match the remote. Non-fatal — returns
    a PrepResult with success=False on errors rather than raising.
    """
    result = PrepResult()

    # Record current branch before any changes
    rc, current_branch, _ = run_git(
        "rev-parse", "--abbrev-ref", "HEAD", cwd=project_path
    )
    result.previous_branch = current_branch if rc == 0 else ""

    # Determine remote and base branch
    remote = get_upstream_remote(project_path, project_name, koan_root)
    result.remote_used = remote

    try:
        config = load_projects_config(koan_root)
        if config:
            am = get_project_auto_merge(config, project_name)
            result.base_branch = am.get("base_branch", "main")
    except Exception as e:
        logger.warning("config load error for base_branch: %s", e)

    base_branch = result.base_branch

    # Fetch latest refs
    rc, _, stderr = run_git(
        "fetch", remote, base_branch, cwd=project_path, timeout=30
    )
    if rc != 0:
        result.success = False
        result.error = f"fetch failed: {stderr}"
        return result

    # Stash dirty state if needed
    rc, porcelain, _ = run_git("status", "--porcelain", cwd=project_path)
    if rc == 0 and porcelain:
        rc, _, stderr = run_git(
            "stash", "--include-untracked", cwd=project_path
        )
        if rc == 0:
            result.stashed = True
        # Stash failure is non-fatal — continue anyway

    # Checkout base branch
    rc, _, stderr = run_git("checkout", base_branch, cwd=project_path)
    if rc != 0:
        # Branch may not exist locally — create from remote tracking
        rc, _, stderr = run_git(
            "checkout", "-b", base_branch, f"{remote}/{base_branch}",
            cwd=project_path,
        )
        if rc != 0:
            result.success = False
            result.error = f"checkout failed: {stderr}"
            return result

    # Fast-forward to match remote
    rc, _, stderr = run_git(
        "merge", "--ff-only", f"{remote}/{base_branch}", cwd=project_path
    )
    if rc != 0:
        # Local diverged — log what will be discarded, then reset
        rc_log, diverged, _ = run_git(
            "log", f"{remote}/{base_branch}..HEAD", "--oneline",
            cwd=project_path,
        )
        if rc_log == 0 and diverged:
            logger.warning(
                "Discarding local commits on %s to match %s/%s:\n%s",
                base_branch, remote, base_branch, diverged,
            )

        rc, _, stderr = run_git(
            "reset", "--hard", f"{remote}/{base_branch}", cwd=project_path
        )
        if rc != 0:
            result.success = False
            result.error = f"reset failed: {stderr}"
            return result

    return result
