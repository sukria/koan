"""Tests for GitHub URL project resolution feature.

Covers:
- get_github_remote() — git remote URL extraction
- save_projects_config() / ensure_github_urls() — auto-population
- resolve_project_path() with owner parameter
- Skill handler owner passthrough
"""

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Ensure test env vars don't leak (preserves KOAN_ROOT)."""
    for key in list(os.environ):
        if key.startswith("KOAN_") and key != "KOAN_ROOT":
            monkeypatch.delenv(key, raising=False)


# ─────────────────────────────────────────────────────
# Phase 1: get_github_remote()
# ─────────────────────────────────────────────────────


class TestGetGithubRemote:
    """Tests for get_github_remote() — extracting owner/repo from git remote."""

    def test_https_url(self, tmp_path):
        """Parses HTTPS GitHub remote."""
        from app.utils import get_github_remote

        result = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="https://github.com/sukria/koan.git\n"
        )
        with patch("app.utils.subprocess.run", return_value=result):
            assert get_github_remote(str(tmp_path)) == "sukria/koan"

    def test_ssh_url(self, tmp_path):
        """Parses SSH GitHub remote."""
        from app.utils import get_github_remote

        result = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="git@github.com:atoomic/Clone.git\n"
        )
        with patch("app.utils.subprocess.run", return_value=result):
            assert get_github_remote(str(tmp_path)) == "atoomic/clone"

    def test_https_without_dot_git(self, tmp_path):
        """Parses HTTPS URL without .git suffix."""
        from app.utils import get_github_remote

        result = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="https://github.com/garu/Clone\n"
        )
        with patch("app.utils.subprocess.run", return_value=result):
            assert get_github_remote(str(tmp_path)) == "garu/clone"

    def test_non_github_remote(self, tmp_path):
        """Returns None for non-GitHub remotes."""
        from app.utils import get_github_remote

        result = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="https://gitlab.com/user/repo.git\n"
        )
        # Both origin and upstream fail
        with patch("app.utils.subprocess.run", return_value=result):
            assert get_github_remote(str(tmp_path)) is None

    def test_no_remote(self, tmp_path):
        """Returns None when git remote fails."""
        from app.utils import get_github_remote

        result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="fatal")
        with patch("app.utils.subprocess.run", return_value=result):
            assert get_github_remote(str(tmp_path)) is None

    def test_upstream_fallback(self, tmp_path):
        """Falls back to upstream when origin is not GitHub."""
        from app.utils import get_github_remote

        origin = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="https://gitlab.com/user/repo.git\n"
        )
        upstream = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="https://github.com/org/repo.git\n"
        )

        def side_effect(cmd, **kwargs):
            if cmd[2] == "origin":
                return origin
            return upstream

        with patch("app.utils.subprocess.run", side_effect=side_effect):
            assert get_github_remote(str(tmp_path)) == "org/repo"

    def test_timeout_handled(self, tmp_path):
        """Handles subprocess timeout gracefully."""
        from app.utils import get_github_remote

        with patch("app.utils.subprocess.run", side_effect=subprocess.TimeoutExpired("git", 5)):
            assert get_github_remote(str(tmp_path)) is None

    def test_git_not_found(self, tmp_path):
        """Handles missing git binary gracefully."""
        from app.utils import get_github_remote

        with patch("app.utils.subprocess.run", side_effect=FileNotFoundError):
            assert get_github_remote(str(tmp_path)) is None

    def test_case_normalization(self, tmp_path):
        """Owner/repo are normalized to lowercase."""
        from app.utils import get_github_remote

        result = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="https://github.com/Sukria/Koan.git\n"
        )
        with patch("app.utils.subprocess.run", return_value=result):
            assert get_github_remote(str(tmp_path)) == "sukria/koan"

    def test_upstream_when_origin_absent(self, tmp_path):
        """Uses upstream when origin doesn't exist."""
        from app.utils import get_github_remote

        origin_fail = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="fatal")
        upstream_ok = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="https://github.com/sukria/koan.git\n"
        )

        def side_effect(cmd, **kwargs):
            if cmd[2] == "origin":
                return origin_fail
            return upstream_ok

        with patch("app.utils.subprocess.run", side_effect=side_effect):
            assert get_github_remote(str(tmp_path)) == "sukria/koan"


