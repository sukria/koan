"""Tests for core_files — unversioned file integrity checker."""

import os
import subprocess
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from app.core_files import (
    CORE_PATHS,
    PROJECT_CORE_PATHS,
    CoreSnapshot,
    snapshot_core_files,
    snapshot_with_backup,
    check_core_files,
    restore_missing_files,
    log_integrity_warnings,
    log_restorations,
    _restore_from_git,
)


@pytest.fixture
def fake_koan_root(tmp_path):
    """Create a minimal koan root with all core files present."""
    instance = tmp_path / "instance"
    instance.mkdir()
    (instance / "missions.md").write_text("# Missions\n")
    (instance / "config.yaml").write_text("enabled: true\n")
    (instance / "soul.md").write_text("# Soul\n")
    (instance / "memory").mkdir()
    (tmp_path / "projects.yaml").write_text("projects: []\n")
    return tmp_path


@pytest.fixture
def fake_project(tmp_path):
    """Create a minimal project directory with .env and CLAUDE.md."""
    proj = tmp_path / "myproject"
    proj.mkdir()
    (proj / ".env").write_text("SECRET=xxx\n")
    (proj / "CLAUDE.md").write_text("# Project\n")
    return proj


class TestSnapshotCoreFiles:
    def test_all_present(self, fake_koan_root):
        snap = snapshot_core_files(str(fake_koan_root))
        assert "instance/" in snap
        assert "instance/missions.md" in snap
        assert "instance/config.yaml" in snap
        assert "instance/soul.md" in snap
        assert "instance/memory/" in snap
        assert "projects.yaml" in snap

    def test_missing_file(self, fake_koan_root):
        (fake_koan_root / "projects.yaml").unlink()
        snap = snapshot_core_files(str(fake_koan_root))
        assert "projects.yaml" not in snap
        assert "instance/" in snap  # other files still present

    def test_missing_directory(self, fake_koan_root):
        import shutil
        shutil.rmtree(fake_koan_root / "instance" / "memory")
        snap = snapshot_core_files(str(fake_koan_root))
        assert "instance/memory/" not in snap
        assert "instance/" in snap

    def test_with_project_path(self, fake_koan_root, fake_project):
        snap = snapshot_core_files(str(fake_koan_root), str(fake_project))
        assert "project:.env" in snap
        assert "project:CLAUDE.md" in snap

    def test_project_env_missing(self, fake_koan_root, tmp_path):
        proj = tmp_path / "noproj"
        proj.mkdir()
        snap = snapshot_core_files(str(fake_koan_root), str(proj))
        assert "project:.env" not in snap

    def test_no_project_path(self, fake_koan_root):
        snap = snapshot_core_files(str(fake_koan_root), None)
        # Should only contain koan root paths
        assert all(not p.startswith("project:") for p in snap)


class TestCheckCoreFiles:
    def test_no_changes(self, fake_koan_root):
        before = snapshot_core_files(str(fake_koan_root))
        warnings = check_core_files(str(fake_koan_root), before)
        assert warnings == []

    def test_file_removed(self, fake_koan_root):
        before = snapshot_core_files(str(fake_koan_root))
        (fake_koan_root / "projects.yaml").unlink()
        warnings = check_core_files(str(fake_koan_root), before)
        assert len(warnings) == 1
        assert "projects.yaml" in warnings[0]

    def test_directory_removed(self, fake_koan_root):
        before = snapshot_core_files(str(fake_koan_root))
        import shutil
        shutil.rmtree(fake_koan_root / "instance" / "memory")
        warnings = check_core_files(str(fake_koan_root), before)
        assert any("instance/memory/" in w for w in warnings)

    def test_multiple_removals(self, fake_koan_root):
        before = snapshot_core_files(str(fake_koan_root))
        (fake_koan_root / "projects.yaml").unlink()
        (fake_koan_root / "instance" / "soul.md").unlink()
        warnings = check_core_files(str(fake_koan_root), before)
        assert len(warnings) == 2

    def test_project_env_removed(self, fake_koan_root, fake_project):
        before = snapshot_core_files(str(fake_koan_root), str(fake_project))
        (fake_project / ".env").unlink()
        warnings = check_core_files(str(fake_koan_root), before, str(fake_project))
        assert any("Project file disappeared: .env" in w for w in warnings)

    def test_project_claudemd_removed(self, fake_koan_root, fake_project):
        before = snapshot_core_files(str(fake_koan_root), str(fake_project))
        assert "project:CLAUDE.md" in before
        (fake_project / "CLAUDE.md").unlink()
        warnings = check_core_files(str(fake_koan_root), before, str(fake_project))
        assert any("Project file disappeared: CLAUDE.md" in w for w in warnings)

    def test_file_added_no_warning(self, fake_koan_root):
        """Adding new files should not trigger warnings."""
        # Snapshot without projects.yaml
        (fake_koan_root / "projects.yaml").unlink()
        before = snapshot_core_files(str(fake_koan_root))
        # Recreate it
        (fake_koan_root / "projects.yaml").write_text("projects: []\n")
        warnings = check_core_files(str(fake_koan_root), before)
        assert warnings == []

    def test_empty_snapshot_no_warnings(self, tmp_path):
        """If nothing existed before, nothing can disappear."""
        before = snapshot_core_files(str(tmp_path))
        warnings = check_core_files(str(tmp_path), before)
        assert warnings == []


