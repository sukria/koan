"""Tests for app/skill_manager.py — skill source management."""

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.skill_manager import (
    SkillSource,
    _extract_scope_from_url,
    _parse_manifest,
    _serialize_manifest,
    compare_versions,
    install_skill_source,
    list_sources,
    load_manifest,
    normalize_git_url,
    parse_version,
    remove_skill_source,
    save_manifest,
    update_all_sources,
    update_skill_source,
    validate_scope,
)


# ---------------------------------------------------------------------------
# normalize_git_url
# ---------------------------------------------------------------------------

class TestNormalizeGitUrl:
    def test_github_shorthand(self):
        assert normalize_git_url("myorg/koan-skills") == "https://github.com/myorg/koan-skills.git"

    def test_full_https_url_unchanged(self):
        url = "https://github.com/myorg/koan-skills.git"
        assert normalize_git_url(url) == url

    def test_ssh_url_unchanged(self):
        url = "git@github.com:myorg/koan-skills.git"
        assert normalize_git_url(url) == url

    def test_strips_whitespace(self):
        assert normalize_git_url("  myorg/repo  ") == "https://github.com/myorg/repo.git"


# ---------------------------------------------------------------------------
# _extract_scope_from_url
# ---------------------------------------------------------------------------

class TestExtractScopeFromUrl:
    def test_github_url_with_prefix(self):
        assert _extract_scope_from_url("https://github.com/myorg/koan-skills-ops.git") == "ops"

    def test_github_url_no_prefix(self):
        assert _extract_scope_from_url("https://github.com/team/deploy-skills.git") == "deploy-skills"

    def test_ssh_url(self):
        assert _extract_scope_from_url("git@github.com:team/koan-skills-analytics.git") == "analytics"

    def test_shorthand(self):
        assert _extract_scope_from_url("myorg/koan-skills-ops") == "ops"

    def test_simple_repo_name(self):
        assert _extract_scope_from_url("https://github.com/org/tools.git") == "tools"

    def test_trailing_slash(self):
        assert _extract_scope_from_url("https://github.com/org/koan-skills-ops/") == "ops"


# ---------------------------------------------------------------------------
# validate_scope
# ---------------------------------------------------------------------------

class TestValidateScope:
    def test_valid_scope(self):
        assert validate_scope("ops") is None

    def test_valid_scope_with_hyphen(self):
        assert validate_scope("my-team") is None

    def test_reserved_core(self):
        assert validate_scope("core") is not None
        assert "reserved" in validate_scope("core")

    def test_empty_scope(self):
        assert validate_scope("") is not None

    def test_invalid_chars(self):
        assert validate_scope("my scope") is not None
        assert validate_scope("../escape") is not None


# ---------------------------------------------------------------------------
# Manifest parsing / serialization
# ---------------------------------------------------------------------------

class TestManifestParsing:
    def test_parse_empty(self):
        assert _parse_manifest("") == {}

    def test_parse_sources_header_only(self):
        assert _parse_manifest("sources:\n") == {}

    def test_parse_single_source(self):
        content = textwrap.dedent("""\
            sources:
              ops:
                url: "https://github.com/myorg/koan-skills-ops.git"
                ref: "main"
                installed_at: "2026-02-07T12:00:00"
                updated_at: "2026-02-07T12:00:00"
        """)
        result = _parse_manifest(content)
        assert "ops" in result
        assert result["ops"].url == "https://github.com/myorg/koan-skills-ops.git"
        assert result["ops"].ref == "main"
        assert result["ops"].installed_at == "2026-02-07T12:00:00"

    def test_parse_multiple_sources(self):
        content = textwrap.dedent("""\
            sources:
              ops:
                url: "https://github.com/myorg/ops.git"
                ref: "main"
              analytics:
                url: "https://github.com/myorg/analytics.git"
                ref: "v1.2.0"
        """)
        result = _parse_manifest(content)
        assert len(result) == 2
        assert result["analytics"].ref == "v1.2.0"

    def test_parse_without_quotes(self):
        content = textwrap.dedent("""\
            sources:
              ops:
                url: https://github.com/myorg/ops.git
                ref: main
        """)
        result = _parse_manifest(content)
        assert result["ops"].url == "https://github.com/myorg/ops.git"

    def test_parse_with_comments(self):
        content = textwrap.dedent("""\
            # Installed skills
            sources:
              # Team ops skills
              ops:
                url: "https://github.com/myorg/ops.git"
                ref: "main"
        """)
        result = _parse_manifest(content)
        assert "ops" in result