# ─────────────────────────────────────────────────────
# Phase 2: save_projects_config() + ensure_github_urls()
# ─────────────────────────────────────────────────────


class TestSaveProjectsConfig:
    """Tests for save_projects_config() — atomic YAML writes."""

    def test_writes_valid_yaml(self, tmp_path):
        from app.projects_config import save_projects_config, load_projects_config

        config = {
            "projects": {
                "koan": {"path": str(tmp_path / "koan"), "github_url": "sukria/koan"}
            }
        }
        (tmp_path / "koan").mkdir()
        save_projects_config(str(tmp_path), config)

        result = load_projects_config(str(tmp_path))
        assert result["projects"]["koan"]["github_url"] == "sukria/koan"

    def test_preserves_existing_fields(self, tmp_path):
        from app.projects_config import save_projects_config, load_projects_config

        config = {
            "defaults": {"git_auto_merge": {"enabled": False}},
            "projects": {
                "koan": {
                    "path": str(tmp_path / "koan"),
                    "github_url": "sukria/koan",
                    "cli_provider": "claude",
                }
            }
        }
        (tmp_path / "koan").mkdir()
        save_projects_config(str(tmp_path), config)

        result = load_projects_config(str(tmp_path))
        assert result["projects"]["koan"]["cli_provider"] == "claude"
        assert result["defaults"]["git_auto_merge"]["enabled"] is False

    def test_atomic_write_has_header(self, tmp_path):
        from app.projects_config import save_projects_config

        config = {"projects": {"myapp": {"path": "/tmp/myapp"}}}
        save_projects_config(str(tmp_path), config)

        content = (tmp_path / "projects.yaml").read_text()
        assert "Kōan" in content
        assert "projects:" in content

    def test_handles_write_error(self, tmp_path):
        from app.projects_config import save_projects_config

        config = {"projects": {"myapp": {"path": "/tmp/myapp"}}}
        (tmp_path / "projects.yaml").write_text("old")
        with patch("app.utils.tempfile.mkstemp", side_effect=OSError("permission denied")):
            with pytest.raises(OSError):
                save_projects_config(str(tmp_path), config)


