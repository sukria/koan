"""Tests for workspace_discovery.py."""

import os
from pathlib import Path

import pytest

from app.workspace_discovery import discover_workspace_projects


@pytest.fixture
def workspace(tmp_path):
    """Create a workspace directory under a mock KOAN_ROOT."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return tmp_path


def test_empty_workspace(workspace):
    """Empty workspace returns empty list."""
    result = discover_workspace_projects(str(workspace))
    assert result == []


def test_no_workspace_dir(tmp_path):
    """Missing workspace/ returns empty list (not an error)."""
    result = discover_workspace_projects(str(tmp_path))
    assert result == []


def test_direct_directories(workspace):
    """Direct directories are discovered."""
    ws = workspace / "workspace"
    (ws / "alpha").mkdir()
    (ws / "beta").mkdir()
    (ws / "gamma").mkdir()

    result = discover_workspace_projects(str(workspace))
    assert len(result) == 3
    assert result[0][0] == "alpha"
    assert result[1][0] == "beta"
    assert result[2][0] == "gamma"


def test_symlinks_resolved(workspace):
    """Symlinks are resolved to their real paths."""
    ws = workspace / "workspace"
    real_dir = workspace / "real-project"
    real_dir.mkdir()
    (ws / "my-link").symlink_to(real_dir)

    result = discover_workspace_projects(str(workspace))
    assert len(result) == 1
    assert result[0][0] == "my-link"
    assert result[0][1] == str(real_dir.resolve())


def test_broken_symlinks_skipped(workspace):
    """Broken symlinks are skipped with a warning."""
    ws = workspace / "workspace"
    (ws / "broken").symlink_to("/nonexistent/path")
    (ws / "good").mkdir()

    result = discover_workspace_projects(str(workspace))
    assert len(result) == 1
    assert result[0][0] == "good"


def test_symlink_loops_skipped(workspace):
    """Symlink loops are skipped without crashing."""
    ws = workspace / "workspace"
    link1 = ws / "loop1"
    link2 = ws / "loop2"
    link1.symlink_to(link2)
    link2.symlink_to(link1)
    (ws / "good").mkdir()

    result = discover_workspace_projects(str(workspace))
    assert len(result) == 1
    assert result[0][0] == "good"


def test_hidden_directories_skipped(workspace):
    """Directories starting with . are skipped."""
    ws = workspace / "workspace"
    (ws / ".git").mkdir()
    (ws / "__pycache__").mkdir()  # Not hidden but shows non-hidden works
    (ws / ".hidden").mkdir()
    (ws / "visible").mkdir()

    result = discover_workspace_projects(str(workspace))
    names = [n for n, _ in result]
    assert "visible" in names
    assert "__pycache__" in names  # Not hidden (doesn't start with .)
    assert ".git" not in names
    assert ".hidden" not in names


def test_files_skipped(workspace):
    """Regular files in workspace are ignored."""
    ws = workspace / "workspace"
    (ws / "README.md").write_text("# Docs")
    (ws / "notes.txt").write_text("notes")
    (ws / "real-project").mkdir()

    result = discover_workspace_projects(str(workspace))
    assert len(result) == 1
    assert result[0][0] == "real-project"


def test_sorted_alphabetically(workspace):
    """Results are sorted case-insensitively."""
    ws = workspace / "workspace"
    (ws / "Zebra").mkdir()
    (ws / "alpha").mkdir()
    (ws / "Beta").mkdir()

    result = discover_workspace_projects(str(workspace))
    names = [n for n, _ in result]
    assert names == ["alpha", "Beta", "Zebra"]


def test_resolved_paths_are_absolute(workspace):
    """All returned paths are absolute."""
    ws = workspace / "workspace"
    (ws / "proj").mkdir()

    result = discover_workspace_projects(str(workspace))
    assert Path(result[0][1]).is_absolute()
