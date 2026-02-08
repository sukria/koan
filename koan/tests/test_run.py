"""Tests for app.run — the full Python main loop."""

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def koan_root(tmp_path):
    """Create a minimal koan root with instance directory."""
    instance = tmp_path / "instance"
    instance.mkdir()
    (instance / "missions.md").write_text("# Missions\n\n## En attente\n\n## En cours\n\n## Terminées\n")
    (instance / "config.yaml").write_text("max_runs: 5\ninterval: 10\n")
    (tmp_path / "koan" / "app").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def projects(tmp_path):
    """Create project directories."""
    p1 = tmp_path / "proj1"
    p1.mkdir()
    p2 = tmp_path / "proj2"
    p2.mkdir()
    return [(str(p1), "proj1"), (str(p2), "proj2")]


# ---------------------------------------------------------------------------
# Test: Colored logging
# ---------------------------------------------------------------------------

class TestLog:
    def test_log_outputs_category(self, capsys):
        from app.run import log, _init_colors
        _init_colors()
        log("koan", "hello")
        out = capsys.readouterr().out
        assert "[koan]" in out
        assert "hello" in out

    def test_log_all_categories(self, capsys):
        from app.run import log, _init_colors
        _init_colors()
        for cat in ["koan", "error", "init", "health", "git", "mission", "quota", "pause"]:
            log(cat, f"test {cat}")
        out = capsys.readouterr().out
        for cat in ["koan", "error", "init", "health", "git", "mission", "quota", "pause"]:
            assert f"[{cat}]" in out

    def test_log_unknown_category(self, capsys):
        from app.run import log, _init_colors
        _init_colors()
        log("custom", "msg")
        out = capsys.readouterr().out
        assert "[custom]" in out


# ---------------------------------------------------------------------------
# Test: parse_projects
# ---------------------------------------------------------------------------

class TestParseProjects:
    def test_multi_project(self, tmp_path):
        from app.run import parse_projects
        p1 = tmp_path / "a"
        p2 = tmp_path / "b"
        p1.mkdir()
        p2.mkdir()
        result = parse_projects(f"a:{p1};b:{p2}")
        assert len(result) == 2
        assert result[0] == ("a", str(p1))
        assert result[1] == ("b", str(p2))

    def test_single_project(self, tmp_path):
        from app.run import parse_projects
        p = tmp_path / "proj"
        p.mkdir()
        result = parse_projects("", str(p))
        assert len(result) == 1
        assert result[0] == ("default", str(p))

    def test_no_project_exits(self):
        from app.run import parse_projects
        with pytest.raises(SystemExit):
            parse_projects("", "")

    def test_nonexistent_path_exits(self, tmp_path):
        from app.run import parse_projects
        with pytest.raises(SystemExit):
            parse_projects(f"bad:{tmp_path}/nonexistent")

    def test_too_many_projects(self, tmp_path):
        from app.run import parse_projects
        # 51 projects
        dirs = []
        for i in range(51):
            d = tmp_path / f"p{i}"
            d.mkdir()
            dirs.append(f"p{i}:{d}")
        with pytest.raises(SystemExit):
            parse_projects(";".join(dirs))


# ---------------------------------------------------------------------------
# Test: set_status
# ---------------------------------------------------------------------------

class TestSetStatus:
    def test_writes_status_file(self, tmp_path):
        from app.run import set_status
        set_status(str(tmp_path), "Running")
        assert (tmp_path / ".koan-status").read_text() == "Running"

    def test_overwrites_status(self, tmp_path):
        from app.run import set_status
        set_status(str(tmp_path), "First")
        set_status(str(tmp_path), "Second")
        assert (tmp_path / ".koan-status").read_text() == "Second"


# ---------------------------------------------------------------------------
# Test: SignalState
# ---------------------------------------------------------------------------

class TestSignalState:
    def test_default_state(self):
        from app.run import SignalState
        s = SignalState()
        assert s.task_running is False
        assert s.first_ctrl_c == 0
        assert s.claude_proc is None
        assert s.timeout == 10


# ---------------------------------------------------------------------------
# Test: _on_sigint
# ---------------------------------------------------------------------------

