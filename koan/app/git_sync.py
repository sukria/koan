#!/usr/bin/env python3
"""
Kōan — Git sync awareness

Checks what changed in the repo since last run:
- Which koan/* branches were merged or deleted
- Recent commits on main by the human
- Current branch state

Writes a summary to the journal so Kōan stays aware of repo evolution
between runs. Called from run.sh periodically (every N runs).

Usage:
    python3 git_sync.py <instance_dir> <project_name> <project_path>
"""

import fcntl
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import List, Tuple


def run_git(cwd: str, *args: str) -> str:
    """Run a git command and return stdout, or empty string on failure."""
    try:
        result = subprocess.run(
            ["git"] + list(args),
            capture_output=True, text=True, timeout=10,
            cwd=cwd,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def get_koan_branches(project_path: str) -> List[str]:
    """List all koan/* branches (local and remote)."""
    output = run_git(project_path, "branch", "-a", "--list", "*koan/*")
    branches = []
    for line in output.splitlines():
        name = line.strip().lstrip("* ")
        # Normalize remote branches
        if "remotes/origin/" in name:
            name = name.replace("remotes/origin/", "")
        if name.startswith("koan/"):
            branches.append(name)
    return sorted(set(branches))


def get_recent_main_commits(project_path: str, since_hours: int = 12) -> List[str]:
    """Get recent commits on main (last N hours)."""
    output = run_git(
        project_path, "log", "origin/main",
        f"--since={since_hours} hours ago",
        "--oneline", "--no-merges", "-20"
    )
    return [line for line in output.splitlines() if line.strip()]


def _normalize_branch(line: str) -> str:
    """Extract koan/* branch name from git branch output line."""
    name = line.strip().lstrip("* ")
    if "remotes/origin/" in name:
        name = name.replace("remotes/origin/", "")
    return name if name.startswith("koan/") else ""


def _get_target_branches(project_path: str) -> List[str]:
    """Return remote target branches that exist in this repo."""
    candidates = ["origin/main", "origin/staging", "origin/develop", "origin/production"]
    existing = []
    for ref in candidates:
        if run_git(project_path, "rev-parse", "--verify", ref):
            existing.append(ref)
    return existing or ["origin/main"]


def get_merged_branches(project_path: str) -> List[str]:
    """List koan/* branches merged into any target branch (main, staging, etc.)."""
    targets = _get_target_branches(project_path)
    merged = set()
    for target in targets:
        output = run_git(project_path, "branch", "-a", "--merged", target,
                         "--list", "*koan/*")
        for line in output.splitlines():
            name = _normalize_branch(line)
            if name:
                merged.add(name)
    return sorted(merged)


def get_unmerged_branches(project_path: str) -> List[str]:
    """List koan/* branches NOT merged into any target branch."""
    all_koan = set(get_koan_branches(project_path))
    merged = set(get_merged_branches(project_path))
    return sorted(all_koan - merged)


def build_sync_report(project_path: str) -> str:
    """Build a human-readable git sync report."""
    # Fetch latest
    run_git(project_path, "fetch", "--prune")

    merged = get_merged_branches(project_path)
    unmerged = get_unmerged_branches(project_path)
    recent = get_recent_main_commits(project_path, since_hours=12)

    parts = []
    now = datetime.now().strftime("%H:%M")
    parts.append(f"Git sync @ {now}")

    if merged:
        parts.append(f"\nMerged koan/* branches ({len(merged)}):")
        for b in merged:
            parts.append(f"  ✓ {b}")

    if unmerged:
        parts.append(f"\nUnmerged koan/* branches ({len(unmerged)}):")
        for b in unmerged:
            parts.append(f"  → {b}")

    if recent:
        parts.append(f"\nRecent main commits ({len(recent)}):")
        for c in recent[:10]:
            parts.append(f"  {c}")

    if not merged and not unmerged and not recent:
        parts.append("\nNo notable changes since last sync.")

    return "\n".join(parts)


def write_sync_to_journal(instance_dir: str, project_name: str, report: str):
    """Append git sync report to today's journal."""
    from app.utils import append_to_journal
    entry = f"\n## Git Sync — {datetime.now().strftime('%H:%M')}\n\n{report}\n"
    append_to_journal(Path(instance_dir), project_name, entry)


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(f"Usage: {sys.argv[0]} <instance_dir> <project_name> <project_path>",
              file=sys.stderr)
        sys.exit(1)

    instance_dir = sys.argv[1]
    project_name = sys.argv[2]
    project_path = sys.argv[3]

    report = build_sync_report(project_path)
    write_sync_to_journal(instance_dir, project_name, report)
    print(report)
