"""Tests for the /doctor skill handler."""

import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skills.core.doctor.handler import (
    handle,
    CheckResult,
    OK, WARN, FAIL, INFO,
    _check_binaries,
    _check_instance_structure,
    _check_processes,
    _check_signal_files,
    _check_projects,
    _check_heartbeat,
    _check_journal_memory,
    _format_duration,
    _dir_size_mb,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def instance_dir(tmp_path):
    """Create a minimal instance directory structure."""
    inst = tmp_path / "instance"
    inst.mkdir()
    (inst / "journal").mkdir()
    (inst / "memory").mkdir()
    (inst / "config.yaml").write_text("max_runs_per_day: 20\n")
    (inst / "missions.md").write_text("# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n")
    (inst / "soul.md").write_text("# Soul\n")
    (inst / "outbox.md").write_text("")
    return inst


@pytest.fixture
def koan_root(tmp_path, instance_dir):
    """Return the koan root (parent of instance)."""
    return tmp_path


def _make_ctx(koan_root, instance_dir, command_name="doctor", args=""):
    return SimpleNamespace(
        koan_root=koan_root,
        instance_dir=instance_dir,
        command_name=command_name,
        args=args,
        send_message=None,
        handle_chat=None,
    )


# ---------------------------------------------------------------------------
# CheckResult
# ---------------------------------------------------------------------------

class TestCheckResult:
    def test_format_with_detail(self):
        r = CheckResult(OK, "git 2.40", "found")
        assert "git 2.40" in r.format()
        assert "found" in r.format()

    def test_format_without_detail(self):
        r = CheckResult(OK, "python 3.11")
        formatted = r.format()
        assert "python 3.11" in formatted
        assert " \u2014 " not in formatted  # em dash not present

    def test_fixable_flag(self):
        r = CheckResult(WARN, "stale", fixable=True, fix_action=lambda: "fixed")
        assert r.fixable is True
        assert r.fix_action() == "fixed"


# ---------------------------------------------------------------------------
# _check_binaries
# ---------------------------------------------------------------------------

class TestCheckBinaries:
    @patch("skills.core.doctor.handler._get_provider", return_value="claude")
    def test_python_ok(self, mock_prov, koan_root):
        results = _check_binaries(koan_root)
        python_results = [r for r in results if "python" in r.label]
        assert len(python_results) == 1
        assert python_results[0].icon == OK

    @patch("skills.core.doctor.handler._get_provider", return_value="claude")
    @patch("shutil.which", return_value=None)
    def test_missing_git(self, mock_which, mock_prov, koan_root):
        results = _check_binaries(koan_root)
        git_results = [r for r in results if r.label.startswith("git")]
        assert any(r.icon == FAIL for r in git_results)

    @patch("skills.core.doctor.handler._get_provider", return_value="claude")
    @patch("shutil.which", side_effect=lambda name: "/usr/bin/git" if name == "git" else None)
    def test_missing_gh(self, mock_which, mock_prov, koan_root):
        results = _check_binaries(koan_root)
        gh_results = [r for r in results if r.label.startswith("gh")]
        assert any(r.icon == WARN for r in gh_results)


# ---------------------------------------------------------------------------
# _check_instance_structure
# ---------------------------------------------------------------------------

class TestCheckInstanceStructure:
    def test_all_present(self, instance_dir):
        results = _check_instance_structure(instance_dir)
        assert all(r.icon in (OK, INFO) for r in results)

    def test_missing_config(self, instance_dir):
        (instance_dir / "config.yaml").unlink()
        results = _check_instance_structure(instance_dir)
        config_results = [r for r in results if "config.yaml" in r.label]
        assert config_results[0].icon == FAIL  # config.yaml is critical

    def test_missing_soul(self, instance_dir):
        (instance_dir / "soul.md").unlink()
        results = _check_instance_structure(instance_dir)
        soul_results = [r for r in results if "soul.md" in r.label]
        assert soul_results[0].icon == WARN  # soul.md is non-critical

    def test_empty_config(self, instance_dir):
        (instance_dir / "config.yaml").write_text("")
        results = _check_instance_structure(instance_dir)
        config_results = [r for r in results if "config.yaml" in r.label]
        assert config_results[0].icon == WARN
        assert "empty" in config_results[0].detail

    def test_missing_memory_dir(self, instance_dir):
        import shutil
        shutil.rmtree(instance_dir / "memory")
        results = _check_instance_structure(instance_dir)
        mem_results = [r for r in results if "memory/" in r.label]
        assert mem_results[0].icon == WARN


# ---------------------------------------------------------------------------
# _check_processes
# ---------------------------------------------------------------------------

class TestCheckProcesses:
    @patch("skills.core.doctor.handler._get_provider", return_value="claude")
    def test_no_pid_files(self, mock_prov, koan_root):
        results = _check_processes(koan_root)
        assert all(r.icon == INFO for r in results)
        assert len(results) == 2  # run + awake only (no ollama for claude)

    @patch("skills.core.doctor.handler._get_provider", return_value="local")
    def test_includes_ollama_for_local(self, mock_prov, koan_root):
        results = _check_processes(koan_root)
        labels = [r.label for r in results]
        assert any("ollama" in l for l in labels)

    @patch("skills.core.doctor.handler._get_provider", return_value="claude")
    @patch("app.pid_manager.check_pidfile", return_value=None)
    @patch("app.pid_manager._read_pid", return_value=12345)
    def test_stale_pid_detected(self, mock_read, mock_check, mock_prov, koan_root):
        # Create a stale PID file
        (koan_root / ".koan-pid-run").write_text("12345")
        results = _check_processes(koan_root)
        run_results = [r for r in results if "run" in r.label]
        assert any(r.icon == WARN and r.fixable for r in run_results)

    @patch("skills.core.doctor.handler._get_provider", return_value="claude")
    @patch("app.pid_manager.check_pidfile", return_value=999)
    def test_running_process_ok(self, mock_check, mock_prov, koan_root):
        (koan_root / ".koan-pid-run").write_text("999")
        results = _check_processes(koan_root)
        run_results = [r for r in results if "run" in r.label]
        assert any(r.icon == OK for r in run_results)


# ---------------------------------------------------------------------------
# _check_signal_files
# ---------------------------------------------------------------------------

class TestCheckSignalFiles:
    @patch("app.pid_manager.check_pidfile", return_value=None)
    def test_orphaned_stop_file(self, mock_check, koan_root):
        (koan_root / ".koan-stop").write_text("STOP")
        results = _check_signal_files(koan_root)
        assert any(r.icon == WARN and r.fixable and ".koan-stop" in r.label for r in results)

    @patch("app.pid_manager.check_pidfile", return_value=123)
    def test_active_stop_file(self, mock_check, koan_root):
        (koan_root / ".koan-stop").write_text("STOP")
        results = _check_signal_files(koan_root)
        assert any(r.icon == INFO and ".koan-stop" in r.label for r in results)

    @patch("app.pid_manager.check_pidfile", return_value=None)
    def test_orphaned_pause_file(self, mock_check, koan_root):
        (koan_root / ".koan-pause").write_text("1")
        (koan_root / ".koan-pause-reason").write_text("quota")
        results = _check_signal_files(koan_root)
        pause_results = [r for r in results if ".koan-pause" in r.label]
        assert any(r.fixable for r in pause_results)

    @patch("app.pid_manager.check_pidfile", return_value=456)
    def test_active_pause_with_reason(self, mock_check, koan_root):
        (koan_root / ".koan-pause").write_text("1")
        (koan_root / ".koan-pause-reason").write_text("quota")
        results = _check_signal_files(koan_root)
        pause_results = [r for r in results if ".koan-pause" in r.label]
        assert any(r.icon == INFO and "quota" in r.detail for r in pause_results)

    @patch("app.pid_manager.check_pidfile", return_value=None)
    def test_orphaned_restart_file(self, mock_check, koan_root):
        (koan_root / ".koan-restart").write_text("1")
        results = _check_signal_files(koan_root)
        assert any(r.fixable and ".koan-restart" in r.label for r in results)

    @patch("app.pid_manager.check_pidfile", return_value=None)
    def test_no_signal_files(self, mock_check, koan_root):
        results = _check_signal_files(koan_root)
        assert len(results) == 0


# ---------------------------------------------------------------------------
# _check_projects
# ---------------------------------------------------------------------------

class TestCheckProjects:
    def test_no_projects_yaml(self, koan_root):
        results = _check_projects(koan_root)
        assert len(results) == 1
        assert results[0].icon == INFO

    def test_valid_projects_yaml(self, koan_root, tmp_path):
        # Create a project directory
        project_dir = tmp_path / "my_project"
        project_dir.mkdir()

        projects_yaml = koan_root / "projects.yaml"
        projects_yaml.write_text(
            f"projects:\n  myproj:\n    path: {project_dir}\n"
        )

        with patch("app.projects_config.load_projects_config") as mock_load, \
             patch("app.projects_config.get_projects_from_config") as mock_get:
            mock_load.return_value = {"projects": {"myproj": {"path": str(project_dir)}}}
            mock_get.return_value = [("myproj", str(project_dir))]
            results = _check_projects(koan_root)

        assert any(r.icon == OK and "projects.yaml" in r.label for r in results)
        assert any(r.icon == OK and "myproj" in r.label for r in results)

    def test_project_path_missing(self, koan_root):
        projects_yaml = koan_root / "projects.yaml"
        projects_yaml.write_text("projects:\n  ghost:\n    path: /nonexistent/path\n")

        with patch("app.projects_config.load_projects_config") as mock_load, \
             patch("app.projects_config.get_projects_from_config") as mock_get:
            mock_load.return_value = {"projects": {"ghost": {"path": "/nonexistent/path"}}}
            mock_get.return_value = [("ghost", "/nonexistent/path")]
            results = _check_projects(koan_root)

        ghost_results = [r for r in results if "ghost" in r.label]
        assert any(r.icon == WARN for r in ghost_results)

    def test_invalid_projects_yaml(self, koan_root):
        projects_yaml = koan_root / "projects.yaml"
        projects_yaml.write_text("not: valid: yaml: [")

        with patch("app.projects_config.load_projects_config", side_effect=ValueError("bad yaml")):
            results = _check_projects(koan_root)

        assert any(r.icon == FAIL for r in results)


# ---------------------------------------------------------------------------
# _check_heartbeat
# ---------------------------------------------------------------------------

class TestCheckHeartbeat:
    def test_no_heartbeat_file(self, koan_root):
        results = _check_heartbeat(koan_root)
        assert results[0].icon == INFO

    def test_fresh_heartbeat(self, koan_root):
        hb = koan_root / ".koan-heartbeat"
        hb.write_text(str(time.time()))
        results = _check_heartbeat(koan_root)
        assert results[0].icon == OK
        assert "fresh" in results[0].detail

    def test_stale_heartbeat(self, koan_root):
        hb = koan_root / ".koan-heartbeat"
        hb.write_text(str(time.time() - 120))  # 2 minutes ago
        results = _check_heartbeat(koan_root)
        assert results[0].icon == WARN
        assert "stale" in results[0].detail

    def test_very_stale_heartbeat(self, koan_root):
        hb = koan_root / ".koan-heartbeat"
        hb.write_text(str(time.time() - 600))  # 10 minutes ago
        results = _check_heartbeat(koan_root)
        assert results[0].icon == FAIL
        assert "very stale" in results[0].detail

    def test_corrupt_heartbeat(self, koan_root):
        hb = koan_root / ".koan-heartbeat"
        hb.write_text("not-a-number")
        results = _check_heartbeat(koan_root)
        assert results[0].icon == WARN


# ---------------------------------------------------------------------------
# _check_journal_memory
# ---------------------------------------------------------------------------

class TestCheckJournalMemory:
    def test_healthy_journal(self, instance_dir):
        # Create a few journal entries
        day_dir = instance_dir / "journal" / "2026-04-15"
        day_dir.mkdir(parents=True)
        (day_dir / "project.md").write_text("some content\n" * 10)

        results = _check_journal_memory(instance_dir)
        journal_results = [r for r in results if "journal" in r.label]
        assert any(r.icon == OK for r in journal_results)

    def test_missions_line_count(self, instance_dir):
        results = _check_journal_memory(instance_dir)
        mission_results = [r for r in results if "missions.md" in r.label]
        assert any(r.icon == OK for r in mission_results)

    def test_large_missions_warns(self, instance_dir):
        # Write a large missions.md
        lines = ["# Missions\n", "## Done\n"] + [f"- task {i}\n" for i in range(600)]
        (instance_dir / "missions.md").write_text("".join(lines))
        results = _check_journal_memory(instance_dir)
        mission_results = [r for r in results if "missions.md" in r.label]
        assert any(r.icon == WARN and "pruning" in r.detail for r in mission_results)

    def test_memory_stats(self, instance_dir):
        (instance_dir / "memory" / "summary.md").write_text("session 1\nsession 2\n")
        results = _check_journal_memory(instance_dir)
        mem_results = [r for r in results if "memory" in r.label]
        assert any(r.icon == OK for r in mem_results)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestFormatDuration:
    def test_seconds(self):
        assert _format_duration(30) == "30s"

    def test_minutes(self):
        assert _format_duration(150) == "2m"

    def test_hours(self):
        assert _format_duration(7200) == "2.0h"

    def test_days(self):
        assert _format_duration(172800) == "2.0d"


class TestDirSizeMb:
    def test_empty_dir(self, tmp_path):
        d = tmp_path / "empty"
        d.mkdir()
        assert _dir_size_mb(d) == 0.0

    def test_dir_with_files(self, tmp_path):
        d = tmp_path / "data"
        d.mkdir()
        (d / "file.txt").write_bytes(b"x" * 1024)
        size = _dir_size_mb(d)
        assert 0 < size < 0.01  # ~1KB


# ---------------------------------------------------------------------------
# Full handle() integration
# ---------------------------------------------------------------------------

class TestHandle:
    @patch("skills.core.doctor.handler._get_provider", return_value="claude")
    @patch("skills.core.doctor.handler._check_binaries")
    def test_basic_output(self, mock_bins, mock_prov, koan_root, instance_dir):
        mock_bins.return_value = [CheckResult(OK, "python 3.11")]
        ctx = _make_ctx(koan_root, instance_dir)
        output = handle(ctx)
        assert "Koan Doctor" in output
        assert "ok" in output

    @patch("skills.core.doctor.handler._get_provider", return_value="claude")
    @patch("skills.core.doctor.handler._check_binaries")
    def test_fix_mode_applies_fixes(self, mock_bins, mock_prov, koan_root, instance_dir):
        fix_called = []

        def mock_fix():
            fix_called.append(True)
            return "Fixed something"

        mock_bins.return_value = [
            CheckResult(WARN, "test issue", "broken", fixable=True, fix_action=mock_fix)
        ]
        ctx = _make_ctx(koan_root, instance_dir, args="--fix")
        output = handle(ctx)
        assert len(fix_called) == 1
        assert "Fixes applied" in output
        assert "Fixed something" in output

    @patch("skills.core.doctor.handler._get_provider", return_value="claude")
    @patch("skills.core.doctor.handler._check_binaries")
    def test_no_fix_without_flag(self, mock_bins, mock_prov, koan_root, instance_dir):
        mock_bins.return_value = [
            CheckResult(WARN, "test issue", "broken", fixable=True, fix_action=lambda: "nope")
        ]
        ctx = _make_ctx(koan_root, instance_dir, args="")
        output = handle(ctx)
        assert "auto-fixable" in output
        assert "Fixes applied" not in output

    @patch("skills.core.doctor.handler._get_provider", return_value="claude")
    def test_counts_in_summary(self, mock_prov, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir)
        output = handle(ctx)
        # Should have a summary line with counts
        assert " ok, " in output
        assert " warn, " in output
        assert " fail" in output


# ---------------------------------------------------------------------------
# Fix actions (integration)
# ---------------------------------------------------------------------------

class TestFixActions:
    @patch("skills.core.doctor.handler._get_provider", return_value="claude")
    @patch("app.pid_manager.check_pidfile", return_value=None)
    @patch("app.pid_manager._read_pid", return_value=99999)
    def test_stale_pid_fix(self, mock_read, mock_check, mock_prov, koan_root):
        """Stale PID file should be fixable and actually remove the file."""
        pid_file = koan_root / ".koan-pid-run"
        pid_file.write_text("99999")

        results = _check_processes(koan_root)
        fixable = [r for r in results if r.fixable]
        assert len(fixable) >= 1

        # Execute the fix
        for r in fixable:
            if r.fix_action:
                msg = r.fix_action()
                assert msg is not None

    @patch("app.pid_manager.check_pidfile", return_value=None)
    def test_orphaned_stop_fix(self, mock_check, koan_root):
        """Orphaned .koan-stop should be removable."""
        stop_file = koan_root / ".koan-stop"
        stop_file.write_text("STOP")

        results = _check_signal_files(koan_root)
        fixable = [r for r in results if r.fixable]
        assert len(fixable) == 1

        msg = fixable[0].fix_action()
        assert "Removed" in msg
        assert not stop_file.exists()

    @patch("app.pid_manager.check_pidfile", return_value=None)
    def test_orphaned_pause_fix(self, mock_check, koan_root):
        """Orphaned .koan-pause + reason should both be removed."""
        (koan_root / ".koan-pause").write_text("1")
        (koan_root / ".koan-pause-reason").write_text("quota")

        results = _check_signal_files(koan_root)
        fixable = [r for r in results if r.fixable and ".koan-pause" in r.label]
        assert len(fixable) == 1

        msg = fixable[0].fix_action()
        assert "Removed" in msg
        assert not (koan_root / ".koan-pause").exists()
        assert not (koan_root / ".koan-pause-reason").exists()