class TestOnSigint:
    def test_no_task_raises_keyboard_interrupt(self):
        from app.run import _on_sigint, _sig
        _sig.task_running = False
        with pytest.raises(KeyboardInterrupt):
            _on_sigint(signal.SIGINT, None)

    def test_first_ctrl_c_warns(self, capsys):
        from app.run import _on_sigint, _sig, _init_colors
        _init_colors()
        _sig.task_running = True
        _sig.first_ctrl_c = 0
        _on_sigint(signal.SIGINT, None)
        assert _sig.first_ctrl_c > 0
        out = capsys.readouterr().out
        assert "Press CTRL-C again" in out

    def test_second_ctrl_c_raises(self, capsys):
        from app.run import _on_sigint, _sig, _init_colors
        _init_colors()
        _sig.task_running = True
        _sig.first_ctrl_c = time.time()  # Just set
        _sig.claude_proc = MagicMock()
        _sig.claude_proc.poll.return_value = None
        with pytest.raises(KeyboardInterrupt):
            _on_sigint(signal.SIGINT, None)
        _sig.claude_proc.terminate.assert_called_once()

    def test_expired_timeout_resets(self, capsys):
        from app.run import _on_sigint, _sig, _init_colors
        _init_colors()
        _sig.task_running = True
        _sig.first_ctrl_c = time.time() - 20  # Expired
        _on_sigint(signal.SIGINT, None)
        # Should be treated as first CTRL-C (warning)
        assert _sig.first_ctrl_c > time.time() - 2
        out = capsys.readouterr().out
        assert "Press CTRL-C again" in out


# ---------------------------------------------------------------------------
# Test: _has_pending_missions
# ---------------------------------------------------------------------------

class TestHasPendingMissions:
    def test_no_missions(self, koan_root):
        from app.run import _has_pending_missions
        instance = str(koan_root / "instance")
        assert _has_pending_missions(instance) is False

    def test_with_missions(self, koan_root):
        from app.run import _has_pending_missions
        instance = koan_root / "instance"
        (instance / "missions.md").write_text(
            "# Missions\n\n## En attente\n\n- Do something\n\n## En cours\n\n## Terminées\n"
        )
        assert _has_pending_missions(str(instance)) is True

    def test_nonexistent_file(self, tmp_path):
        from app.run import _has_pending_missions
        assert _has_pending_missions(str(tmp_path)) is False


# ---------------------------------------------------------------------------
# Test: _cleanup_temp
# ---------------------------------------------------------------------------

class TestCleanupTemp:
    def test_removes_files(self, tmp_path):
        from app.run import _cleanup_temp
        f1 = tmp_path / "a.tmp"
        f2 = tmp_path / "b.tmp"
        f1.write_text("data")
        f2.write_text("data")
        _cleanup_temp(str(f1), str(f2))
        assert not f1.exists()
        assert not f2.exists()

    def test_ignores_missing(self, tmp_path):
        from app.run import _cleanup_temp
        # Should not raise
        _cleanup_temp(str(tmp_path / "nonexistent"))


# ---------------------------------------------------------------------------
# Test: _commit_instance
# ---------------------------------------------------------------------------

class TestCommitInstance:
    def test_commits_when_changes(self, tmp_path):
        from app.run import _commit_instance
        # Init git repo
        subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"],
                       cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"],
                       cwd=str(tmp_path), capture_output=True)
        (tmp_path / "file.txt").write_text("initial")
        subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True)

        # Make a change
        (tmp_path / "file.txt").write_text("modified")
        _commit_instance(str(tmp_path), "test commit")

        # Verify commit happened
        result = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=str(tmp_path), capture_output=True, text=True,
        )
        assert "test commit" in result.stdout


# ---------------------------------------------------------------------------
# Test: _notify
# ---------------------------------------------------------------------------

class TestNotify:
    @patch("app.run.format_and_send", create=True)
    def test_notify_calls_format_and_send(self, mock_send):
        with patch("app.notify.format_and_send", mock_send):
            from app.run import _notify
            _notify("/fake/instance", "hello")
            # Should not raise even if import fails


# ---------------------------------------------------------------------------
# Test: plan_iteration
# ---------------------------------------------------------------------------