class TestManifestSerialization:
    def test_serialize_empty(self):
        assert _serialize_manifest({}) == "sources:\n"

    def test_serialize_single_source(self):
        sources = {
            "ops": SkillSource(
                scope="ops",
                url="https://github.com/myorg/ops.git",
                ref="main",
                installed_at="2026-02-07T12:00:00",
                updated_at="2026-02-07T12:00:00",
            )
        }
        result = _serialize_manifest(sources)
        assert "ops:" in result
        assert "https://github.com/myorg/ops.git" in result
        assert "2026-02-07T12:00:00" in result

    def test_roundtrip(self):
        sources = {
            "ops": SkillSource(
                scope="ops",
                url="https://github.com/myorg/ops.git",
                ref="main",
                installed_at="2026-02-07T12:00:00",
                updated_at="2026-02-07T12:00:00",
            ),
            "analytics": SkillSource(
                scope="analytics",
                url="https://github.com/team/analytics.git",
                ref="v1.2.0",
                installed_at="2026-02-06T10:00:00",
                updated_at="2026-02-07T08:00:00",
            ),
        }
        serialized = _serialize_manifest(sources)
        parsed = _parse_manifest(serialized)
        assert len(parsed) == 2
        assert parsed["ops"].url == sources["ops"].url
        assert parsed["analytics"].ref == sources["analytics"].ref


class TestManifestIO:
    def test_load_nonexistent(self, tmp_path):
        assert load_manifest(tmp_path) == {}

    def test_save_and_load(self, tmp_path):
        sources = {
            "ops": SkillSource(
                scope="ops",
                url="https://github.com/myorg/ops.git",
                ref="main",
                installed_at="2026-02-07T12:00:00",
            )
        }
        save_manifest(tmp_path, sources)
        loaded = load_manifest(tmp_path)
        assert "ops" in loaded
        assert loaded["ops"].url == "https://github.com/myorg/ops.git"

    def test_save_creates_file(self, tmp_path):
        save_manifest(tmp_path, {})
        assert (tmp_path / "skills.yaml").exists()


# ---------------------------------------------------------------------------
# Version utilities
# ---------------------------------------------------------------------------

class TestParseVersion:
    def test_simple_version(self):
        assert parse_version("1.2.3") == (1, 2, 3, "")

    def test_prerelease(self):
        assert parse_version("1.0.0-alpha") == (1, 0, 0, "alpha")

    def test_zero_version(self):
        assert parse_version("0.0.0") == (0, 0, 0, "")

    def test_invalid_version(self):
        assert parse_version("not-a-version") is None

    def test_partial_version(self):
        assert parse_version("1.2") is None

    def test_whitespace(self):
        assert parse_version("  1.0.0  ") == (1, 0, 0, "")


class TestCompareVersions:
    def test_equal(self):
        assert compare_versions("1.0.0", "1.0.0") == 0

    def test_major_greater(self):
        assert compare_versions("2.0.0", "1.0.0") == 1

    def test_major_less(self):
        assert compare_versions("1.0.0", "2.0.0") == -1

    def test_minor_greater(self):
        assert compare_versions("1.1.0", "1.0.0") == 1

    def test_patch_greater(self):
        assert compare_versions("1.0.1", "1.0.0") == 1

    def test_prerelease_less_than_release(self):
        assert compare_versions("1.0.0-alpha", "1.0.0") == -1

    def test_release_greater_than_prerelease(self):
        assert compare_versions("1.0.0", "1.0.0-beta") == 1

    def test_invalid_version_returns_zero(self):
        assert compare_versions("invalid", "1.0.0") == 0

    def test_prerelease_comparison(self):
        assert compare_versions("1.0.0-alpha", "1.0.0-beta") == -1


# ---------------------------------------------------------------------------
# install_skill_source
# ---------------------------------------------------------------------------