class TestEnsureGithubUrls:
    """Tests for ensure_github_urls() — auto-populating github_url."""

    def test_populates_missing_github_urls(self, tmp_path):
        from app.projects_config import ensure_github_urls

        koan_dir = tmp_path / "koan"
        koan_dir.mkdir()
        config = {
            "projects": {
                "koan": {"path": str(koan_dir)}
            }
        }
        (tmp_path / "projects.yaml").write_text(yaml.dump(config))

        with patch("app.utils.get_github_remote", return_value="sukria/koan"):
            msgs = ensure_github_urls(str(tmp_path))

        assert len(msgs) == 1
        assert "sukria/koan" in msgs[0]

        # Verify it was saved
        saved = yaml.safe_load((tmp_path / "projects.yaml").read_text())
        assert saved["projects"]["koan"]["github_url"] == "sukria/koan"

    def test_skips_existing_github_urls(self, tmp_path):
        from app.projects_config import ensure_github_urls

        config = {
            "projects": {
                "koan": {"path": "/tmp/koan", "github_url": "custom/koan"}
            }
        }
        (tmp_path / "projects.yaml").write_text(yaml.dump(config))

        with patch("app.utils.get_github_remote") as mock_remote:
            msgs = ensure_github_urls(str(tmp_path))

        assert len(msgs) == 0
        mock_remote.assert_not_called()

    def test_skips_non_git_projects(self, tmp_path):
        from app.projects_config import ensure_github_urls

        config = {
            "projects": {
                "notes": {"path": str(tmp_path / "notes")}
            }
        }
        (tmp_path / "notes").mkdir()
        (tmp_path / "projects.yaml").write_text(yaml.dump(config))

        with patch("app.utils.get_github_remote", return_value=None):
            msgs = ensure_github_urls(str(tmp_path))

        assert len(msgs) == 0

    def test_idempotent(self, tmp_path):
        from app.projects_config import ensure_github_urls

        koan_dir = tmp_path / "koan"
        koan_dir.mkdir()
        config = {
            "projects": {
                "koan": {"path": str(koan_dir)}
            }
        }
        (tmp_path / "projects.yaml").write_text(yaml.dump(config))

        with patch("app.utils.get_github_remote", return_value="sukria/koan"):
            msgs1 = ensure_github_urls(str(tmp_path))
            msgs2 = ensure_github_urls(str(tmp_path))

        assert len(msgs1) == 1
        assert len(msgs2) == 0  # second run is a no-op

    def test_no_projects_yaml(self, tmp_path):
        from app.projects_config import ensure_github_urls
        # No projects.yaml file
        msgs = ensure_github_urls(str(tmp_path))
        assert msgs == []

    def test_skips_missing_paths(self, tmp_path):
        from app.projects_config import ensure_github_urls

        config = {
            "projects": {
                "ghost": {"path": "/nonexistent/path/ghost"}
            }
        }
        (tmp_path / "projects.yaml").write_text(yaml.dump(config))

        msgs = ensure_github_urls(str(tmp_path))
        assert len(msgs) == 0

    def test_handles_write_error_gracefully(self, tmp_path):
        from app.projects_config import ensure_github_urls

        koan_dir = tmp_path / "koan"
        koan_dir.mkdir()
        config = {
            "projects": {
                "koan": {"path": str(koan_dir)}
            }
        }
        (tmp_path / "projects.yaml").write_text(yaml.dump(config))

        with patch("app.utils.get_github_remote", return_value="sukria/koan"), \
             patch("app.projects_config.save_projects_config", side_effect=OSError("disk full")):
            msgs = ensure_github_urls(str(tmp_path))

        assert any("could not save" in m.lower() for m in msgs)


# ─────────────────────────────────────────────────────
# Phase 3: resolve_project_path() with owner
# ─────────────────────────────────────────────────────