class TestPlanIteration:
    @patch("app.run.subprocess.run")
    @patch("app.run.resolve_focus_area", return_value="General autonomous work")
    @patch("app.run.should_run_contemplative", return_value=False)
    @patch("app.run.get_contemplative_chance", return_value=10)
    def test_autonomous_mode(self, mock_chance, mock_contemp, mock_focus, mock_subproc, koan_root):
        from app.run import plan_iteration

        # Mock subprocess calls (usage_estimator, usage_tracker, pick_mission)
        def side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            # pick_mission returns empty (autonomous)
            if any("pick_mission" in str(c) for c in cmd):
                result.stdout = ""
            # usage_tracker returns implement mode
            elif any("usage_tracker" in str(c) for c in cmd):
                result.stdout = "implement:50:Normal budget:0"
            # focus_manager returns not in focus
            elif any("focus_manager" in str(c) for c in cmd):
                result.returncode = 1  # Not in focus
            return result

        mock_subproc.side_effect = side_effect

        instance = str(koan_root / "instance")
        projects = [("test", str(koan_root))]
        os.environ["KOAN_PROJECTS"] = f"test:{koan_root}"

        result = plan_iteration(instance, str(koan_root), projects, 1, 0, 5)
        assert result["action"] == "autonomous"
        assert result["project_name"] == "test"

    @patch("app.run.subprocess.run")
    @patch("app.run.resolve_focus_area", return_value="Execute assigned mission")
    def test_mission_mode(self, mock_focus, mock_subproc, koan_root):
        from app.run import plan_iteration

        def side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            if any("pick_mission" in str(c) for c in cmd):
                result.stdout = "test:Fix the bug"
            elif any("usage_tracker" in str(c) for c in cmd):
                result.stdout = "implement:50:Normal:0"
            return result

        mock_subproc.side_effect = side_effect

        instance = str(koan_root / "instance")
        projects = [("test", str(koan_root))]
        os.environ["KOAN_PROJECTS"] = f"test:{koan_root}"

        result = plan_iteration(instance, str(koan_root), projects, 1, 0, 5)
        assert result["action"] == "mission"
        assert result["mission_title"] == "Fix the bug"

    @patch("app.run.subprocess.run")
    def test_unknown_project_error(self, mock_subproc, koan_root):
        from app.run import plan_iteration

        def side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            if any("pick_mission" in str(c) for c in cmd):
                result.stdout = "unknown_project:Do things"
            elif any("usage_tracker" in str(c) for c in cmd):
                result.stdout = "implement:50:Normal:0"
            return result

        mock_subproc.side_effect = side_effect

        instance = str(koan_root / "instance")
        projects = [("test", str(koan_root))]
        os.environ["KOAN_PROJECTS"] = f"test:{koan_root}"

        result = plan_iteration(instance, str(koan_root), projects, 1, 0, 5)
        assert result["action"] == "error"
        assert "unknown_project" in result["error"]


# ---------------------------------------------------------------------------
# Test: handle_pause
# ---------------------------------------------------------------------------

class TestHandlePause:
    def test_auto_resume(self, koan_root):
        from app.run import handle_pause

        instance = str(koan_root / "instance")
        (koan_root / ".koan-pause").touch()

        with patch("app.pause_manager.check_and_resume", return_value="Quota reset"):
            result = handle_pause(str(koan_root), instance, [("test", "/tmp")], 5)
        assert result == "resume"

    def test_manual_resume(self, koan_root):
        from app.run import handle_pause

        instance = str(koan_root / "instance")
        # No .koan-pause file = manual resume

        with patch("app.pause_manager.check_and_resume", return_value=None):
            result = handle_pause(str(koan_root), instance, [("test", "/tmp")], 5)
        assert result == "resume"

    @patch("app.run.time.sleep")
    @patch("app.run.run_claude_task")
    def test_contemplative_on_pause(self, mock_claude, mock_sleep, koan_root):
        from app.run import handle_pause
        import random

        instance = str(koan_root / "instance")
        (koan_root / ".koan-pause").touch()

        # Force contemplative to trigger (roll < 50)
        with patch("app.pause_manager.check_and_resume", return_value=None), \
             patch("random.randint", return_value=10), \
             patch("app.run.subprocess.run") as mock_sub:
            # focus_manager check returns not in focus
            mock_sub.return_value = MagicMock(returncode=1)
            mock_claude.return_value = 0

            # Remove pause file partway through to simulate resume
            original_exists = Path.exists
            call_count = [0]
            def fake_exists(self):
                if str(self).endswith(".koan-pause"):
                    call_count[0] += 1
                    if call_count[0] > 3:
                        return False
                return original_exists(self)

            with patch.object(Path, "exists", fake_exists):
                result = handle_pause(str(koan_root), instance, [("test", str(koan_root))], 5)

            assert result == "resume"


# ---------------------------------------------------------------------------
# Test: run_claude_task
# ---------------------------------------------------------------------------

