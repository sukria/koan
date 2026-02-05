"""Tests for migrate_memory.py — one-shot flat→hybrid migration."""

from pathlib import Path
from unittest import mock

import pytest


@pytest.fixture
def memory_dir(tmp_path):
    """Create a flat memory structure for migration testing."""
    mem = tmp_path / "instance" / "memory"
    mem.mkdir(parents=True)
    return mem


@pytest.fixture
def patched_migrate(tmp_path, memory_dir):
    """Import migrate_memory with patched module-level constants."""
    import importlib

    with mock.patch.dict("os.environ", {"KOAN_ROOT": str(tmp_path)}):
        import app.migrate_memory as mod

        importlib.reload(mod)
        # Ensure module constants point to our tmp dirs
        mod.KOAN_ROOT = tmp_path
        mod.INSTANCE = tmp_path / "instance"
        mod.MEMORY = memory_dir
        yield mod


class TestMigrate:
    def test_no_memory_dir(self, patched_migrate, tmp_path, capsys):
        """When memory dir doesn't exist, prints error and returns."""
        patched_migrate.MEMORY = tmp_path / "nonexistent"
        patched_migrate.migrate()
        assert "No memory directory found" in capsys.readouterr().out

    def test_creates_global_and_projects_dirs(self, patched_migrate, memory_dir):
        """Migration creates global/ and projects/default/ subdirs."""
        patched_migrate.migrate()
        assert (memory_dir / "global").is_dir()
        assert (memory_dir / "projects" / "default").is_dir()

    def test_moves_global_files(self, patched_migrate, memory_dir):
        """Global files (human-preferences, strategy, etc.) move to global/."""
        for name in ["human-preferences.md", "strategy.md", "genesis.md"]:
            (memory_dir / name).write_text(f"# {name}")

        patched_migrate.migrate()

        for name in ["human-preferences.md", "strategy.md", "genesis.md"]:
            assert (memory_dir / "global" / name).exists()
            assert not (memory_dir / name).exists()

    def test_moves_project_files(self, patched_migrate, memory_dir):
        """Project files (learnings, context) move to projects/default/."""
        (memory_dir / "learnings.md").write_text("# Learnings")
        (memory_dir / "context.md").write_text("# Context")

        patched_migrate.migrate()

        assert (memory_dir / "projects" / "default" / "learnings.md").exists()
        assert (memory_dir / "projects" / "default" / "context.md").exists()
        assert not (memory_dir / "learnings.md").exists()
        assert not (memory_dir / "context.md").exists()

    def test_creates_learnings_if_missing(self, patched_migrate, memory_dir):
        """If learnings.md doesn't exist, creates it in projects/default/."""
        patched_migrate.migrate()
        learnings = memory_dir / "projects" / "default" / "learnings.md"
        assert learnings.exists()
        assert "Learnings" in learnings.read_text()

    def test_keeps_summary_at_root(self, patched_migrate, memory_dir):
        """summary.md stays at memory root, not moved."""
        (memory_dir / "summary.md").write_text("# Summary")
        patched_migrate.migrate()
        assert (memory_dir / "summary.md").exists()
        assert not (memory_dir / "global" / "summary.md").exists()

    def test_creates_summary_if_missing(self, patched_migrate, memory_dir):
        """If summary.md doesn't exist, creates it at root."""
        patched_migrate.migrate()
        assert (memory_dir / "summary.md").exists()

    def test_skips_missing_global_files(self, patched_migrate, memory_dir):
        """Files that don't exist are silently skipped."""
        # Only create one of the global files
        (memory_dir / "strategy.md").write_text("# Strategy")
        patched_migrate.migrate()
        assert (memory_dir / "global" / "strategy.md").exists()
        assert not (memory_dir / "global" / "human-preferences.md").exists()

    def test_idempotent_dirs(self, patched_migrate, memory_dir):
        """Running twice doesn't fail (dirs already exist)."""
        patched_migrate.migrate()
        # Second run — dirs exist, no files to move
        patched_migrate.migrate()
        assert (memory_dir / "global").is_dir()
        assert (memory_dir / "projects" / "default").is_dir()

    def test_full_migration(self, patched_migrate, memory_dir, capsys):
        """End-to-end: flat structure → hybrid structure."""
        (memory_dir / "human-preferences.md").write_text("prefs")
        (memory_dir / "strategy.md").write_text("strat")
        (memory_dir / "learnings.md").write_text("learn")
        (memory_dir / "summary.md").write_text("summary")

        patched_migrate.migrate()

        out = capsys.readouterr().out
        assert "Migration complete" in out
        assert (memory_dir / "global" / "human-preferences.md").read_text() == "prefs"
        assert (memory_dir / "global" / "strategy.md").read_text() == "strat"
        assert (memory_dir / "projects" / "default" / "learnings.md").read_text() == "learn"
        assert (memory_dir / "summary.md").read_text() == "summary"