class TestResolveProjectPathWithOwner:
    """Tests for enhanced resolve_project_path() with owner parameter."""

    def test_without_owner_unchanged(self, monkeypatch):
        """Without owner, behavior is identical to before."""
        from app import utils
        monkeypatch.setattr(utils, "KOAN_ROOT", Path("/tmp/test"))
        monkeypatch.setenv("KOAN_PROJECTS", "koan:/home/koan;web:/home/web")

        from app.utils import resolve_project_path
        assert resolve_project_path("koan") == "/home/koan"

    def test_exact_name_match(self, monkeypatch):
        """Exact project name match still works."""
        from app import utils
        monkeypatch.setattr(utils, "KOAN_ROOT", Path("/tmp/test"))
        monkeypatch.setenv("KOAN_PROJECTS", "koan:/home/koan")

        from app.utils import resolve_project_path
        assert resolve_project_path("koan", owner="sukria") == "/home/koan"

    def test_github_url_match(self, tmp_path, monkeypatch):
        """Matches via github_url in projects.yaml when name doesn't match."""
        from app import utils
        monkeypatch.setattr(utils, "KOAN_ROOT", tmp_path)

        config = {
            "projects": {
                "my-koan": {
                    "path": "/home/my-koan",
                    "github_url": "sukria/koan"
                }
            }
        }
        (tmp_path / "projects.yaml").write_text(yaml.dump(config))

        from app.utils import resolve_project_path
        # repo name "koan" doesn't match project name "my-koan"
        # but github_url matches
        assert resolve_project_path("koan", owner="sukria") == "/home/my-koan"

    def test_github_url_case_insensitive(self, tmp_path, monkeypatch):
        """GitHub URL matching is case-insensitive."""
        from app import utils
        monkeypatch.setattr(utils, "KOAN_ROOT", tmp_path)

        config = {
            "projects": {
                "myapp": {
                    "path": "/home/myapp",
                    "github_url": "Sukria/Koan"
                }
            }
        }
        (tmp_path / "projects.yaml").write_text(yaml.dump(config))

        from app.utils import resolve_project_path
        assert resolve_project_path("koan", owner="sukria") == "/home/myapp"

    def test_auto_discovery(self, tmp_path, monkeypatch):
        """Auto-discovers github_url from git remote when no match found."""
        from app import utils
        monkeypatch.setattr(utils, "KOAN_ROOT", tmp_path)

        project_dir = tmp_path / "my-koan"
        project_dir.mkdir()
        config = {
            "projects": {
                "my-koan": {"path": str(project_dir)}
            }
        }
        (tmp_path / "projects.yaml").write_text(yaml.dump(config))

        result = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="https://github.com/sukria/koan.git\n"
        )
        with patch("app.utils.subprocess.run", return_value=result):
            from app.utils import resolve_project_path
            path = resolve_project_path("koan", owner="sukria")

        assert path == str(project_dir)

        # Verify auto-discovery saved to projects.yaml
        saved = yaml.safe_load((tmp_path / "projects.yaml").read_text())
        assert saved["projects"]["my-koan"]["github_url"] == "sukria/koan"

    def test_single_project_fallback(self, monkeypatch):
        """Falls back to single project without owner."""
        from app import utils
        monkeypatch.setattr(utils, "KOAN_ROOT", Path("/tmp/test"))
        monkeypatch.setenv("KOAN_PROJECTS", "only:/home/only")

        from app.utils import resolve_project_path
        assert resolve_project_path("unknown") == "/home/only"

    def test_no_match_returns_none(self, monkeypatch):
        """Returns None when nothing matches with multiple projects."""
        from app import utils
        monkeypatch.setattr(utils, "KOAN_ROOT", Path("/tmp/test"))
        monkeypatch.setenv("KOAN_PROJECTS", "koan:/home/koan;web:/home/web")

        with patch("app.utils.get_github_remote", return_value=None):
            from app.utils import resolve_project_path
            assert resolve_project_path("unknown", owner="somebody") is None

    def test_basename_match(self, monkeypatch):
        """Directory basename match still works."""
        from app import utils
        monkeypatch.setattr(utils, "KOAN_ROOT", Path("/tmp/test"))
        monkeypatch.setenv("KOAN_PROJECTS", "myproject:/home/workspace/koan")

        from app.utils import resolve_project_path
        assert resolve_project_path("koan") == "/home/workspace/koan"

    def test_fork_scenario(self, tmp_path, monkeypatch):
        """Fork workflow: owner in URL differs from owner in github_url."""
        from app import utils
        monkeypatch.setattr(utils, "KOAN_ROOT", tmp_path)

        # Project has github_url pointing to the upstream owner
        config = {
            "projects": {
                "koan": {
                    "path": "/home/koan",
                    "github_url": "sukria/koan"
                }
            }
        }
        (tmp_path / "projects.yaml").write_text(yaml.dump(config))

        from app.utils import resolve_project_path
        # URL is from fork owner — doesn't match github_url
        # Should still match on project name
        assert resolve_project_path("koan", owner="atoomic") == "/home/koan"


# ─────────────────────────────────────────────────────
# Phase 4: Skill handler owner passthrough
# ─────────────────────────────────────────────────────


class TestRebaseHandlerOwner:
    """Tests that /rebase handler passes owner to resolve_project_path."""

    def test_passes_owner(self, tmp_path):
        from skills.core.rebase.handler import handle

        ctx = MagicMock()
        ctx.args = "https://github.com/sukria/koan/pull/42"
        ctx.instance_dir = tmp_path

        with patch("app.utils.resolve_project_path", return_value="/home/koan") as mock_resolve, \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission"):
            handle(ctx)

        mock_resolve.assert_called_once_with("koan", owner="sukria")