class TestRunClaudeTask:
    def test_captures_output(self, tmp_path):
        from app.run import run_claude_task, _sig
        _sig.task_running = False

        stdout_f = str(tmp_path / "out.txt")
        stderr_f = str(tmp_path / "err.txt")

        exit_code = run_claude_task(
            cmd=["echo", "hello world"],
            stdout_file=stdout_f,
            stderr_file=stderr_f,
            cwd=str(tmp_path),
        )

        assert exit_code == 0
        assert Path(stdout_f).read_text().strip() == "hello world"
        assert _sig.task_running is False

    def test_nonzero_exit(self, tmp_path):
        from app.run import run_claude_task

        stdout_f = str(tmp_path / "out.txt")
        stderr_f = str(tmp_path / "err.txt")

        exit_code = run_claude_task(
            cmd=["false"],
            stdout_file=stdout_f,
            stderr_file=stderr_f,
            cwd=str(tmp_path),
        )

        assert exit_code != 0

    def test_resets_signal_state(self, tmp_path):
        from app.run import run_claude_task, _sig

        stdout_f = str(tmp_path / "out.txt")
        stderr_f = str(tmp_path / "err.txt")

        run_claude_task(
            cmd=["echo", "test"],
            stdout_file=stdout_f,
            stderr_file=stderr_f,
            cwd=str(tmp_path),
        )

        assert _sig.task_running is False
        assert _sig.first_ctrl_c == 0
        assert _sig.claude_proc is None


# ---------------------------------------------------------------------------
# Test: main (restart wrapper)
# ---------------------------------------------------------------------------

class TestMain:
    @patch("app.run.main_loop")
    def test_normal_exit(self, mock_loop):
        from app.run import main
        main()
        mock_loop.assert_called_once()

    @patch("app.run.main_loop")
    @patch("app.run.time.sleep")
    def test_restart_on_42(self, mock_sleep, mock_loop):
        from app.run import main
        call_count = [0]
        def side_effect():
            call_count[0] += 1
            if call_count[0] == 1:
                raise SystemExit(42)
            # Second call: normal exit
        mock_loop.side_effect = side_effect
        main()
        assert call_count[0] == 2
        mock_sleep.assert_called_once_with(1)

    @patch("app.run.main_loop")
    def test_other_exit_code_propagates(self, mock_loop):
        from app.run import main
        mock_loop.side_effect = SystemExit(1)
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# Test: main_loop basic flow
# ---------------------------------------------------------------------------

class TestMainLoop:
    @patch("app.run.subprocess.run")
    @patch("app.run.run_startup", return_value=(5, 10, "koan/"))
    @patch("app.run.acquire_pid")
    @patch("app.run.release_pid")
    def test_stop_file_exits_loop(self, mock_release, mock_acquire, mock_startup, mock_subproc, koan_root):
        from app.run import main_loop

        os.environ["KOAN_ROOT"] = str(koan_root)
        os.environ["KOAN_PROJECTS"] = f"test:{koan_root}"

        # Create stop file
        (koan_root / ".koan-stop").touch()
        (koan_root / ".koan-project").write_text("test")

        with patch("app.run._notify"):
            main_loop()

        # Should have exited cleanly
        mock_release.assert_called()
        assert not (koan_root / ".koan-stop").exists()

    @patch("app.run.subprocess.run")
    @patch("app.run.run_startup", return_value=(5, 10, "koan/"))
    @patch("app.run.acquire_pid")
    @patch("app.run.release_pid")
    def test_restart_file_exits_42(self, mock_release, mock_acquire, mock_startup, mock_subproc, koan_root):
        from app.run import main_loop

        os.environ["KOAN_ROOT"] = str(koan_root)
        os.environ["KOAN_PROJECTS"] = f"test:{koan_root}"

        # Create restart file with mtime in the future (after start_time set inside main_loop)
        restart_file = koan_root / ".koan-restart"
        restart_file.write_text("restart")
        future = time.time() + 3600
        os.utime(str(restart_file), (future, future))

        with pytest.raises(SystemExit) as exc:
            with patch("app.run._notify"):
                main_loop()
        assert exc.value.code == 42


# ---------------------------------------------------------------------------
# Test: bold helpers
# ---------------------------------------------------------------------------

class TestBoldHelpers:
    def test_bold_cyan(self):
        from app.run import bold_cyan, _init_colors
        _init_colors()
        result = bold_cyan("test")
        assert "test" in result

    def test_bold_green(self):
        from app.run import bold_green, _init_colors
        _init_colors()
        result = bold_green("test")
        assert "test" in result


# ---------------------------------------------------------------------------
# Test: CLI entry point
# ---------------------------------------------------------------------------

class TestCLI:
    def test_module_runnable(self):
        """Verify the module can be imported without side effects."""
        import app.run
        assert hasattr(app.run, "main")
        assert hasattr(app.run, "main_loop")
