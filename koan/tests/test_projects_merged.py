"""Tests for projects_merged.py."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from app.projects_merged import (
    get_all_projects,
    refresh_projects,
    get_warnings,
    invalidate_cache,
    get_github_url,
    set_github_url,
    get_github_url_cache,
    clear_github_url_cache,
)


@pytest.fixture(autouse=True)
def clean_cache():
    """Reset module caches before each test."""
    invalidate_cache()
    clear_github_url_cache()
    yield
    invalidate_cache()
    clear_github_url_cache()


@pytest.fixture
def koan_root(tmp_path):
    """Create a KOAN_ROOT with workspace/ directory."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return tmp_path


def _write_projects_yaml(root, content):
    """Write a projects.yaml file."""
    (root / "projects.yaml").write_text(content)


class TestGetAllProjects:
    def test_workspace_only(self, koan_root):
        """Workspace projects discovered when no yaml."""
        ws = koan_root / "workspace"
        (ws / "alpha").mkdir()
        (ws / "beta").mkdir()

        result = get_all_projects(str(koan_root))
        assert len(result) == 2
        assert result[0][0] == "alpha"
        assert result[1][0] == "beta"

    def test_yaml_only(self, koan_root):
        """Yaml-only projects still work."""
        proj_dir = koan_root / "myapp"
        proj_dir.mkdir()
        _write_projects_yaml(koan_root, f"""
projects:
  myapp:
    path: "{proj_dir}"
""")
        result = get_all_projects(str(koan_root))
        assert len(result) == 1
        assert result[0][0] == "myapp"

    def test_mixed_sources(self, koan_root):
        """Yaml and workspace projects merge."""
        proj_dir = koan_root / "yaml-proj"
        proj_dir.mkdir()
        _write_projects_yaml(koan_root, f"""
projects:
  yaml-proj:
    path: "{proj_dir}"
""")
        ws = koan_root / "workspace"
        (ws / "ws-proj").mkdir()

        result = get_all_projects(str(koan_root))
        names = [n for n, _ in result]
        assert "yaml-proj" in names
        assert "ws-proj" in names

    def test_yaml_wins_on_duplicate(self, koan_root):
        """When same name exists in both, yaml path wins."""
        yaml_dir = koan_root / "yaml-path"
        yaml_dir.mkdir()
        _write_projects_yaml(koan_root, f"""
projects:
  myproj:
    path: "{yaml_dir}"
""")
        ws = koan_root / "workspace"
        (ws / "myproj").mkdir()

        result = get_all_projects(str(koan_root))
        assert len(result) == 1
        assert result[0][0] == "myproj"
        assert result[0][1] == str(yaml_dir)

    def test_duplicate_warning(self, koan_root):
        """Duplicate with different paths generates a warning."""
        yaml_dir = koan_root / "yaml-path"
        yaml_dir.mkdir()
        _write_projects_yaml(koan_root, f"""
projects:
  myproj:
    path: "{yaml_dir}"
""")
        ws = koan_root / "workspace"
        (ws / "myproj").mkdir()

        refresh_projects(str(koan_root))
        warnings = get_warnings()
        assert len(warnings) == 1
        assert "Duplicate" in warnings[0]
        assert "myproj" in warnings[0]

    def test_no_warning_when_paths_identical(self, koan_root):
        """Duplicate with identical paths (yaml path == workspace path) emits no warning."""
        ws = koan_root / "workspace"
        (ws / "myproj").mkdir()
        ws_path = str(ws / "myproj")
        # yaml entry points to the exact same path as workspace discovery
        _write_projects_yaml(koan_root, f"""
projects:
  myproj:
    path: "{ws_path}"
""")

        refresh_projects(str(koan_root))
        warnings = get_warnings()
        assert not any("Duplicate" in w for w in warnings)

    def test_workspace_with_yaml_override_no_path(self, koan_root):
        """Yaml entry without path uses workspace path for the project."""
        ws = koan_root / "workspace"
        (ws / "myproj").mkdir()
        _write_projects_yaml(koan_root, """
projects:
  myproj:
    models:
      mission: "opus"
""")
        result = get_all_projects(str(koan_root))
        assert len(result) == 1
        assert result[0][0] == "myproj"
        # Path comes from workspace
        assert "workspace" in result[0][1]


class TestProjectLimit:
    def test_limit_enforced(self, koan_root):
        """Projects exceeding 50 are truncated."""
        ws = koan_root / "workspace"
        for i in range(55):
            (ws / f"proj-{i:03d}").mkdir()

        result = get_all_projects(str(koan_root))
        assert len(result) == 50

        warnings = get_warnings()
        assert any("55 projects" in w for w in warnings)