class TestInstallSkillSource:
    def _make_skill_dir(self, parent, name):
        """Create a minimal skill directory with a SKILL.md."""
        skill_dir = parent / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(textwrap.dedent(f"""\
            ---
            name: {name}
            description: Test skill
            version: 1.0.0
            commands:
              - name: {name}
                description: Test command
            ---
        """))

    @patch("app.skill_manager._run_git")
    def test_install_success(self, mock_git, tmp_path):
        """Successful install clones repo and tracks in manifest."""
        def fake_clone(*args, cwd=None, timeout=60):
            # Simulate git clone by creating the target directory
            for i, arg in enumerate(args):
                if arg == "clone":
                    target = Path(args[-1])
                    target.mkdir(parents=True, exist_ok=True)
                    self._make_skill_dir(target, "deploy")
                    return 0, "", ""
            return 1, "", "error"

        mock_git.side_effect = fake_clone
        ok, msg = install_skill_source(tmp_path, "https://github.com/myorg/ops.git", scope="ops")
        assert ok
        assert "1 skill" in msg
        assert "ops" in msg

        # Verify manifest was created
        sources = load_manifest(tmp_path)
        assert "ops" in sources
        assert sources["ops"].url == "https://github.com/myorg/ops.git"

    @patch("app.skill_manager._run_git")
    def test_install_auto_scope(self, mock_git, tmp_path):
        """Scope is derived from URL when not provided."""
        def fake_clone(*args, cwd=None, timeout=60):
            target = Path(args[-1])
            target.mkdir(parents=True, exist_ok=True)
            self._make_skill_dir(target, "deploy")
            return 0, "", ""

        mock_git.side_effect = fake_clone
        ok, msg = install_skill_source(tmp_path, "https://github.com/myorg/koan-skills-ops.git")
        assert ok
        sources = load_manifest(tmp_path)
        assert "ops" in sources

    def test_install_reserved_scope(self, tmp_path):
        """Cannot install into 'core' scope."""
        ok, msg = install_skill_source(tmp_path, "https://github.com/x/y.git", scope="core")
        assert not ok
        assert "reserved" in msg

    @patch("app.skill_manager._run_git")
    def test_install_duplicate_scope(self, mock_git, tmp_path):
        """Cannot install same scope twice."""
        # Pre-populate manifest
        save_manifest(tmp_path, {
            "ops": SkillSource(scope="ops", url="https://example.com/ops.git")
        })
        ok, msg = install_skill_source(tmp_path, "https://github.com/other/ops.git", scope="ops")
        assert not ok
        assert "already installed" in msg

    @patch("app.skill_manager._run_git")
    def test_install_no_skills_found(self, mock_git, tmp_path):
        """Install fails if no SKILL.md files found."""
        def fake_clone(*args, cwd=None, timeout=60):
            target = Path(args[-1])
            target.mkdir(parents=True, exist_ok=True)
            (target / "README.md").write_text("No skills here")
            return 0, "", ""

        mock_git.side_effect = fake_clone
        ok, msg = install_skill_source(tmp_path, "https://github.com/x/y.git", scope="empty")
        assert not ok
        assert "No SKILL.md" in msg
        # Directory should be cleaned up
        assert not (tmp_path / "skills" / "empty").exists()

    @patch("app.skill_manager._run_git")
    def test_install_git_failure(self, mock_git, tmp_path):
        """Install fails gracefully on git error."""
        mock_git.return_value = (1, "", "fatal: repo not found")
        ok, msg = install_skill_source(tmp_path, "https://github.com/x/y.git", scope="ops")
        assert not ok
        assert "clone failed" in msg

    @patch("app.skill_manager._run_git")
    def test_install_with_ref(self, mock_git, tmp_path):
        """Install with a specific ref/tag."""
        def fake_clone(*args, cwd=None, timeout=60):
            target = Path(args[-1])
            target.mkdir(parents=True, exist_ok=True)
            self._make_skill_dir(target, "deploy")
            return 0, "", ""

        mock_git.side_effect = fake_clone
        ok, msg = install_skill_source(
            tmp_path, "https://github.com/myorg/ops.git",
            scope="ops", ref="v1.0.0"
        )
        assert ok
        sources = load_manifest(tmp_path)
        assert sources["ops"].ref == "v1.0.0"
        # Verify --branch v1.0.0 was passed
        call_args = mock_git.call_args_list[0]
        assert "v1.0.0" in call_args[0]

    @patch("app.skill_manager._run_git")
    def test_install_github_shorthand(self, mock_git, tmp_path):
        """GitHub shorthand is expanded to full URL."""
        def fake_clone(*args, cwd=None, timeout=60):
            target = Path(args[-1])
            target.mkdir(parents=True, exist_ok=True)
            self._make_skill_dir(target, "deploy")
            return 0, "", ""

        mock_git.side_effect = fake_clone
        ok, msg = install_skill_source(tmp_path, "myorg/koan-skills-ops", scope="ops")
        assert ok
        sources = load_manifest(tmp_path)
        assert sources["ops"].url == "https://github.com/myorg/koan-skills-ops.git"

    def test_install_existing_directory(self, tmp_path):
        """Cannot install if directory already exists."""
        (tmp_path / "skills" / "ops").mkdir(parents=True)
        ok, msg = install_skill_source(tmp_path, "https://github.com/x/y.git", scope="ops")
        assert not ok
        assert "already exists" in msg