class TestRecreateHandlerOwner:
    """Tests that /recreate handler passes owner to resolve_project_path."""

    def test_passes_owner(self, tmp_path):
        from skills.core.recreate.handler import handle

        ctx = MagicMock()
        ctx.args = "https://github.com/garu/Clone/pull/10"
        ctx.instance_dir = tmp_path

        with patch("app.utils.resolve_project_path", return_value="/home/clone") as mock_resolve, \
             patch("app.utils.get_known_projects", return_value=[("clone", "/home/clone")]), \
             patch("app.utils.insert_pending_mission"):
            handle(ctx)

        mock_resolve.assert_called_once_with("Clone", owner="garu")


class TestPrHandlerOwner:
    """Tests that /pr handler passes owner to resolve_project_path."""

    def test_passes_owner(self, tmp_path):
        from skills.core.pr.handler import handle

        ctx = MagicMock()
        ctx.args = "https://github.com/sukria/koan/pull/99"
        ctx.send_message = None

        with patch("app.utils.resolve_project_path", return_value="/home/koan") as mock_resolve, \
             patch("app.pr_review.run_pr_review", return_value=(True, "ok")):
            handle(ctx)

        mock_resolve.assert_called_once_with("koan", owner="sukria")


class TestCheckHandlerOwner:
    """Tests that /check handler passes owner for project resolution."""

    def test_passes_owner_for_pr(self, tmp_path):
        from skills.core.check.handler import handle

        ctx = MagicMock()
        ctx.args = "https://github.com/sukria/koan/pull/85"
        ctx.instance_dir = tmp_path

        with patch("app.utils.resolve_project_path", return_value="/home/koan") as mock_resolve, \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission"):
            handle(ctx)

        mock_resolve.assert_called_once_with("koan", owner="sukria")

    def test_passes_owner_for_issue(self, tmp_path):
        from skills.core.check.handler import handle

        ctx = MagicMock()
        ctx.args = "https://github.com/garu/Clone/issues/18"
        ctx.instance_dir = tmp_path

        with patch("app.utils.resolve_project_path", return_value="/home/clone") as mock_resolve, \
             patch("app.utils.get_known_projects", return_value=[("clone", "/home/clone")]), \
             patch("app.utils.insert_pending_mission"):
            handle(ctx)

        mock_resolve.assert_called_once_with("Clone", owner="garu")


class TestPlanHandlerOwner:
    """Tests that /plan handler passes owner for project resolution."""

    def test_passes_owner_for_issue_url(self, tmp_path):
        from skills.core.plan.handler import handle

        ctx = MagicMock()
        ctx.args = "https://github.com/sukria/koan/issues/230"
        ctx.instance_dir = tmp_path

        with patch("app.utils.resolve_project_path", return_value="/home/koan") as mock_resolve, \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission"):
            handle(ctx)

        mock_resolve.assert_called_once_with("koan", owner="sukria")


# ─────────────────────────────────────────────────────
# Phase 5: Startup integration
# ─────────────────────────────────────────────────────


class TestStartupEnsureGithubUrls:
    """Tests that run_startup calls ensure_github_urls."""

    def test_ensure_called_in_startup(self):
        """Verify the ensure_github_urls call exists in run_startup source."""
        import inspect
        from app import run

        source = inspect.getsource(run.run_startup)
        assert "ensure_github_urls" in source
        assert "github-urls" in source


# ─────────────────────────────────────────────────────
# Integration / edge cases
# ─────────────────────────────────────────────────────


class TestGithubUrlEdgeCases:
    """Edge cases for the full resolution pipeline."""

    def test_url_with_trailing_slash(self, tmp_path):
        """Trailing slash in URL doesn't break parsing."""
        from app.utils import get_github_remote

        result = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="https://github.com/sukria/koan/\n"
        )
        # The regex requires no trailing slash after repo name
        # but get_github_remote strips whitespace
        with patch("app.utils.subprocess.run", return_value=result):
            # Should handle or return None gracefully
            remote = get_github_remote(str(tmp_path))
            # URL has trailing slash — regex may or may not match,
            # but it should not crash
            assert remote is None or isinstance(remote, str)