class TestCaching:
    def test_cache_hit(self, koan_root):
        """Second call returns cached result without re-scanning."""
        ws = koan_root / "workspace"
        (ws / "proj").mkdir()

        result1 = get_all_projects(str(koan_root))
        # Second call without changes — should return cached result
        result2 = get_all_projects(str(koan_root))
        assert result1 == result2

    def test_invalidate_and_rescan(self, koan_root):
        """invalidate_cache() forces re-scan."""
        ws = koan_root / "workspace"
        (ws / "proj").mkdir()

        result1 = get_all_projects(str(koan_root))
        assert len(result1) == 1

        (ws / "proj").rmdir()
        invalidate_cache()
        result2 = get_all_projects(str(koan_root))
        assert len(result2) == 0

    def test_refresh_updates_cache(self, koan_root):
        """refresh_projects() updates the cache."""
        ws = koan_root / "workspace"
        (ws / "proj1").mkdir()

        get_all_projects(str(koan_root))
        (ws / "proj2").mkdir()

        result = refresh_projects(str(koan_root))
        assert len(result) == 2

    def test_workspace_change_invalidates_cache(self, koan_root):
        """Adding a project to workspace/ invalidates cache automatically."""
        ws = koan_root / "workspace"
        (ws / "proj1").mkdir()

        result1 = get_all_projects(str(koan_root))
        assert len(result1) == 1

        # Adding a new directory changes workspace/ mtime
        (ws / "proj2").mkdir()
        # Ensure mtime differs (filesystem resolution can be 1s on Linux)
        import os
        os.utime(str(ws), (ws.stat().st_atime + 1, ws.stat().st_mtime + 1))

        # get_all_projects should detect the mtime change and re-scan
        result2 = get_all_projects(str(koan_root))
        assert len(result2) == 2
        names = [n for n, _ in result2]
        assert "proj1" in names
        assert "proj2" in names

    def test_workspace_mtime_no_change_cache_hit(self, koan_root):
        """When workspace/ mtime unchanged, cache is used."""
        ws = koan_root / "workspace"
        (ws / "proj").mkdir()

        result1 = get_all_projects(str(koan_root))
        # Modify a file inside the workspace project (doesn't change ws/ mtime)
        (ws / "proj" / "README.md").write_text("hello")
        result2 = get_all_projects(str(koan_root))
        assert result1 == result2

    def test_no_workspace_dir_cache_works(self, tmp_path):
        """Cache works even when workspace/ doesn't exist."""
        proj_dir = tmp_path / "myapp"
        proj_dir.mkdir()
        _write_projects_yaml(tmp_path, f"""
projects:
  myapp:
    path: "{proj_dir}"
""")
        result1 = get_all_projects(str(tmp_path))
        result2 = get_all_projects(str(tmp_path))
        assert result1 == result2 == [("myapp", str(proj_dir))]

    def test_workspace_created_after_cache_invalidates(self, tmp_path):
        """Creating workspace/ after initial cache triggers rescan."""
        proj_dir = tmp_path / "myapp"
        proj_dir.mkdir()
        _write_projects_yaml(tmp_path, f"""
projects:
  myapp:
    path: "{proj_dir}"
""")
        result1 = get_all_projects(str(tmp_path))
        assert len(result1) == 1

        # Create workspace/ with a new project
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "ws-proj").mkdir()

        result2 = get_all_projects(str(tmp_path))
        assert len(result2) == 2
        names = [n for n, _ in result2]
        assert "myapp" in names
        assert "ws-proj" in names


class TestGithubUrlCache:
    def test_set_and_get(self):
        set_github_url("myproj", "https://github.com/me/myproj")
        assert get_github_url("myproj") == "https://github.com/me/myproj"

    def test_get_missing(self):
        assert get_github_url("nonexistent") is None

    def test_get_cache_dict(self):
        set_github_url("a", "url-a")
        set_github_url("b", "url-b")
        cache = get_github_url_cache()
        assert cache == {"a": "url-a", "b": "url-b"}

    def test_clear(self):
        set_github_url("a", "url-a")
        clear_github_url_cache()
        assert get_github_url("a") is None


class TestEdgeCases:
    def test_no_workspace_no_yaml(self, tmp_path):
        """No workspace, no yaml — returns empty."""
        result = get_all_projects(str(tmp_path))
        assert result == []

    def test_invalid_yaml_still_returns_workspace(self, koan_root):
        """Bad yaml doesn't prevent workspace discovery."""
        (koan_root / "projects.yaml").write_text("{{invalid yaml")
        ws = koan_root / "workspace"
        (ws / "proj").mkdir()

        result = get_all_projects(str(koan_root))
        assert len(result) == 1
        assert result[0][0] == "proj"
        warnings = get_warnings()
        assert any("projects.yaml" in w for w in warnings)

    def test_sorted_output(self, koan_root):
        """Output is sorted case-insensitively."""
        ws = koan_root / "workspace"
        (ws / "Zebra").mkdir()
        (ws / "alpha").mkdir()

        proj_dir = koan_root / "mypath"
        proj_dir.mkdir()
        _write_projects_yaml(koan_root, f"""
projects:
  Middle:
    path: "{proj_dir}"
""")
        result = get_all_projects(str(koan_root))
        names = [n for n, _ in result]
        assert names == ["alpha", "Middle", "Zebra"]
