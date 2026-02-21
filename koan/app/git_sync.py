#!/usr/bin/env python3
"""
Kōan — Git sync awareness

Checks what changed in the repo since last run:
- Which koan/* branches were merged or deleted
- Recent commits on main by the human
- Current branch state

Writes a summary to the journal so Kōan stays aware of repo evolution
between runs. Called from run.py periodically (every N runs).

Usage:
    python3 git_sync.py <instance_dir> <project_name> <project_path>
"""

import sys
from datetime import datetime
from pathlib import Path
from typing import List

from app.git_utils import run_git as _run_git_core


# ---------------------------------------------------------------------------
# Low-level git helpers (stateless)
# ---------------------------------------------------------------------------

def run_git(cwd: str, *args: str) -> str:
    """Run a git command and return stdout, or empty string on failure.

    Thin wrapper around git_utils.run_git() preserving the original
    string-return interface for backward compatibility.
    """
    rc, stdout, _ = _run_git_core(*args, cwd=cwd, timeout=10)
    return stdout if rc == 0 else ""


def _get_prefix() -> str:
    """Get the configured branch prefix (lazy import to avoid circular deps)."""
    from app.config import get_branch_prefix
    return get_branch_prefix()


def _normalize_branch(line: str, prefix: str = "") -> str:
    """Extract agent branch name from git branch output line.

    Args:
        line: Raw line from git branch output.
        prefix: Branch prefix to match (e.g., 'koan/'). If empty, uses config.
    """
    if not prefix:
        prefix = _get_prefix()
    name = line.strip().lstrip("* ")
    if "remotes/origin/" in name:
        name = name.replace("remotes/origin/", "")
    return name if name.startswith(prefix) else ""


# ---------------------------------------------------------------------------
# GitSync class — encapsulates project context
# ---------------------------------------------------------------------------

