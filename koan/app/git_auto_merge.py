#!/usr/bin/env python3
"""
Koan -- Git Auto-Merge

Automatically merges koan/* branches based on configuration rules.
Supports:
- Default global rules
- Per-project overrides
- Configurable base_branch (main, develop, staging, etc.)
- Multiple merge strategies (squash, merge, rebase)
- Safety checks (clean working tree, branch pushed, conflict detection)
"""

import fcntl
import fnmatch
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, Dict, List

# Import config utilities
from app.utils import load_config


def run_git(cwd: str, *args) -> Tuple[int, str, str]:
    """Run a git command and return (exit_code, stdout, stderr).

    Args:
        cwd: Working directory for the command
        *args: Git command arguments (e.g., "status", "--porcelain")

    Returns:
        (exit_code, stdout, stderr)
    """
    try:
        result = subprocess.run(
            ["git"] + list(args),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return 1, "", "Git command timed out"
    except Exception as e:
        return 1, "", str(e)


def get_auto_merge_config(config: dict, project_name: str) -> dict:
    """Get auto-merge config with per-project override support.

    Merges global defaults with project-specific overrides.

    Args:
        config: Full config dict from load_config()
        project_name: Name of the project (e.g., "koan", "anantys-back")

    Returns:
        Merged config with keys: enabled, base_branch, strategy, rules
    """
    global_cfg = config.get("git_auto_merge", {})
    project_cfg = config.get("projects", {}).get(project_name, {}).get("git_auto_merge", {})

    # Deep merge: project overrides global
    return {
        "enabled": project_cfg.get("enabled", global_cfg.get("enabled", True)),
        "base_branch": project_cfg.get("base_branch", global_cfg.get("base_branch", "main")),
        "strategy": project_cfg.get("strategy", global_cfg.get("strategy", "squash")),
        "rules": project_cfg.get("rules", global_cfg.get("rules", []))
    }


def find_matching_rule(branch: str, rules: List[dict]) -> Optional[dict]:
    """Find the first rule matching the branch name.

    Args:
        branch: Branch name (e.g., "koan/fix-cors")
        rules: List of rule dicts with "pattern" keys

    Returns:
        First matching rule dict, or None if no match
    """
    for rule in rules:
        pattern = rule.get("pattern", "")
        if fnmatch.fnmatch(branch, pattern):
            return rule
    return None


def should_auto_merge(config: dict, branch: str) -> Tuple[bool, Optional[dict], str]:
    """Check if branch should be auto-merged based on config.

    Args:
        config: Merged config from get_auto_merge_config()
        branch: Branch name to check

    Returns:
        (should_merge, matching_rule, resolved_base_branch)
    """
    # Check if auto-merge is enabled
    if not config.get("enabled", True):
        return False, None, ""

    # Find matching rule
    rules = config.get("rules", [])
    rule = find_matching_rule(branch, rules)

    if not rule or not rule.get("auto_merge", False):
        return False, None, ""

    # Resolve base_branch with precedence: rule > project > global > default
    base_branch = rule.get("base_branch") or config.get("base_branch", "main")

    return True, rule, base_branch


def is_working_tree_clean(project_path: str) -> bool:
    """Check if working tree has no uncommitted changes.

    Args:
        project_path: Path to the git repository

    Returns:
        True if clean, False otherwise
    """
    exit_code, stdout, _ = run_git(project_path, "status", "--porcelain")
    return exit_code == 0 and stdout == ""


def is_branch_pushed(project_path: str, branch: str) -> bool:
    """Check if branch exists on remote origin.

    Args:
        project_path: Path to the git repository
        branch: Branch name to check

    Returns:
        True if branch exists on origin, False otherwise
    """
    exit_code, stdout, _ = run_git(project_path, "ls-remote", "--heads", "origin", branch)
    return exit_code == 0 and branch in stdout


def perform_merge(project_path: str, branch: str, base_branch: str, strategy: str) -> Tuple[bool, str]:
    """Execute git merge with specified strategy.

    Args:
        project_path: Path to the git repository
        branch: Source branch to merge from
        base_branch: Target branch to merge into
        strategy: "squash", "merge", or "rebase"

    Returns:
        (success, error_message)
    """
    # Checkout base branch
    exit_code, _, stderr = run_git(project_path, "checkout", base_branch)
    if exit_code != 0:
        return False, f"Failed to checkout {base_branch}: {stderr}"

    # Pull latest from remote
    exit_code, _, stderr = run_git(project_path, "pull", "origin", base_branch)
    if exit_code != 0:
        return False, f"Failed to pull {base_branch}: {stderr}"

    # Perform merge based on strategy
    if strategy == "squash":
        # Squash merge: combine all commits into one
        exit_code, _, stderr = run_git(project_path, "merge", "--squash", branch)
        if exit_code != 0:
            # Abort squash on conflict
            run_git(project_path, "reset", "--hard")
            return False, f"Merge conflict during squash: {stderr}"

        # Commit the squash
        commit_msg = f"koan: auto-merge {branch} (squash)"
        exit_code, _, stderr = run_git(project_path, "commit", "-m", commit_msg)
        if exit_code != 0:
            return False, f"Failed to commit squash: {stderr}"

    elif strategy == "rebase":
        # Rebase: replay commits on top of base_branch
        exit_code, _, stderr = run_git(project_path, "rebase", base_branch, branch)
        if exit_code != 0:
            # Abort rebase on conflict
            run_git(project_path, "rebase", "--abort")
            return False, f"Merge conflict during rebase: {stderr}"

        # Fast-forward merge
        exit_code, _, stderr = run_git(project_path, "checkout", base_branch)
        if exit_code != 0:
            return False, f"Failed to checkout {base_branch} after rebase: {stderr}"

        exit_code, _, stderr = run_git(project_path, "merge", "--ff-only", branch)
        if exit_code != 0:
            return False, f"Failed to fast-forward merge: {stderr}"

    else:
        # Default: regular merge with --no-ff
        exit_code, _, stderr = run_git(project_path, "merge", "--no-ff", branch, "-m", f"koan: auto-merge {branch}")
        if exit_code != 0:
            # Abort merge on conflict
            run_git(project_path, "merge", "--abort")
            return False, f"Merge conflict: {stderr}"

    # Push to remote
    exit_code, _, stderr = run_git(project_path, "push", "origin", base_branch)
    if exit_code != 0:
        return False, f"Failed to push {base_branch}: {stderr}"

    return True, ""


def cleanup_branch(project_path: str, branch: str) -> bool:
    """Delete branch locally and on remote.

    Args:
        project_path: Path to the git repository
        branch: Branch name to delete

    Returns:
        True if cleanup succeeded, False otherwise
    """
    # Delete local branch
    exit_code, _, _ = run_git(project_path, "branch", "-d", branch)
    if exit_code != 0:
        # Try force delete if regular delete fails
        exit_code, _, _ = run_git(project_path, "branch", "-D", branch)
        if exit_code != 0:
            return False

    # Delete remote branch
    exit_code, _, _ = run_git(project_path, "push", "origin", "--delete", branch)
    return exit_code == 0


def write_merge_success_to_journal(instance_dir: str, project_name: str, branch: str, base_branch: str, strategy: str):
    """Write successful merge to today's journal.

    Args:
        instance_dir: Path to instance directory
        project_name: Name of the project
        branch: Source branch that was merged
        base_branch: Target branch merged into
        strategy: Merge strategy used
    """
    journal_dir = Path(instance_dir) / "journal" / datetime.now().strftime("%Y-%m-%d")
    journal_file = journal_dir / f"{project_name}.md"
    journal_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%H:%M")
    entry = f"\n## Auto-Merge — {timestamp}\n\n✓ Merged `{branch}` into `{base_branch}` ({strategy})\n"

    # Append to journal with file locking
    with open(journal_file, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(entry)
        fcntl.flock(f, fcntl.LOCK_UN)


def write_merge_failure_to_journal(instance_dir: str, project_name: str, branch: str, error: str):
    """Write failed merge to today's journal.

    Args:
        instance_dir: Path to instance directory
        project_name: Name of the project
        branch: Source branch that failed to merge
        error: Error message
    """
    journal_dir = Path(instance_dir) / "journal" / datetime.now().strftime("%Y-%m-%d")
    journal_file = journal_dir / f"{project_name}.md"
    journal_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%H:%M")
    entry = f"\n## Auto-Merge Failed — {timestamp}\n\n✗ Failed to merge `{branch}`: {error}\nManual intervention required.\n"

    # Append to journal with file locking
    with open(journal_file, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(entry)
        fcntl.flock(f, fcntl.LOCK_UN)


def auto_merge_branch(instance_dir: str, project_name: str, project_path: str, branch: str) -> int:
    """Main entry point for auto-merge logic.

    Orchestrates the complete auto-merge flow:
    1. Load config for project
    2. Check if enabled + rule matches
    3. Safety checks (clean tree, branch pushed)
    4. Perform merge
    5. Push to remote
    6. Cleanup branch (if configured)
    7. Write to journal

    Args:
        instance_dir: Path to instance directory
        project_name: Name of the project
        project_path: Path to the project repository
        branch: Branch name to auto-merge

    Returns:
        0 = success or skip (non-blocking)
        1 = error (logged but non-blocking)
    """
    # Load config
    config = load_config()
    merge_config = get_auto_merge_config(config, project_name)

    # Check if should auto-merge
    should_merge, rule, base_branch = should_auto_merge(merge_config, branch)

    if not should_merge:
        print(f"[git_auto_merge] Not configured for auto-merge: {branch}")
        return 0

    print(f"[git_auto_merge] Auto-merge enabled for {branch} → {base_branch}")

    # Safety check: working tree must be clean
    if not is_working_tree_clean(project_path):
        error = "Working tree has uncommitted changes"
        print(f"[git_auto_merge] Safety check failed: {error}")
        write_merge_failure_to_journal(instance_dir, project_name, branch, error)
        return 1

    # Safety check: branch must be pushed
    if not is_branch_pushed(project_path, branch):
        error = "Branch not pushed to remote"
        print(f"[git_auto_merge] Safety check failed: {error}")
        write_merge_failure_to_journal(instance_dir, project_name, branch, error)
        return 1

    # Get merge strategy
    strategy = rule.get("strategy") or merge_config.get("strategy", "squash")

    # Perform merge
    success, error = perform_merge(project_path, branch, base_branch, strategy)

    if not success:
        print(f"[git_auto_merge] Merge failed: {error}")
        write_merge_failure_to_journal(instance_dir, project_name, branch, error)
        return 1

    print(f"[git_auto_merge] Successfully merged {branch} into {base_branch} ({strategy})")

    # Cleanup branch if configured
    if rule.get("delete_after_merge", False):
        if cleanup_branch(project_path, branch):
            print(f"[git_auto_merge] Cleaned up branch {branch}")
        else:
            print(f"[git_auto_merge] Warning: Failed to cleanup branch {branch}")

    # Write success to journal
    write_merge_success_to_journal(instance_dir, project_name, branch, base_branch, strategy)

    return 0


if __name__ == "__main__":
    if len(sys.argv) < 5:
        print("Usage: git_auto_merge.py <instance_dir> <project_name> <project_path> <branch>")
        sys.exit(1)

    instance_dir = sys.argv[1]
    project_name = sys.argv[2]
    project_path = sys.argv[3]
    branch = sys.argv[4]

    sys.exit(auto_merge_branch(instance_dir, project_name, project_path, branch))
