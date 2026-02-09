"""Tests for koan/app/projects_config.py — project configuration loader."""

import pytest
from pathlib import Path
from unittest.mock import patch

from app.projects_config import (
    load_projects_config,
    get_projects_from_config,
    get_project_config,
    get_project_auto_merge,
    validate_project_paths,
    _validate_config,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def koan_root(tmp_path):
    """A temporary KOAN_ROOT with no projects.yaml."""
    return str(tmp_path)


def _write_yaml(koan_root, content):
    """Write projects.yaml content to the given root."""
    Path(koan_root, "projects.yaml").write_text(content)


def _minimal_config(koan_root, extra=""):
    """Write a minimal valid projects.yaml."""
    _write_yaml(koan_root, f"""
projects:
  myapp:
    path: "/tmp/myapp"
{extra}""")


# ---------------------------------------------------------------------------
# load_projects_config
# ---------------------------------------------------------------------------


class TestLoadProjectsConfig:
    """Tests for load_projects_config()."""

    def test_returns_none_when_no_file(self, koan_root):
        assert load_projects_config(koan_root) is None

    def test_returns_none_for_empty_file(self, koan_root):
        _write_yaml(koan_root, "")
        assert load_projects_config(koan_root) is None

    def test_loads_minimal_config(self, koan_root):
        _minimal_config(koan_root)
        config = load_projects_config(koan_root)
        assert config is not None
        assert "projects" in config
        assert "myapp" in config["projects"]

    def test_loads_config_with_defaults(self, koan_root):
        _write_yaml(koan_root, """
defaults:
  git_auto_merge:
    enabled: true
    base_branch: main
projects:
  app:
    path: /tmp/app
""")
        config = load_projects_config(koan_root)
        assert config["defaults"]["git_auto_merge"]["enabled"] is True

    def test_raises_on_invalid_yaml(self, koan_root):
        _write_yaml(koan_root, ":\n  invalid: [yaml\n  unclosed")
        with pytest.raises(ValueError, match="Invalid YAML"):
            load_projects_config(koan_root)

    def test_raises_when_not_a_dict(self, koan_root):
        _write_yaml(koan_root, "- this is a list\n- not a dict")
        with pytest.raises(ValueError, match="must be a YAML mapping"):
            load_projects_config(koan_root)

    def test_raises_when_projects_missing(self, koan_root):
        _write_yaml(koan_root, "defaults:\n  enabled: true\n")
        with pytest.raises(ValueError, match="'projects' section is required"):
            load_projects_config(koan_root)

    def test_raises_when_projects_empty(self, koan_root):
        _write_yaml(koan_root, "projects: {}")
        with pytest.raises(ValueError, match="at least one project"):
            load_projects_config(koan_root)

    def test_raises_when_projects_not_a_dict(self, koan_root):
        _write_yaml(koan_root, "projects:\n  - item1\n  - item2")
        with pytest.raises(ValueError, match="must be a mapping"):
            load_projects_config(koan_root)

    def test_raises_when_project_has_no_config(self, koan_root):
        _write_yaml(koan_root, "projects:\n  myapp:")
        with pytest.raises(ValueError, match="has no configuration"):
            load_projects_config(koan_root)

    def test_raises_when_project_missing_path(self, koan_root):
        _write_yaml(koan_root, "projects:\n  myapp:\n    other: value")
        with pytest.raises(ValueError, match="missing required 'path'"):
            load_projects_config(koan_root)

    def test_raises_when_path_is_empty(self, koan_root):
        _write_yaml(koan_root, 'projects:\n  myapp:\n    path: ""')
        with pytest.raises(ValueError, match="invalid path"):
            load_projects_config(koan_root)

    def test_raises_when_project_is_not_dict(self, koan_root):
        _write_yaml(koan_root, "projects:\n  myapp: just-a-string")
        with pytest.raises(ValueError, match="must be a mapping"):
            load_projects_config(koan_root)

    def test_raises_when_defaults_is_not_dict(self, koan_root):
        _write_yaml(koan_root, "defaults: not-a-dict\nprojects:\n  a:\n    path: /tmp/a")
        with pytest.raises(ValueError, match="'defaults' must be a mapping"):
            load_projects_config(koan_root)

    def test_raises_when_too_many_projects(self, koan_root):
        projects = "\n".join(
            f"  p{i}:\n    path: /tmp/p{i}" for i in range(51)
        )
        _write_yaml(koan_root, f"projects:\n{projects}")
        with pytest.raises(ValueError, match="Max 50 projects"):
            load_projects_config(koan_root)

    def test_50_projects_is_ok(self, koan_root):
        projects = "\n".join(
            f"  p{i}:\n    path: /tmp/p{i}" for i in range(50)
        )
        _write_yaml(koan_root, f"projects:\n{projects}")
        config = load_projects_config(koan_root)
        assert len(config["projects"]) == 50

    def test_multiple_projects(self, koan_root):
        _write_yaml(koan_root, """
projects:
  frontend:
    path: /tmp/frontend
  backend:
    path: /tmp/backend
  koan:
    path: /tmp/koan
""")
        config = load_projects_config(koan_root)
        assert len(config["projects"]) == 3


# ---------------------------------------------------------------------------
# validate_project_paths
# ---------------------------------------------------------------------------


class TestValidateProjectPaths:
    """Tests for validate_project_paths() — filesystem checks."""

    def test_valid_paths(self, tmp_path):
        project_dir = tmp_path / "myapp"
        project_dir.mkdir()
        config = {"projects": {"myapp": {"path": str(project_dir)}}}
        assert validate_project_paths(config) is None

    def test_missing_path(self):
        config = {"projects": {"myapp": {"path": "/nonexistent/path/xyz"}}}
        result = validate_project_paths(config)
        assert result is not None
        assert "does not exist" in result
        assert "myapp" in result

    def test_empty_projects(self):
        config = {"projects": {}}
        assert validate_project_paths(config) is None

    def test_multiple_projects_one_missing(self, tmp_path):
        good_dir = tmp_path / "good"
        good_dir.mkdir()
        config = {
            "projects": {
                "good": {"path": str(good_dir)},
                "bad": {"path": "/nonexistent/xyz"},
            }
        }
        result = validate_project_paths(config)
        assert "bad" in result


# ---------------------------------------------------------------------------
# get_projects_from_config
# ---------------------------------------------------------------------------


class TestGetProjectsFromConfig:
    """Tests for get_projects_from_config()."""

    def test_extracts_name_path_tuples(self):
        config = {
            "projects": {
                "koan": {"path": "/home/koan"},
                "web": {"path": "/home/web"},
            }
        }
        result = get_projects_from_config(config)
        assert ("koan", "/home/koan") in result
        assert ("web", "/home/web") in result

    def test_sorted_alphabetically(self):
        config = {
            "projects": {
                "zebra": {"path": "/z"},
                "alpha": {"path": "/a"},
                "middle": {"path": "/m"},
            }
        }
        result = get_projects_from_config(config)
        assert result[0][0] == "alpha"
        assert result[1][0] == "middle"
        assert result[2][0] == "zebra"

    def test_case_insensitive_sort(self):
        config = {
            "projects": {
                "Bravo": {"path": "/b"},
                "alpha": {"path": "/a"},
            }
        }
        result = get_projects_from_config(config)
        assert result[0][0] == "alpha"
        assert result[1][0] == "Bravo"

    def test_strips_path_whitespace(self):
        config = {"projects": {"app": {"path": "  /tmp/app  "}}}
        result = get_projects_from_config(config)
        assert result[0] == ("app", "/tmp/app")

    def test_empty_projects(self):
        config = {"projects": {}}
        assert get_projects_from_config(config) == []

    def test_missing_projects_key(self):
        config = {}
        assert get_projects_from_config(config) == []

    def test_single_project(self):
        config = {"projects": {"solo": {"path": "/solo"}}}
        result = get_projects_from_config(config)
        assert result == [("solo", "/solo")]


# ---------------------------------------------------------------------------
# get_project_config
# ---------------------------------------------------------------------------


class TestGetProjectConfig:
    """Tests for get_project_config() — merged defaults + project overrides."""

    def test_project_inherits_defaults(self):
        config = {
            "defaults": {
                "git_auto_merge": {"enabled": True, "base_branch": "main"},
            },
            "projects": {
                "app": {"path": "/app"},
            },
        }
        result = get_project_config(config, "app")
        assert result["git_auto_merge"]["enabled"] is True
        assert result["git_auto_merge"]["base_branch"] == "main"

    def test_project_overrides_defaults(self):
        config = {
            "defaults": {
                "git_auto_merge": {"enabled": True, "base_branch": "main", "strategy": "squash"},
            },
            "projects": {
                "app": {
                    "path": "/app",
                    "git_auto_merge": {"base_branch": "staging"},
                },
            },
        }
        result = get_project_config(config, "app")
        # Overridden
        assert result["git_auto_merge"]["base_branch"] == "staging"
        # Inherited
        assert result["git_auto_merge"]["enabled"] is True
        assert result["git_auto_merge"]["strategy"] == "squash"

    def test_no_defaults_section(self):
        config = {
            "projects": {
                "app": {
                    "path": "/app",
                    "git_auto_merge": {"enabled": False},
                },
            },
        }
        result = get_project_config(config, "app")
        assert result["git_auto_merge"]["enabled"] is False

    def test_unknown_project_returns_defaults(self):
        config = {
            "defaults": {"git_auto_merge": {"enabled": True}},
            "projects": {},
        }
        result = get_project_config(config, "nonexistent")
        assert result["git_auto_merge"]["enabled"] is True

    def test_path_excluded_from_merged_config(self):
        config = {
            "projects": {
                "app": {"path": "/app", "git_auto_merge": {"enabled": True}},
            },
        }
        result = get_project_config(config, "app")
        assert "path" not in result

    def test_project_only_keys_included(self):
        config = {
            "defaults": {},
            "projects": {
                "app": {"path": "/app", "custom_key": "custom_value"},
            },
        }
        result = get_project_config(config, "app")
        assert result["custom_key"] == "custom_value"

    def test_scalar_defaults_overridden(self):
        config = {
            "defaults": {"cli_provider": "claude"},
            "projects": {
                "app": {"path": "/app", "cli_provider": "copilot"},
            },
        }
        result = get_project_config(config, "app")
        assert result["cli_provider"] == "copilot"

    def test_none_defaults_handled(self):
        config = {
            "defaults": None,
            "projects": {"app": {"path": "/app"}},
        }
        result = get_project_config(config, "app")
        assert isinstance(result, dict)

    def test_none_project_handled(self):
        config = {
            "defaults": {"git_auto_merge": {"enabled": True}},
            "projects": {"app": None},
        }
        # get_project_config for a project with None config
        result = get_project_config(config, "app")
        assert result["git_auto_merge"]["enabled"] is True


# ---------------------------------------------------------------------------
# get_project_auto_merge
# ---------------------------------------------------------------------------


class TestGetProjectAutoMerge:
    """Tests for get_project_auto_merge()."""

    def test_returns_defaults_when_no_override(self):
        config = {
            "defaults": {
                "git_auto_merge": {
                    "enabled": True,
                    "base_branch": "main",
                    "strategy": "squash",
                    "rules": [{"pattern": "koan/*"}],
                },
            },
            "projects": {"app": {"path": "/app"}},
        }
        result = get_project_auto_merge(config, "app")
        assert result["enabled"] is True
        assert result["base_branch"] == "main"
        assert result["strategy"] == "squash"
        assert len(result["rules"]) == 1

    def test_project_override(self):
        config = {
            "defaults": {
                "git_auto_merge": {"enabled": True, "strategy": "squash"},
            },
            "projects": {
                "app": {
                    "path": "/app",
                    "git_auto_merge": {"strategy": "merge", "base_branch": "develop"},
                },
            },
        }
        result = get_project_auto_merge(config, "app")
        assert result["enabled"] is True  # inherited
        assert result["strategy"] == "merge"  # overridden
        assert result["base_branch"] == "develop"  # overridden

    def test_sensible_defaults_when_nothing_configured(self):
        config = {"projects": {"app": {"path": "/app"}}}
        result = get_project_auto_merge(config, "app")
        assert result["enabled"] is False
        assert result["base_branch"] == "main"
        assert result["strategy"] == "squash"
        assert result["rules"] == []

    def test_unknown_project_uses_defaults(self):
        config = {
            "defaults": {"git_auto_merge": {"enabled": True}},
            "projects": {"app": {"path": "/app"}},
        }
        result = get_project_auto_merge(config, "unknown")
        assert result["enabled"] is True


# ---------------------------------------------------------------------------
# Integration: load + get
# ---------------------------------------------------------------------------


class TestIntegration:
    """End-to-end tests: load_projects_config → get_projects_from_config."""

    def test_full_workflow(self, koan_root):
        _write_yaml(koan_root, """
defaults:
  git_auto_merge:
    enabled: false
    base_branch: main
    strategy: squash

projects:
  koan:
    path: /home/koan
    git_auto_merge:
      enabled: false
      strategy: merge

  backend:
    path: /home/backend
    git_auto_merge:
      base_branch: staging

  frontend:
    path: /home/frontend
""")
        config = load_projects_config(koan_root)

        # Projects extraction
        projects = get_projects_from_config(config)
        assert len(projects) == 3
        assert projects[0][0] == "backend"  # sorted
        assert projects[1][0] == "frontend"
        assert projects[2][0] == "koan"

        # Koan auto-merge
        koan_am = get_project_auto_merge(config, "koan")
        assert koan_am["enabled"] is False
        assert koan_am["strategy"] == "merge"
        assert koan_am["base_branch"] == "main"  # inherited

        # Backend auto-merge
        backend_am = get_project_auto_merge(config, "backend")
        assert backend_am["base_branch"] == "staging"
        assert backend_am["strategy"] == "squash"  # inherited

        # Frontend auto-merge (pure defaults)
        frontend_am = get_project_auto_merge(config, "frontend")
        assert frontend_am["enabled"] is False
        assert frontend_am["base_branch"] == "main"
        assert frontend_am["strategy"] == "squash"

    def test_minimal_config(self, koan_root):
        _write_yaml(koan_root, """
projects:
  solo:
    path: /home/solo
""")
        config = load_projects_config(koan_root)
        projects = get_projects_from_config(config)
        assert projects == [("solo", "/home/solo")]

        am = get_project_auto_merge(config, "solo")
        assert am["enabled"] is False

    def test_projects_yaml_with_comments(self, koan_root):
        _write_yaml(koan_root, """
# This is a comment
defaults:
  # Another comment
  git_auto_merge:
    enabled: true
    base_branch: main

projects:
  myapp:
    path: /tmp/myapp
    # Per-project override
    git_auto_merge:
      strategy: rebase
""")
        config = load_projects_config(koan_root)
        assert config is not None
        am = get_project_auto_merge(config, "myapp")
        assert am["strategy"] == "rebase"
        assert am["enabled"] is True  # inherited from defaults


# ---------------------------------------------------------------------------
# get_known_projects integration with projects.yaml
# ---------------------------------------------------------------------------


class TestGetKnownProjectsWithYaml:
    """Tests for get_known_projects() when projects.yaml exists."""

    def test_projects_yaml_takes_priority(self, tmp_path, monkeypatch):
        from app import utils
        monkeypatch.setattr(utils, "KOAN_ROOT", tmp_path)
        monkeypatch.setenv("KOAN_PROJECTS", "envproject:/env/path")

        # Write projects.yaml
        (tmp_path / "projects.yaml").write_text("""
projects:
  yamlproject:
    path: /yaml/path
""")
        from app.utils import get_known_projects
        result = get_known_projects()
        assert len(result) == 1
        assert result[0] == ("yamlproject", "/yaml/path")

    def test_falls_back_to_env_when_no_yaml(self, tmp_path, monkeypatch):
        from app import utils
        monkeypatch.setattr(utils, "KOAN_ROOT", tmp_path)
        monkeypatch.setenv("KOAN_PROJECTS", "envproject:/env/path")

        # No projects.yaml
        from app.utils import get_known_projects
        result = get_known_projects()
        assert len(result) == 1
        assert result[0] == ("envproject", "/env/path")

    def test_falls_back_on_invalid_yaml(self, tmp_path, monkeypatch):
        from app import utils
        monkeypatch.setattr(utils, "KOAN_ROOT", tmp_path)
        monkeypatch.setenv("KOAN_PROJECTS", "fallback:/fallback")

        # Write broken projects.yaml
        (tmp_path / "projects.yaml").write_text("not valid: [yaml")
        from app.utils import get_known_projects
        result = get_known_projects()
        assert len(result) == 1
        assert result[0] == ("fallback", "/fallback")

    def test_falls_back_on_schema_error(self, tmp_path, monkeypatch):
        from app import utils
        monkeypatch.setattr(utils, "KOAN_ROOT", tmp_path)
        monkeypatch.setenv("KOAN_PROJECTS", "fallback:/fallback")

        # Valid YAML but missing 'projects' section
        (tmp_path / "projects.yaml").write_text("defaults:\n  enabled: true\n")
        from app.utils import get_known_projects
        result = get_known_projects()
        assert len(result) == 1
        assert result[0] == ("fallback", "/fallback")

    def test_legacy_project_path_no_longer_supported(self, tmp_path, monkeypatch):
        """KOAN_PROJECT_PATH is no longer a fallback — returns empty list."""
        from app import utils
        monkeypatch.setattr(utils, "KOAN_ROOT", tmp_path)
        monkeypatch.delenv("KOAN_PROJECTS", raising=False)
        monkeypatch.setenv("KOAN_PROJECT_PATH", "/legacy/path")

        # No projects.yaml
        from app.utils import get_known_projects
        result = get_known_projects()
        assert result == []


# ---------------------------------------------------------------------------
# get_auto_merge_config integration with projects.yaml
# ---------------------------------------------------------------------------


class TestAutoMergeConfigWithYaml:
    """Tests for get_auto_merge_config() when projects.yaml exists."""

    def test_reads_from_projects_yaml(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))

        (tmp_path / "projects.yaml").write_text("""
defaults:
  git_auto_merge:
    enabled: true
    strategy: squash
projects:
  myapp:
    path: /tmp/myapp
    git_auto_merge:
      strategy: merge
""")
        from app.config import get_auto_merge_config
        result = get_auto_merge_config({}, "myapp")
        assert result["strategy"] == "merge"
        assert result["enabled"] is True

    def test_falls_back_to_config_yaml_global(self, tmp_path, monkeypatch):
        """Without projects.yaml, config.yaml global settings are used (projects: section ignored)."""
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        # No projects.yaml

        config = {
            "git_auto_merge": {"enabled": True, "base_branch": "main", "strategy": "squash"},
            "projects": {"app": {"git_auto_merge": {"strategy": "rebase"}}},
        }
        from app.config import get_auto_merge_config
        result = get_auto_merge_config(config, "app")
        # config.yaml projects: section is ignored — global "squash" used
        assert result["strategy"] == "squash"
        assert result["enabled"] is True

    def test_unknown_project_in_yaml_falls_back(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))

        (tmp_path / "projects.yaml").write_text("""
projects:
  known:
    path: /tmp/known
""")
        config = {
            "git_auto_merge": {"enabled": True, "strategy": "squash"},
        }
        from app.config import get_auto_merge_config
        # 'unknown' is not in projects.yaml — falls back to config.yaml
        result = get_auto_merge_config(config, "unknown")
        assert result["enabled"] is True
        assert result["strategy"] == "squash"
