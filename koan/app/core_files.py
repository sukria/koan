"""
Kōan -- Core file integrity checker.

Verifies that critical unversioned files (projects.yaml, instance/, .env)
are not accidentally deleted during mission execution. These files are
gitignored and cannot be recovered from version control.

Used as pre/post guards around Claude CLI invocations.
Includes auto-recovery: tracked project files are restored from git,
unversioned core files are restored from pre-mission backups.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


# Paths relative to KOAN_ROOT that must never be deleted.
# Directories end with "/" to distinguish from files.
CORE_PATHS = (
    "instance/",
    "instance/missions.md",
    "instance/config.yaml",
    "instance/soul.md",
    "instance/memory/",
    "projects.yaml",
)

# Paths relative to a *project* working directory that must survive.
# Checked when project_path is provided (i.e. for every project).
PROJECT_CORE_PATHS = (
    ".env",
    "CLAUDE.md",
)


def _check_paths(root: Path, paths: tuple) -> Set[str]:
    """Return the set of paths that exist under *root*."""
    present = set()
    for p in paths:
        target = root / p
        if p.endswith("/"):
            if target.is_dir():
                present.add(p)
        else:
            if target.is_file():
                present.add(p)
    return present


class CoreSnapshot:
    """Pre-mission snapshot of core files.

    Stores both the set of present paths (for diff) and backup copies
    of unversioned files (for restoration).
    """

    def __init__(
        self,
        present: Set[str],
        backup_dir: Optional[str] = None,
        project_path: Optional[str] = None,
    ):
        self.present = present
        self.backup_dir = backup_dir
        self.project_path = project_path

    def cleanup(self) -> None:
        """Remove temporary backup directory."""
        if self.backup_dir:
            shutil.rmtree(self.backup_dir, ignore_errors=True)
            self.backup_dir = None


def snapshot_core_files(
    koan_root: str,
    project_path: Optional[str] = None,
) -> Set[str]:
    """Take a snapshot of which core files currently exist.

    Returns a set of relative path strings that are present.
    Call this BEFORE a mission runs.

    For richer snapshots with backup support, use snapshot_with_backup().
    """
    root = Path(koan_root)
    present = _check_paths(root, CORE_PATHS)

    if project_path:
        proj = Path(project_path)
        for p in PROJECT_CORE_PATHS:
            if (proj / p).is_file():
                present.add(f"project:{p}")

    return present


def snapshot_with_backup(
    koan_root: str,
    project_path: Optional[str] = None,
) -> CoreSnapshot:
    """Take a snapshot and back up unversioned core files.

    Creates temporary copies of unversioned files so they can be
    restored if deleted during mission execution.
    """
    present = snapshot_core_files(koan_root, project_path)

    backup_dir = tempfile.mkdtemp(prefix="koan-core-backup-")
    root = Path(koan_root)

    # Back up unversioned koan root files (not directories)
    for p in CORE_PATHS:
        if p.endswith("/"):
            continue
        src = root / p
        if src.is_file():
            dst = Path(backup_dir) / p
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dst))

    return CoreSnapshot(
        present=present,
        backup_dir=backup_dir,
        project_path=project_path,
    )


def check_core_files(
    koan_root: str,
    before: Set[str],
    project_path: Optional[str] = None,
) -> List[str]:
    """Compare current state against a pre-mission snapshot.

    Returns a list of human-readable warnings for files that
    disappeared since *before* was captured.  Empty list = all good.
    """
    if isinstance(before, CoreSnapshot):
        after = snapshot_core_files(koan_root, project_path)
        missing = before.present - after
    else:
        after = snapshot_core_files(koan_root, project_path)
        missing = before - after
    if not missing:
        return []

    warnings = []
    for path in sorted(missing):
        if path.startswith("project:"):
            real = path[len("project:"):]
            warnings.append(f"Project file disappeared: {real}")
        else:
            warnings.append(f"Core file disappeared: {path}")
    return warnings


def restore_missing_files(
    koan_root: str,
    before: "Set[str] | CoreSnapshot",
    project_path: Optional[str] = None,
) -> Tuple[List[str], List[str]]:
    """Attempt to restore files that disappeared during mission execution.

    Returns (restored, failed) — lists of human-readable descriptions.

    Strategy:
    - Project tracked files (CLAUDE.md): restore via git checkout HEAD
    - Unversioned core files: restore from pre-mission backup (if CoreSnapshot)
    """
    after = snapshot_core_files(koan_root, project_path)

    if isinstance(before, CoreSnapshot):
        missing = before.present - after
        backup_dir = before.backup_dir
        snap_project_path = before.project_path or project_path
    else:
        missing = before - after
        backup_dir = None
        snap_project_path = project_path

    if not missing:
        return [], []

    restored = []
    failed = []

    for path in sorted(missing):
        if path.startswith("project:"):
            real = path[len("project:"):]
            proj = snap_project_path
            if proj and _restore_from_git(proj, real):
                restored.append(f"Restored project file from git: {real}")
            else:
                failed.append(f"Project file disappeared: {real}")
        else:
            # Try backup restore for unversioned core files
            if backup_dir and not path.endswith("/"):
                backup_file = Path(backup_dir) / path
                target = Path(koan_root) / path
                if backup_file.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(backup_file), str(target))
                    restored.append(f"Restored core file from backup: {path}")
                    continue
            failed.append(f"Core file disappeared: {path}")

    return restored, failed


def _restore_from_git(project_path: str, filename: str) -> bool:
    """Try to restore a file via git checkout HEAD -- <file>.

    Returns True if successful, False otherwise.
    """
    try:
        # First check if the file is tracked in git
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", filename],
            cwd=project_path,
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            return False  # Not tracked, can't restore

        result = subprocess.run(
            ["git", "checkout", "HEAD", "--", filename],
            cwd=project_path,
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def log_integrity_warnings(warnings: List[str]) -> None:
    """Print integrity warnings to stderr."""
    if not warnings:
        return
    print("[core_files] ⚠️  INTEGRITY CHECK FAILED:", file=sys.stderr)
    for w in warnings:
        print(f"[core_files]   - {w}", file=sys.stderr)


def log_restorations(restored: List[str]) -> None:
    """Print restoration results to stderr."""
    if not restored:
        return
    print("[core_files] ♻️  AUTO-RECOVERY:", file=sys.stderr)
    for r in restored:
        print(f"[core_files]   ✓ {r}", file=sys.stderr)