# ---------------------------------------------------------------------------
# update_skill_source
# ---------------------------------------------------------------------------

class TestUpdateSkillSource:
    @patch("app.skill_manager._run_git")
    def test_update_success(self, mock_git, tmp_path):
        """Successful update pulls and updates timestamp."""
        # Setup: manifest + directory
        save_manifest(tmp_path, {
            "ops": SkillSource(
                scope="ops",
                url="https://github.com/myorg/ops.git",
                ref="main",
                installed_at="2026-02-06T12:00:00",
                updated_at="2026-02-06T12:00:00",
            )
        })
        skills_dir = tmp_path / "skills" / "ops" / "deploy"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text("---\nname: deploy\n---")

        mock_git.return_value = (0, "Updating abc123..def456", "")
        ok, msg = update_skill_source(tmp_path, "ops")
        assert ok
        assert "Updated" in msg
        assert "Restart" in msg

        # Verify timestamp was updated
        sources = load_manifest(tmp_path)
        assert sources["ops"].updated_at != "2026-02-06T12:00:00"

    @patch("app.skill_manager._run_git")
    def test_update_already_current(self, mock_git, tmp_path):
        """Update when already up to date."""
        save_manifest(tmp_path, {
            "ops": SkillSource(scope="ops", url="https://github.com/x.git")
        })
        (tmp_path / "skills" / "ops").mkdir(parents=True)

        mock_git.return_value = (0, "Already up to date.", "")
        ok, msg = update_skill_source(tmp_path, "ops")
        assert ok
        assert "already up to date" in msg

    def test_update_unknown_scope(self, tmp_path):
        """Update fails for untracked scope."""
        save_manifest(tmp_path, {})
        ok, msg = update_skill_source(tmp_path, "nonexistent")
        assert not ok
        assert "not found" in msg

    @patch("app.skill_manager._run_git")
    def test_update_git_failure(self, mock_git, tmp_path):
        """Update handles git pull failure."""
        save_manifest(tmp_path, {
            "ops": SkillSource(scope="ops", url="https://github.com/x.git")
        })
        (tmp_path / "skills" / "ops").mkdir(parents=True)

        mock_git.return_value = (1, "", "error: merge conflict")
        ok, msg = update_skill_source(tmp_path, "ops")
        assert not ok
        assert "pull failed" in msg

    def test_update_missing_directory(self, tmp_path):
        """Update fails when directory was deleted."""
        save_manifest(tmp_path, {
            "ops": SkillSource(scope="ops", url="https://github.com/x.git")
        })
        ok, msg = update_skill_source(tmp_path, "ops")
        assert not ok
        assert "missing" in msg


# ---------------------------------------------------------------------------
# update_all_sources
# ---------------------------------------------------------------------------

