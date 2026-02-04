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


def get_origin_url(cwd: str) -> str:
    """Get the origin remote URL for a git repository.

    Returns empty string if origin is not configured or git fails.
    """
    exit_code, stdout, _ = run_git(cwd, "remote", "get-url", "origin")
    if exit_code != 0:
        return ""
    return stdout


def normalize_git_url(url: str) -> str:
    """Normalize a git URL for comparison.

    Handles: https://github.com/user/repo.git, git@github.com:user/repo.git,
    https://github.com/user/repo (no .git suffix).
    Returns lowercase 'host/user/repo' for comparison.
    """
    url = url.strip().lower()
    # Remove trailing .git
    if url.endswith(".git"):
        url = url[:-4]
    # SSH format: git@github.com:user/repo
    if url.startswith("git@"):
        url = url[4:]  # remove git@
        url = url.replace(":", "/", 1)  # git@host:user/repo -> host/user/repo
    # HTTPS format: https://github.com/user/repo
    elif "://" in url:
        url = url.split("://", 1)[1]
    # Remove trailing slash
    url = url.rstrip("/")
    return url


def is_upstream_origin(cwd: str, upstream_url: str) -> bool:
    """Check if origin remote points to the upstream repository.

    Args:
        cwd: Project working directory.
        upstream_url: The canonical upstream URL from config.

    Returns:
        True if origin matches upstream, False if it's a fork.
    """
    if not upstream_url:
        # No upstream configured — assume origin IS upstream (backward compat)
        return True
    origin_url = get_origin_url(cwd)
    if not origin_url:
        return False
    return normalize_git_url(origin_url) == normalize_git_url(upstream_url)


def create_pull_request(cwd: str, branch: str, base_branch: str, upstream_url: str) -> Tuple[bool, str]:
    """Create a pull request on the upstream repository using gh CLI.

    Assumes the branch has already been pushed to origin (the fork).

    Args:
        cwd: Project working directory.
        branch: The branch to create a PR from.
        base_branch: The target branch on upstream.
        upstream_url: The upstream repository URL (used to derive owner/repo).

    Returns:
        (success, pr_url_or_error)
    """
    # Derive upstream repo identifier (e.g., "sukria/koan") from URL
    normalized = normalize_git_url(upstream_url)
    # normalized is like "github.com/sukria/koan"
    parts = normalized.split("/")
    if len(parts) < 3:
        return False, f"Cannot parse upstream URL: {upstream_url}"
    upstream_repo = f"{parts[-2]}/{parts[-1]}"

    # Get origin owner for cross-fork PR (e.g., "atoomic")
    origin_url = get_origin_url(cwd)
    origin_normalized = normalize_git_url(origin_url)
    origin_parts = origin_normalized.split("/")
    if len(origin_parts) < 3:
        return False, f"Cannot parse origin URL: {origin_url}"
    origin_owner = origin_parts[-2]

    # The head ref for cross-fork PRs is "owner:branch"
    head_ref = f"{origin_owner}:{branch}"

    subjects = get_branch_commit_messages(cwd, branch, base_branch)
    title = build_merge_commit_message(branch, "pr", subjects).split("\n")[0]
    body = "\n".join(f"- {s}" for s in subjects) if subjects else "Auto-generated PR from koan branch."

    try:
        result = subprocess.run(
            ["gh", "pr", "create",
             "--repo", upstream_repo,
             "--head", head_ref,
             "--base", base_branch,
             "--title", title,
             "--body", body],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return False, result.stderr.strip() or "gh pr create failed"
        return True, result.stdout.strip()
    except FileNotFoundError:
        return False, "gh CLI not found — install GitHub CLI to create PRs from forks"
    except subprocess.TimeoutExpired:
        return False, "gh pr create timed out"
    except Exception as e:
        return False, str(e)


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

    def write_pr_success_to_journal(self, branch: str, base_branch: str, pr_url: str):
        """Write successful PR creation to today's journal."""
        from app.utils import append_to_journal
        timestamp = datetime.now().strftime("%H:%M")
        entry = f"\n## Pull Request Created — {timestamp}\n\n✓ PR for `{branch}` → `{base_branch}`: {pr_url}\n(Fork detected — PR submitted upstream instead of auto-merge)\n"
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

        # Fork detection: if origin is not upstream, create PR instead of merging
        upstream_url = merge_config.get("upstream_url", "")
        if upstream_url and not is_upstream_origin(self.project_path, upstream_url):
            print(f"[git_auto_merge] Fork detected — creating PR upstream instead of merging")
            success, result = create_pull_request(self.project_path, branch, base_branch, upstream_url)
            if success:
                print(f"[git_auto_merge] PR created: {result}")
                self.write_pr_success_to_journal(branch, base_branch, result)
                return 0
            else:
                print(f"[git_auto_merge] PR creation failed: {result}")
                self.write_merge_failure_to_journal(branch, f"PR creation failed: {result}")
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


def write_pr_success_to_journal(instance_dir: str, project_name: str, branch: str, base_branch: str, pr_url: str):
    """Write successful PR creation to today's journal."""
    from app.utils import append_to_journal
    timestamp = datetime.now().strftime("%H:%M")
    entry = f"\n## Pull Request Created — {timestamp}\n\n✓ PR for `{branch}` → `{base_branch}`: {pr_url}\n(Fork detected — PR submitted upstream instead of auto-merge)\n"
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

    # Fork detection: if origin is not upstream, create PR instead of merging
    upstream_url = merge_config.get("upstream_url", "")
    if upstream_url and not is_upstream_origin(project_path, upstream_url):
        print(f"[git_auto_merge] Fork detected — creating PR upstream instead of merging")
        success, result = create_pull_request(project_path, branch, base_branch, upstream_url)
        if success:
            print(f"[git_auto_merge] PR created: {result}")
            write_pr_success_to_journal(instance_dir, project_name, branch, base_branch, result)
            return 0
        else:
            print(f"[git_auto_merge] PR creation failed: {result}")
            write_merge_failure_to_journal(instance_dir, project_name, branch, f"PR creation failed: {result}")
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
