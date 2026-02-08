"""Tests for the sanity runner (koan/sanity/__init__.py)."""

import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

import sanity
from sanity import discover_checks, run_all


class TestDiscoverChecks:
    def test_discovers_missions_structure(self):
        """The missions_structure module should be discovered."""
        checks = discover_checks()
        assert "missions_structure" in checks

    def test_returns_sorted(self):
        checks = discover_checks()
        assert checks == sorted(checks)

    def test_excludes_init(self):
        checks = discover_checks()
        assert "__init__" not in checks


class TestRunAll:
    def test_runs_missions_structure_on_clean_instance(self, tmp_path):
        missions = tmp_path / "missions.md"
        missions.write_text("# Missions\n\n## Pending\n- a\n\n## In Progress\n\n## Done\n")
        results = run_all(str(tmp_path))
        # Should find missions_structure and report no modifications
        names = [r[0] for r in results]
        assert "missions_structure" in names
        for name, modified, changes in results:
            if name == "missions_structure":
                assert not modified
                assert changes == []

    def test_runs_missions_structure_on_dirty_instance(self, tmp_path):
        missions = tmp_path / "missions.md"
        missions.write_text(
            "# Missions\n\n## Pending\n- a\n\n## Recent activity\nstuff\n\n## Done\n"
        )
        results = run_all(str(tmp_path))
        for name, modified, changes in results:
            if name == "missions_structure":
                assert modified
                assert len(changes) > 0

    def test_skips_modules_without_run(self):
        """Modules without a run() function are silently skipped."""
        # This tests the getattr guard in run_all
        # Use patch.object to avoid mock resolution issues with nested
        # string-based patch targets on package modules
        mock_module = MagicMock(spec=[])  # no 'run' attribute
        with patch.object(
            sanity, "discover_checks", return_value=["fake_check"]
        ), patch.object(
            sanity.importlib, "import_module", return_value=mock_module
        ):
            results = run_all("/tmp/nonexistent")
            assert results == []


class TestBackwardCompat:
    def test_app_missions_sanity_still_works(self):
        """The shim in app.missions_sanity should re-export correctly."""
        from app.missions_sanity import find_issues, sanitize, run_sanity_check
        assert callable(find_issues)
        assert callable(sanitize)
        assert callable(run_sanity_check)

    def test_shim_find_issues_works(self):
        from app.missions_sanity import find_issues
        issues = find_issues("# Missions\n\n## Pending\n- a\n\n## Done\n")
        assert issues == []

    def test_shim_sanitize_works(self):
        from app.missions_sanity import sanitize
        cleaned, changes = sanitize(
            "# Missions\n\n## Pending\n- a\n\n## Recent activity\nstuff\n\n## Done\n"
        )
        assert "## Recent activity" not in cleaned
        assert len(changes) == 1