class TestUpdateAllSources:
    def test_no_sources(self, tmp_path):
        ok, msg = update_all_sources(tmp_path)
        assert ok
        assert "No installed" in msg

    @patch("app.skill_manager._run_git")
    def test_update_all(self, mock_git, tmp_path):
        """Updates all tracked sources."""
        save_manifest(tmp_path, {
            "ops": SkillSource(scope="ops", url="https://github.com/x.git"),
            "analytics": SkillSource(scope="analytics", url="https://github.com/y.git"),
        })
        (tmp_path / "skills" / "ops").mkdir(parents=True)
        (tmp_path / "skills" / "analytics").mkdir(parents=True)

        mock_git.return_value = (0, "Already up to date.", "")
        ok, msg = update_all_sources(tmp_path)
        assert ok
        assert "ops" in msg
        assert "analytics" in msg


# ---------------------------------------------------------------------------
# remove_skill_source
# ---------------------------------------------------------------------------

class TestRemoveSkillSource:
    def test_remove_tracked(self, tmp_path):
        """Remove a tracked skill source."""
        save_manifest(tmp_path, {
            "ops": SkillSource(scope="ops", url="https://github.com/x.git")
        })
        (tmp_path / "skills" / "ops").mkdir(parents=True)
        (tmp_path / "skills" / "ops" / "file.txt").write_text("test")

        ok, msg = remove_skill_source(tmp_path, "ops")
        assert ok
        assert not (tmp_path / "skills" / "ops").exists()
        assert "ops" not in load_manifest(tmp_path)

    def test_remove_untracked_directory(self, tmp_path):
        """Remove a directory even if not in manifest."""
        (tmp_path / "skills" / "manual").mkdir(parents=True)
        ok, msg = remove_skill_source(tmp_path, "manual")
        assert ok
        assert not (tmp_path / "skills" / "manual").exists()

    def test_remove_nonexistent(self, tmp_path):
        """Remove fails for unknown scope."""
        ok, msg = remove_skill_source(tmp_path, "ghost")
        assert not ok
        assert "not found" in msg

    def test_remove_cleans_manifest(self, tmp_path):
        """Manifest is cleaned after removal."""
        save_manifest(tmp_path, {
            "ops": SkillSource(scope="ops", url="https://github.com/x.git"),
            "analytics": SkillSource(scope="analytics", url="https://github.com/y.git"),
        })
        (tmp_path / "skills" / "ops").mkdir(parents=True)

        ok, msg = remove_skill_source(tmp_path, "ops")
        assert ok
        sources = load_manifest(tmp_path)
        assert "ops" not in sources
        assert "analytics" in sources


# ---------------------------------------------------------------------------
# list_sources
# ---------------------------------------------------------------------------

class TestListSources:
    def test_empty(self, tmp_path):
        result = list_sources(tmp_path)
        assert "No external" in result
        assert "install" in result

    def test_with_sources(self, tmp_path):
        save_manifest(tmp_path, {
            "ops": SkillSource(
                scope="ops",
                url="https://github.com/myorg/ops.git",
                ref="main",
                updated_at="2026-02-07T12:00:00",
            )
        })
        result = list_sources(tmp_path)
        assert "ops" in result
        assert "https://github.com/myorg/ops.git" in result
        assert "main" in result

    def test_with_skills_count(self, tmp_path):
        """Shows skill count from actual directory."""
        save_manifest(tmp_path, {
            "ops": SkillSource(scope="ops", url="https://github.com/x.git")
        })
        # Create 2 skills in the directory
        for name in ["deploy", "oncall"]:
            d = tmp_path / "skills" / "ops" / name
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(f"---\nname: {name}\n---")

        result = list_sources(tmp_path)
        assert "2 skills" in result


# ---------------------------------------------------------------------------
# SkillSource dataclass
# ---------------------------------------------------------------------------

class TestSkillSource:
    def test_default_values(self):
        src = SkillSource(scope="ops", url="https://example.com/ops.git")
        assert src.ref == "main"
        assert src.installed_at == ""
        assert src.updated_at == ""

    def test_all_fields(self):
        src = SkillSource(
            scope="ops",
            url="https://example.com/ops.git",
            ref="v1.0.0",
            installed_at="2026-02-07",
            updated_at="2026-02-07",
        )
        assert src.scope == "ops"
        assert src.ref == "v1.0.0"
