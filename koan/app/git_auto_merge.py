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
from app.utils import load_config, get_auto_merge_config


# ---------------------------------------------------------------------------
# Low-level git helpers (stateless)
# ---------------------------------------------------------------------------

def run_git(cwd: str, *args, env: Optional[Dict[str, str]] = None) -> Tuple[int, str, str]:
    """Run a git command and return (exit_code, stdout, stderr).

    Args:
        cwd: Working directory for the git command.
        *args: Git subcommand and arguments.
        env: Optional extra environment variables to set for this command.
             Merged on top of the current environment.
    """
    try:
        run_env = None
        if env:
            run_env = {**os.environ, **env}
        result = subprocess.run(
            ["git"] + list(args),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
            env=run_env
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return 1, "", "Git command timed out"
    except Exception as e:
        return 1, "", str(e)


def get_branch_commit_messages(project_path: str, branch: str, base_branch: str) -> List[str]:
    """Get commit subjects from branch since divergence from base."""
    _, stdout, _ = run_git(project_path, "log", f"{base_branch}..{branch}", "--pretty=format:%s")
    return [line for line in stdout.splitlines() if line.strip()]


def build_merge_commit_message(branch: str, strategy: str, subjects: List[str]) -> str:
    """Build a descriptive merge commit message."""
    header = f"koan: auto-merge {branch} ({strategy})"
    if not subjects:
        return header
    body = "\n".join(f"- {s}" for s in subjects)
    return f"{header}\n\n{body}"


def get_author_env() -> Dict[str, str]:
    """Return GIT_AUTHOR env vars if KOAN_EMAIL is set.

    Uses GIT_AUTHOR_NAME/EMAIL environment variables instead of --author flag.
    This works for all git commands (merge, commit, rebase) unlike --author
    which is only valid for git commit.
    """
    email = os.environ.get("KOAN_EMAIL", "")
    if email:
        return {
            "GIT_AUTHOR_NAME": "Koan",
            "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": "Koan",
            "GIT_COMMITTER_EMAIL": email,
        }
    return {}


def find_matching_rule(branch: str, rules: List[dict]) -> Optional[dict]:
    """Find the first rule matching the branch name."""
    for rule in rules:
        pattern = rule.get("pattern", "")
        if fnmatch.fnmatch(branch, pattern):
            return rule
    return None


def should_auto_merge(config: dict, branch: str) -> Tuple[bool, Optional[dict], str]:
    """Check if branch should be auto-merged based on config."""
    if not config.get("enabled", True):
        return False, None, ""

    rules = config.get("rules", [])
    rule = find_matching_rule(branch, rules)

    if not rule or not rule.get("auto_merge", False):
        return False, None, ""

    base_branch = rule.get("base_branch") or config.get("base_branch", "main")
    return True, rule, base_branch


# ---------------------------------------------------------------------------
# GitAutoMerger class — encapsulates project context
# ---------------------------------------------------------------------------

class GitAutoMerger:
    """Handles automatic merging of koan/* branches for a specific project.

    Encapsulates the project_path and instance_dir so callers don't need
    to thread them through every function call.
    """

    def __init__(self, instance_dir: str, project_name: str, project_path: str):
        self.instance_dir = instance_dir
        self.project_name = project_name
        self.project_path = project_path

    def is_working_tree_clean(self) -> bool:
        """Check if working tree has no uncommitted changes."""
        exit_code, stdout, _ = run_git(self.project_path, "status", "--porcelain")
        return exit_code == 0 and stdout == ""

    def is_branch_pushed(self, branch: str) -> bool:
        """Check if branch exists on remote origin."""
        exit_code, stdout, _ = run_git(self.project_path, "ls-remote", "--heads", "origin", branch)
        return exit_code == 0 and branch in stdout

    def perform_merge(self, branch: str, base_branch: str, strategy: str) -> Tuple[bool, str]:
        """Execute git merge with specified strategy.

        Always returns to base_branch, even on failure.
        """
        try:
            return self._perform_merge_inner(branch, base_branch, strategy)
        finally:
            run_git(self.project_path, "checkout", base_branch)

    def _perform_merge_inner(self, branch: str, base_branch: str, strategy: str) -> Tuple[bool, str]:
        """Inner merge logic (called by perform_merge with branch safety)."""
        subjects = get_branch_commit_messages(self.project_path, branch, base_branch)
        author_env = get_author_env()

        exit_code, _, stderr = run_git(self.project_path, "checkout", base_branch)
        if exit_code != 0:
            return False, f"Failed to checkout {base_branch}: {stderr}"

        exit_code, _, stderr = run_git(self.project_path, "pull", "origin", base_branch)
        if exit_code != 0:
            return False, f"Failed to pull {base_branch}: {stderr}"

        if strategy == "squash":
            exit_code, _, stderr = run_git(self.project_path, "merge", "--squash", branch)
            if exit_code != 0:
                run_git(self.project_path, "reset", "--hard")
                return False, f"Merge conflict during squash: {stderr}"

            commit_msg = build_merge_commit_message(branch, strategy, subjects)
            exit_code, _, stderr = run_git(self.project_path, "commit", "-m", commit_msg, env=author_env)
            if exit_code != 0:
                return False, f"Failed to commit squash: {stderr}"

        elif strategy == "rebase":
            exit_code, _, stderr = run_git(self.project_path, "rebase", base_branch, branch)
            if exit_code != 0:
                run_git(self.project_path, "rebase", "--abort")
                return False, f"Merge conflict during rebase: {stderr}"

            exit_code, _, stderr = run_git(self.project_path, "checkout", base_branch)
            if exit_code != 0:
                return False, f"Failed to checkout {base_branch} after rebase: {stderr}"

            exit_code, _, stderr = run_git(self.project_path, "merge", "--ff-only", branch)
            if exit_code != 0:
                return False, f"Failed to fast-forward merge: {stderr}"

        else:
            commit_msg = build_merge_commit_message(branch, "merge", subjects)
            exit_code, _, stderr = run_git(self.project_path, "merge", "--no-ff", branch, "-m", commit_msg, env=author_env)
            if exit_code != 0:
                run_git(self.project_path, "merge", "--abort")
                return False, f"Merge conflict: {stderr}"

        exit_code, _, stderr = run_git(self.project_path, "push", "origin", base_branch)
        if exit_code != 0:
            return False, f"Failed to push {base_branch}: {stderr}"

        return True, ""

    def cleanup_local_branch(self, branch: str) -> bool:
        """Delete branch locally only.

        Uses -d first (safe delete, requires branch to be merged).
        Falls back to -D if -d fails (force delete).
        """
        exit_code, _, _ = run_git(self.project_path, "branch", "-d", branch)
        if exit_code != 0:
            exit_code, _, _ = run_git(self.project_path, "branch", "-D", branch)
            return exit_code == 0
        return True

    def cleanup_remote_branch(self, branch: str) -> bool:
        """Delete branch on remote origin."""
        exit_code, _, _ = run_git(self.project_path, "push", "origin", "--delete", branch)
        return exit_code == 0

    def cleanup_branch(self, branch: str) -> bool:
        """Delete branch locally and on remote (backward compat)."""
        local_ok = self.cleanup_local_branch(branch)
        if not local_ok:
            return False
        return self.cleanup_remote_branch(branch)

    def write_merge_success_to_journal(self, branch: str, base_branch: str, strategy: str):
        """Write successful merge to today's journal."""
        from app.utils import append_to_journal
        timestamp = datetime.now().strftime("%H:%M")
        entry = f"\n## Auto-Merge — {timestamp}\n\n✓ Merged `{branch}` into `{base_branch}` ({strategy})\n"
        append_to_journal(Path(self.instance_dir), self.project_name, entry)

    def write_merge_failure_to_journal(self, branch: str, error: str):
        """Write failed merge to today's journal."""
        from app.utils import append_to_journal
        timestamp = datetime.now().strftime("%H:%M")
        entry = f"\n## Auto-Merge Failed — {timestamp}\n\n✗ Failed to merge `{branch}`: {error}\nManual intervention required.\n"
        append_to_journal(Path(self.instance_dir), self.project_name, entry)

    def auto_merge_branch(self, branch: str) -> int:
        """Main entry point for auto-merge logic.

        Returns:
            0 = success or skip (non-blocking)
            1 = error (logged but non-blocking)
        """
        config = load_config()
        merge_config = get_auto_merge_config(config, self.project_name)

        should_merge, rule, base_branch = should_auto_merge(merge_config, branch)

        if not should_merge:
            print(f"[git_auto_merge] Not configured for auto-merge: {branch}")
            return 0

        print(f"[git_auto_merge] Auto-merge enabled for {branch} → {base_branch}")

        if not self.is_working_tree_clean():
            error = "Working tree has uncommitted changes"
            print(f"[git_auto_merge] Safety check failed: {error}")
            self.write_merge_failure_to_journal(branch, error)
            return 1

        if not self.is_branch_pushed(branch):
            error = "Branch not pushed to remote"
            print(f"[git_auto_merge] Safety check failed: {error}")
            self.write_merge_failure_to_journal(branch, error)
            return 1

        strategy = rule.get("strategy") or merge_config.get("strategy", "squash")

        success, error = self.perform_merge(branch, base_branch, strategy)

        if not success:
            print(f"[git_auto_merge] Merge failed: {error}")
            self.write_merge_failure_to_journal(branch, error)
            return 1

        print(f"[git_auto_merge] Successfully merged {branch} into {base_branch} ({strategy})")

        # Always delete local branch after successful merge (stay on base_branch)
        if self.cleanup_local_branch(branch):
            print(f"[git_auto_merge] Deleted local branch {branch}")
        else:
            print(f"[git_auto_merge] Warning: Failed to delete local branch {branch}")

        # Optionally delete remote branch if configured
        if rule.get("delete_after_merge", False):
            if self.cleanup_remote_branch(branch):
                print(f"[git_auto_merge] Deleted remote branch {branch}")
            else:
                print(f"[git_auto_merge] Warning: Failed to delete remote branch {branch}")

        self.write_merge_success_to_journal(branch, base_branch, strategy)

        return 0


# ---------------------------------------------------------------------------
# Module-level functions (backward compatibility)
# ---------------------------------------------------------------------------

def is_working_tree_clean(project_path: str) -> bool:
    """Check if working tree has no uncommitted changes."""
    exit_code, stdout, _ = run_git(project_path, "status", "--porcelain")
    return exit_code == 0 and stdout == ""


def is_branch_pushed(project_path: str, branch: str) -> bool:
    """Check if branch exists on remote origin."""
    exit_code, stdout, _ = run_git(project_path, "ls-remote", "--heads", "origin", branch)
    return exit_code == 0 and branch in stdout


def perform_merge(project_path: str, branch: str, base_branch: str, strategy: str) -> Tuple[bool, str]:
    """Execute git merge with specified strategy."""
    try:
        return _perform_merge_inner(project_path, branch, base_branch, strategy)
    finally:
        run_git(project_path, "checkout", base_branch)


def _perform_merge_inner(project_path: str, branch: str, base_branch: str, strategy: str) -> Tuple[bool, str]:
    """Inner merge logic (called by perform_merge with branch safety)."""
    subjects = get_branch_commit_messages(project_path, branch, base_branch)
    author_env = get_author_env()

    exit_code, _, stderr = run_git(project_path, "checkout", base_branch)
    if exit_code != 0:
        return False, f"Failed to checkout {base_branch}: {stderr}"

    exit_code, _, stderr = run_git(project_path, "pull", "origin", base_branch)
    if exit_code != 0:
        return False, f"Failed to pull {base_branch}: {stderr}"

    if strategy == "squash":
        exit_code, _, stderr = run_git(project_path, "merge", "--squash", branch)
        if exit_code != 0:
            run_git(project_path, "reset", "--hard")
            return False, f"Merge conflict during squash: {stderr}"

        commit_msg = build_merge_commit_message(branch, strategy, subjects)
        exit_code, _, stderr = run_git(project_path, "commit", "-m", commit_msg, env=author_env)
        if exit_code != 0:
            return False, f"Failed to commit squash: {stderr}"

    elif strategy == "rebase":
        exit_code, _, stderr = run_git(project_path, "rebase", base_branch, branch)
        if exit_code != 0:
            run_git(project_path, "rebase", "--abort")
            return False, f"Merge conflict during rebase: {stderr}"

        exit_code, _, stderr = run_git(project_path, "checkout", base_branch)
        if exit_code != 0:
            return False, f"Failed to checkout {base_branch} after rebase: {stderr}"

        exit_code, _, stderr = run_git(project_path, "merge", "--ff-only", branch)
        if exit_code != 0:
            return False, f"Failed to fast-forward merge: {stderr}"

    else:
        commit_msg = build_merge_commit_message(branch, "merge", subjects)
        exit_code, _, stderr = run_git(project_path, "merge", "--no-ff", branch, "-m", commit_msg, env=author_env)
        if exit_code != 0:
            run_git(project_path, "merge", "--abort")
            return False, f"Merge conflict: {stderr}"

    exit_code, _, stderr = run_git(project_path, "push", "origin", base_branch)
    if exit_code != 0:
        return False, f"Failed to push {base_branch}: {stderr}"

    return True, ""


def cleanup_local_branch(project_path: str, branch: str) -> bool:
    """Delete branch locally only.

    Uses -d first (safe delete, requires branch to be merged).
    Falls back to -D if -d fails (force delete).
    """
    exit_code, _, _ = run_git(project_path, "branch", "-d", branch)
    if exit_code != 0:
        exit_code, _, _ = run_git(project_path, "branch", "-D", branch)
        return exit_code == 0
    return True


def cleanup_remote_branch(project_path: str, branch: str) -> bool:
    """Delete branch on remote origin."""
    exit_code, _, _ = run_git(project_path, "push", "origin", "--delete", branch)
    return exit_code == 0


def cleanup_branch(project_path: str, branch: str) -> bool:
    """Delete branch locally and on remote (backward compat wrapper)."""
    local_ok = cleanup_local_branch(project_path, branch)
    if not local_ok:
        return False
    return cleanup_remote_branch(project_path, branch)


def write_merge_success_to_journal(instance_dir: str, project_name: str, branch: str, base_branch: str, strategy: str):
    """Write successful merge to today's journal."""
    from app.utils import append_to_journal
    timestamp = datetime.now().strftime("%H:%M")
    entry = f"\n## Auto-Merge — {timestamp}\n\n✓ Merged `{branch}` into `{base_branch}` ({strategy})\n"
    append_to_journal(Path(instance_dir), project_name, entry)


def write_merge_failure_to_journal(instance_dir: str, project_name: str, branch: str, error: str):
    """Write failed merge to today's journal."""
    from app.utils import append_to_journal
    timestamp = datetime.now().strftime("%H:%M")
    entry = f"\n## Auto-Merge Failed — {timestamp}\n\n✗ Failed to merge `{branch}`: {error}\nManual intervention required.\n"
    append_to_journal(Path(instance_dir), project_name, entry)


def auto_merge_branch(instance_dir: str, project_name: str, project_path: str, branch: str) -> int:
    """Main entry point for auto-merge logic.

    Orchestrates the complete auto-merge flow using module-level functions.
    This preserves backward compatibility with tests that patch at module level.
    """
    config = load_config()
    merge_config = get_auto_merge_config(config, project_name)

    should_merge_flag, rule, base_branch = should_auto_merge(merge_config, branch)

    if not should_merge_flag:
        print(f"[git_auto_merge] Not configured for auto-merge: {branch}")
        return 0

    print(f"[git_auto_merge] Auto-merge enabled for {branch} → {base_branch}")

    if not is_working_tree_clean(project_path):
        error = "Working tree has uncommitted changes"
        print(f"[git_auto_merge] Safety check failed: {error}")
        write_merge_failure_to_journal(instance_dir, project_name, branch, error)
        return 1

    if not is_branch_pushed(project_path, branch):
        error = "Branch not pushed to remote"
        print(f"[git_auto_merge] Safety check failed: {error}")
        write_merge_failure_to_journal(instance_dir, project_name, branch, error)
        return 1

    strategy = rule.get("strategy") or merge_config.get("strategy", "squash")

    success, error = perform_merge(project_path, branch, base_branch, strategy)

    if not success:
        print(f"[git_auto_merge] Merge failed: {error}")
        write_merge_failure_to_journal(instance_dir, project_name, branch, error)
        return 1

    print(f"[git_auto_merge] Successfully merged {branch} into {base_branch} ({strategy})")

    # Always delete local branch after successful merge (stay on base_branch)
    if cleanup_local_branch(project_path, branch):
        print(f"[git_auto_merge] Deleted local branch {branch}")
    else:
        print(f"[git_auto_merge] Warning: Failed to delete local branch {branch}")

    # Optionally delete remote branch if configured
    if rule.get("delete_after_merge", False):
        if cleanup_remote_branch(project_path, branch):
            print(f"[git_auto_merge] Deleted remote branch {branch}")
        else:
            print(f"[git_auto_merge] Warning: Failed to delete remote branch {branch}")

    write_merge_success_to_journal(instance_dir, project_name, branch, base_branch, strategy)

    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 5:
        print("Usage: git_auto_merge.py <instance_dir> <project_name> <project_path> <branch>")
        sys.exit(1)

    instance_dir = sys.argv[1]
    project_name = sys.argv[2]
    project_path = sys.argv[3]
    branch = sys.argv[4]

    merger = GitAutoMerger(instance_dir, project_name, project_path)
    sys.exit(merger.auto_merge_branch(branch))