class TestSnapshotWithBackup:
    def test_creates_backup_of_core_files(self, fake_koan_root):
        snap = snapshot_with_backup(str(fake_koan_root))
        assert snap.backup_dir is not None
        backup = Path(snap.backup_dir)
        # Should have backed up all non-directory core files
        assert (backup / "projects.yaml").is_file()
        assert (backup / "instance" / "missions.md").is_file()
        assert (backup / "instance" / "config.yaml").is_file()
        assert (backup / "instance" / "soul.md").is_file()
        snap.cleanup()

    def test_backup_content_matches_original(self, fake_koan_root):
        snap = snapshot_with_backup(str(fake_koan_root))
        original = (fake_koan_root / "projects.yaml").read_text()
        backup = (Path(snap.backup_dir) / "projects.yaml").read_text()
        assert original == backup
        snap.cleanup()

    def test_cleanup_removes_backup(self, fake_koan_root):
        snap = snapshot_with_backup(str(fake_koan_root))
        backup_dir = snap.backup_dir
        assert Path(backup_dir).exists()
        snap.cleanup()
        assert not Path(backup_dir).exists()
        assert snap.backup_dir is None

    def test_double_cleanup_is_safe(self, fake_koan_root):
        snap = snapshot_with_backup(str(fake_koan_root))
        snap.cleanup()
        snap.cleanup()  # Should not raise

    def test_present_matches_snapshot_core_files(self, fake_koan_root):
        snap = snapshot_with_backup(str(fake_koan_root))
        plain = snapshot_core_files(str(fake_koan_root))
        assert snap.present == plain
        snap.cleanup()

    def test_with_project_path(self, fake_koan_root, fake_project):
        snap = snapshot_with_backup(str(fake_koan_root), str(fake_project))
        assert "project:CLAUDE.md" in snap.present
        assert "project:.env" in snap.present
        assert snap.project_path == str(fake_project)
        snap.cleanup()


class TestRestoreMissingFiles:
    def test_nothing_missing_returns_empty(self, fake_koan_root):
        snap = snapshot_with_backup(str(fake_koan_root))
        restored, failed = restore_missing_files(str(fake_koan_root), snap)
        assert restored == []
        assert failed == []
        snap.cleanup()

    def test_restore_core_file_from_backup(self, fake_koan_root):
        snap = snapshot_with_backup(str(fake_koan_root))
        # Delete a core file
        (fake_koan_root / "projects.yaml").unlink()
        assert not (fake_koan_root / "projects.yaml").exists()

        restored, failed = restore_missing_files(str(fake_koan_root), snap)
        assert any("projects.yaml" in r for r in restored)
        assert failed == []
        # File should be restored
        assert (fake_koan_root / "projects.yaml").is_file()
        assert (fake_koan_root / "projects.yaml").read_text() == "projects: []\n"
        snap.cleanup()

    def test_restore_multiple_core_files(self, fake_koan_root):
        snap = snapshot_with_backup(str(fake_koan_root))
        (fake_koan_root / "projects.yaml").unlink()
        (fake_koan_root / "instance" / "soul.md").unlink()

        restored, failed = restore_missing_files(str(fake_koan_root), snap)
        assert len(restored) == 2
        assert failed == []
        assert (fake_koan_root / "projects.yaml").is_file()
        assert (fake_koan_root / "instance" / "soul.md").is_file()
        snap.cleanup()

    def test_directory_loss_is_unrecoverable(self, fake_koan_root):
        snap = snapshot_with_backup(str(fake_koan_root))
        import shutil
        shutil.rmtree(fake_koan_root / "instance" / "memory")

        restored, failed = restore_missing_files(str(fake_koan_root), snap)
        assert any("instance/memory/" in f for f in failed)
        snap.cleanup()

    def test_restore_project_claudemd_from_git(self, fake_koan_root, fake_project):
        snap = snapshot_with_backup(str(fake_koan_root), str(fake_project))
        (fake_project / "CLAUDE.md").unlink()

        with patch("app.core_files._restore_from_git", return_value=True) as mock_git:
            restored, failed = restore_missing_files(
                str(fake_koan_root), snap, str(fake_project),
            )
        assert any("CLAUDE.md" in r for r in restored)
        assert failed == []
        mock_git.assert_called_once_with(str(fake_project), "CLAUDE.md")
        snap.cleanup()

    def test_project_file_git_restore_fails(self, fake_koan_root, fake_project):
        snap = snapshot_with_backup(str(fake_koan_root), str(fake_project))
        (fake_project / "CLAUDE.md").unlink()

        with patch("app.core_files._restore_from_git", return_value=False):
            restored, failed = restore_missing_files(
                str(fake_koan_root), snap, str(fake_project),
            )
        assert restored == []
        assert any("CLAUDE.md" in f for f in failed)
        snap.cleanup()

    def test_plain_set_snapshot_fallback(self, fake_koan_root, fake_project):
        """Works with legacy Set[str] snapshot (no backup dir)."""
        before = snapshot_core_files(str(fake_koan_root), str(fake_project))
        (fake_project / "CLAUDE.md").unlink()

        with patch("app.core_files._restore_from_git", return_value=True):
            restored, failed = restore_missing_files(
                str(fake_koan_root), before, str(fake_project),
            )
        assert any("CLAUDE.md" in r for r in restored)
        assert failed == []

    def test_plain_set_no_backup_for_core_files(self, fake_koan_root):
        """With plain set (no backup), core files can't be restored."""
        before = snapshot_core_files(str(fake_koan_root))
        (fake_koan_root / "projects.yaml").unlink()

        restored, failed = restore_missing_files(str(fake_koan_root), before)
        assert restored == []
        assert any("projects.yaml" in f for f in failed)