class GitSync:
    """Tracks git state changes between koan runs for a specific project.

    Encapsulates the project_path and instance_dir so callers don't need
    to thread them through every function call.
    """

    def __init__(self, instance_dir: str, project_name: str, project_path: str):
        self.instance_dir = instance_dir
        self.project_name = project_name
        self.project_path = project_path

    def get_koan_branches(self) -> List[str]:
        """List all agent branches (local and remote)."""
        prefix = _get_prefix()
        glob_pattern = f"*{prefix}*"
        output = run_git(self.project_path, "branch", "-a", "--list", glob_pattern)
        branches = []
        for line in output.splitlines():
            name = line.strip().lstrip("* ")
            if "remotes/origin/" in name:
                name = name.replace("remotes/origin/", "")
            if name.startswith(prefix):
                branches.append(name)
        return sorted(set(branches))

    def get_recent_main_commits(self, since_hours: int = 12) -> List[str]:
        """Get recent commits on main (last N hours)."""
        output = run_git(
            self.project_path, "log", "origin/main",
            f"--since={since_hours} hours ago",
            "--oneline", "--no-merges", "-20"
        )
        return [line for line in output.splitlines() if line.strip()]

    def _get_target_branches(self) -> List[str]:
        """Return remote target branches that exist in this repo."""
        candidates = ["origin/main", "origin/staging", "origin/develop", "origin/production"]
        existing = []
        for ref in candidates:
            if run_git(self.project_path, "rev-parse", "--verify", ref):
                existing.append(ref)
        return existing or ["origin/main"]

    def get_merged_branches(self) -> List[str]:
        """List agent branches merged into any target branch."""
        prefix = _get_prefix()
        glob_pattern = f"*{prefix}*"
        targets = self._get_target_branches()
        merged = set()
        for target in targets:
            output = run_git(self.project_path, "branch", "-a", "--merged", target,
                             "--list", glob_pattern)
            for line in output.splitlines():
                name = _normalize_branch(line, prefix)
                if name:
                    merged.add(name)
        return sorted(merged)

    def get_unmerged_branches(self) -> List[str]:
        """List koan/* branches NOT merged into any target branch."""
        all_koan = set(self.get_koan_branches())
        merged = set(self.get_merged_branches())
        return sorted(all_koan - merged)

    def _get_current_branch(self) -> str:
        """Return the current branch name, or empty string on failure."""
        return run_git(self.project_path, "rev-parse", "--abbrev-ref", "HEAD")

    def _get_local_branches(self, prefix: str) -> List[str]:
        """List local-only branches matching prefix (excludes remotes)."""
        output = run_git(self.project_path, "branch", "--list", f"{prefix}*")
        branches = []
        for line in output.splitlines():
            name = line.strip().lstrip("* ")
            if name.startswith(prefix):
                branches.append(name)
        return branches

    def cleanup_merged_branches(self, merged: List[str]) -> List[str]:
        """Delete local branches that are confirmed merged.

        Only deletes branches matching the agent prefix. Never deletes
        the current branch. Uses ``git branch -d`` (safe delete — refuses
        if not fully merged).

        Args:
            merged: List of merged branch names from get_merged_branches().

        Returns:
            List of successfully deleted branch names.
        """
        if not merged:
            return []

        current = self._get_current_branch()
        prefix = _get_prefix()
        local_branches = set(self._get_local_branches(prefix))

        deleted = []
        for branch in merged:
            # Skip if not a local branch (remote-only ref)
            if branch not in local_branches:
                continue
            # Never delete the current branch
            if branch == current:
                continue
            # Safe delete — git branch -d refuses if not fully merged
            result = run_git(self.project_path, "branch", "-d", branch)
            if result:  # non-empty stdout means success
                deleted.append(branch)

        return deleted

    def build_sync_report(self) -> str:
        """Build a human-readable git sync report."""
        run_git(self.project_path, "fetch", "--prune")

        merged = self.get_merged_branches()
        unmerged = self.get_unmerged_branches()
        recent = self.get_recent_main_commits(since_hours=12)

        # Auto-cleanup merged local branches
        cleaned = self.cleanup_merged_branches(merged)

        parts = []
        now = datetime.now().strftime("%H:%M")
        parts.append(f"Git sync @ {now}")

        prefix = _get_prefix()
        label = f"{prefix}*"

        if merged:
            parts.append(f"\nMerged {label} branches ({len(merged)}):")
            for b in merged:
                suffix = " (cleaned up)" if b in cleaned else ""
                parts.append(f"  ✓ {b}{suffix}")

        if cleaned:
            parts.append(f"\nCleaned up {len(cleaned)} merged local branch(es).")

        if unmerged:
            parts.append(f"\nUnmerged {label} branches ({len(unmerged)}):")
            for b in unmerged:
                parts.append(f"  → {b}")

        if recent:
            parts.append(f"\nRecent main commits ({len(recent)}):")
            for c in recent[:10]:
                parts.append(f"  {c}")

        if not merged and not unmerged and not recent:
            parts.append("\nNo notable changes since last sync.")

        return "\n".join(parts)

    def write_sync_to_journal(self, report: str):
        """Append git sync report to today's journal."""
        from app.journal import append_to_journal
        entry = f"\n## Git Sync — {datetime.now().strftime('%H:%M')}\n\n{report}\n"
        append_to_journal(Path(self.instance_dir), self.project_name, entry)

    def sync_and_report(self) -> str:
        """Full sync: build report and write to journal. Returns the report."""
        report = self.build_sync_report()
        self.write_sync_to_journal(report)
        return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(f"Usage: {sys.argv[0]} <instance_dir> <project_name> <project_path>",
              file=sys.stderr)
        sys.exit(1)

    instance_dir = sys.argv[1]
    project_name = sys.argv[2]
    project_path = sys.argv[3]

    sync = GitSync(instance_dir, project_name, project_path)
    report = sync.sync_and_report()
    print(report)