class TestRestoreFromGit:
    def test_tracked_file_restores(self, tmp_path):
        """Integration: git init, add, commit, delete, restore."""
        proj = tmp_path / "repo"
        proj.mkdir()
        (proj / "CLAUDE.md").write_text("# Test\n")

        subprocess.run(["git", "init"], cwd=str(proj), capture_output=True)
        subprocess.run(["git", "add", "CLAUDE.md"], cwd=str(proj), capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(proj), capture_output=True,
            env={**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
                 "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t"},
        )
        (proj / "CLAUDE.md").unlink()
        assert not (proj / "CLAUDE.md").exists()

        result = _restore_from_git(str(proj), "CLAUDE.md")
        assert result is True
        assert (proj / "CLAUDE.md").is_file()
        assert (proj / "CLAUDE.md").read_text() == "# Test\n"

    def test_untracked_file_fails(self, tmp_path):
        proj = tmp_path / "repo"
        proj.mkdir()
        subprocess.run(["git", "init"], cwd=str(proj), capture_output=True)

        result = _restore_from_git(str(proj), "nonexistent.md")
        assert result is False

    def test_no_git_repo_fails(self, tmp_path):
        result = _restore_from_git(str(tmp_path), "CLAUDE.md")
        assert result is False


class TestCheckCoreFilesWithSnapshot:
    """Verify check_core_files works with CoreSnapshot objects."""

    def test_accepts_core_snapshot(self, fake_koan_root):
        snap = snapshot_with_backup(str(fake_koan_root))
        warnings = check_core_files(str(fake_koan_root), snap)
        assert warnings == []
        snap.cleanup()

    def test_detects_missing_with_snapshot(self, fake_koan_root):
        snap = snapshot_with_backup(str(fake_koan_root))
        (fake_koan_root / "projects.yaml").unlink()
        warnings = check_core_files(str(fake_koan_root), snap)
        assert len(warnings) == 1
        assert "projects.yaml" in warnings[0]
        snap.cleanup()


class TestLogIntegrityWarnings:
    def test_no_warnings(self, capsys):
        log_integrity_warnings([])
        assert capsys.readouterr().err == ""

    def test_with_warnings(self, capsys):
        log_integrity_warnings(["Core file disappeared: projects.yaml"])
        err = capsys.readouterr().err
        assert "INTEGRITY CHECK FAILED" in err
        assert "projects.yaml" in err


class TestLogRestorations:
    def test_no_restorations(self, capsys):
        log_restorations([])
        assert capsys.readouterr().err == ""

    def test_with_restorations(self, capsys):
        log_restorations(["Restored core file from backup: projects.yaml"])
        err = capsys.readouterr().err
        assert "AUTO-RECOVERY" in err
        assert "projects.yaml" in err
