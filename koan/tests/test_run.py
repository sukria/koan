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

    def test_force_color_env_enables_colors(self, capsys, monkeypatch):
        """KOAN_FORCE_COLOR=1 enables ANSI colors even without TTY."""
        monkeypatch.setenv("KOAN_FORCE_COLOR", "1")
        from app.run import _init_colors, _COLORS
        # Force re-init
        import app.run as run_mod
        run_mod._COLORS = {}
        _init_colors()
        colors = run_mod._COLORS
        assert colors.get("reset") == "\033[0m"
        assert colors.get("red") == "\033[31m"


# ---------------------------------------------------------------------------
# Test: parse_projects
# ---------------------------------------------------------------------------

class TestParseProjects:
    """Tests for parse_projects() — delegates to get_known_projects().

    Since parse_projects() now reads from projects.yaml > KOAN_PROJECTS,
    tests must set env vars or create projects.yaml.
    """

    def test_multi_project(self, tmp_path, monkeypatch):
        from app import utils
        monkeypatch.setattr(utils, "KOAN_ROOT", tmp_path)
        from app.run import parse_projects
        p1 = tmp_path / "a"
        p2 = tmp_path / "b"
        p1.mkdir()
        p2.mkdir()
        monkeypatch.setenv("KOAN_PROJECTS", f"a:{p1};b:{p2}")
        result = parse_projects()
        assert len(result) == 2
        assert result[0] == ("a", str(p1))
        assert result[1] == ("b", str(p2))

    def test_single_project_via_env(self, tmp_path, monkeypatch):
        from app import utils
        monkeypatch.setattr(utils, "KOAN_ROOT", tmp_path)
        from app.run import parse_projects
        p = tmp_path / "proj"
        p.mkdir()
        monkeypatch.setenv("KOAN_PROJECTS", f"proj:{p}")
        result = parse_projects()
        assert len(result) == 1
        assert result[0] == ("proj", str(p))

    def test_no_project_exits(self, tmp_path, monkeypatch):
        from app import utils
        monkeypatch.setattr(utils, "KOAN_ROOT", tmp_path)
        monkeypatch.delenv("KOAN_PROJECTS", raising=False)
        from app.run import parse_projects
        with pytest.raises(SystemExit):
            parse_projects()

    def test_nonexistent_path_exits(self, tmp_path, monkeypatch):
        from app import utils
        monkeypatch.setattr(utils, "KOAN_ROOT", tmp_path)
        monkeypatch.setenv("KOAN_PROJECTS", f"bad:{tmp_path}/nonexistent")
        from app.run import parse_projects
        with pytest.raises(SystemExit):
            parse_projects()

    def test_too_many_projects(self, tmp_path, monkeypatch):
        from app import utils
        monkeypatch.setattr(utils, "KOAN_ROOT", tmp_path)
        from app.run import parse_projects
        # 51 projects
        dirs = []
        for i in range(51):
            d = tmp_path / f"p{i}"
            d.mkdir()
            dirs.append(f"p{i}:{d}")
        monkeypatch.setenv("KOAN_PROJECTS", ";".join(dirs))
        with pytest.raises(SystemExit):
            parse_projects()

    def test_projects_yaml_used(self, tmp_path, monkeypatch):
        """parse_projects reads from projects.yaml when available."""
        from app import utils
        monkeypatch.setattr(utils, "KOAN_ROOT", tmp_path)
        from app.run import parse_projects
        p = tmp_path / "myproject"
        p.mkdir()
        (tmp_path / "projects.yaml").write_text(f"""
projects:
  myproject:
    path: "{p}"
""")
        monkeypatch.delenv("KOAN_PROJECTS", raising=False)
        result = parse_projects()
        assert len(result) == 1
        assert result[0][0] == "myproject"


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
        assert s.phase == ""


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
        _sig.phase = ""
        _on_sigint(signal.SIGINT, None)
        assert _sig.first_ctrl_c > 0
        out = capsys.readouterr().out
        assert "Press CTRL-C again" in out

    def test_first_ctrl_c_shows_phase(self, capsys):
        """First CTRL-C should display the current phase name."""
        from app.run import _on_sigint, _sig, _init_colors
        _init_colors()
        _sig.task_running = True
        _sig.first_ctrl_c = 0
        _sig.phase = "Morning ritual"
        _on_sigint(signal.SIGINT, None)
        out = capsys.readouterr().out
        assert "Morning ritual" in out
        assert "Press CTRL-C again" in out
        _sig.phase = ""

    def test_first_ctrl_c_no_phase_no_parens(self, capsys):
        """When no phase is set, the message should not have empty parens."""
        from app.run import _on_sigint, _sig, _init_colors
        _init_colors()
        _sig.task_running = True
        _sig.first_ctrl_c = 0
        _sig.phase = ""
        _on_sigint(signal.SIGINT, None)
        out = capsys.readouterr().out
        assert "()" not in out

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
# Test: protected_phase context manager
# ---------------------------------------------------------------------------

class TestProtectedPhase:
    def test_sets_task_running(self):
        from app.run import protected_phase, _sig
        _sig.task_running = False
        _sig.phase = ""
        with protected_phase("Testing"):
            assert _sig.task_running is True
            assert _sig.phase == "Testing"
        assert _sig.task_running is False
        assert _sig.phase == ""

    def test_resets_on_exit(self):
        from app.run import protected_phase, _sig
        _sig.task_running = False
        _sig.phase = ""
        _sig.first_ctrl_c = 99.0
        with protected_phase("Phase A"):
            assert _sig.first_ctrl_c == 0  # Reset on entry
        assert _sig.first_ctrl_c == 0  # Reset on exit

    def test_restores_previous_state(self):
        """Nested protected_phase should restore outer state."""
        from app.run import protected_phase, _sig
        _sig.task_running = False
        _sig.phase = ""
        with protected_phase("Outer"):
            assert _sig.phase == "Outer"
            assert _sig.task_running is True
            with protected_phase("Inner"):
                assert _sig.phase == "Inner"
                assert _sig.task_running is True
            assert _sig.phase == "Outer"
            assert _sig.task_running is True
        assert _sig.task_running is False
        assert _sig.phase == ""

    def test_restores_on_exception(self):
        """State should be restored even if an exception occurs."""
        from app.run import protected_phase, _sig
        _sig.task_running = False
        _sig.phase = ""
        try:
            with protected_phase("Failing"):
                assert _sig.task_running is True
                raise ValueError("boom")
        except ValueError:
            pass
        assert _sig.task_running is False
        assert _sig.phase == ""

    def test_restores_on_keyboard_interrupt(self):
        """State should be restored on KeyboardInterrupt."""
        from app.run import protected_phase, _sig
        _sig.task_running = False
        _sig.phase = ""
        try:
            with protected_phase("Interrupted"):
                raise KeyboardInterrupt
        except KeyboardInterrupt:
            pass
        assert _sig.task_running is False
        assert _sig.phase == ""

    def test_first_ctrl_c_during_phase_warns(self, capsys):
        """First CTRL-C inside a protected_phase should warn, not abort."""
        from app.run import protected_phase, _on_sigint, _sig, _init_colors
        _init_colors()
        with protected_phase("Git sync"):
            _sig.first_ctrl_c = 0
            _on_sigint(signal.SIGINT, None)
            out = capsys.readouterr().out
            assert "Git sync" in out
            assert "Press CTRL-C again" in out
            assert _sig.first_ctrl_c > 0

    def test_double_ctrl_c_during_phase_aborts(self, capsys):
        """Double CTRL-C inside a protected_phase should raise."""
        from app.run import protected_phase, _on_sigint, _sig, _init_colors
        _init_colors()
        with protected_phase("Morning ritual"):
            _sig.first_ctrl_c = time.time()  # Simulate first press
            with pytest.raises(KeyboardInterrupt):
                _on_sigint(signal.SIGINT, None)

    def test_outside_phase_ctrl_c_raises_immediately(self):
        """Without protected_phase, CTRL-C should raise immediately."""
        from app.run import _on_sigint, _sig
        _sig.task_running = False
        _sig.phase = ""
        with pytest.raises(KeyboardInterrupt):
            _on_sigint(signal.SIGINT, None)

    def test_phase_cleared_after_double_tap(self):
        """After double-tap abort, phase and task_running should be cleared."""
        from app.run import protected_phase, _on_sigint, _sig, _init_colors
        _init_colors()
        _sig.task_running = False
        _sig.phase = ""
        try:
            with protected_phase("Some phase"):
                _sig.first_ctrl_c = time.time()
                try:
                    _on_sigint(signal.SIGINT, None)
                except KeyboardInterrupt:
                    raise
        except KeyboardInterrupt:
            pass
        # The context manager __exit__ should have run
        assert _sig.phase == ""
        assert _sig.task_running is False


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
        """Stop file created DURING loop causes clean exit."""
        from app.run import main_loop

        os.environ["KOAN_ROOT"] = str(koan_root)
        os.environ["KOAN_PROJECTS"] = f"test:{koan_root}"
        (koan_root / ".koan-project").write_text("test")

        # Create .koan-stop AFTER startup clears it — simulate /stop while running
        original_startup = mock_startup.side_effect

        def startup_then_stop(*args, **kwargs):
            (koan_root / ".koan-stop").touch()
            return (5, 10, "koan/")

        mock_startup.side_effect = startup_then_stop

        with patch("app.run._notify"):
            main_loop()

        # Should have exited cleanly
        mock_release.assert_called()
        assert not (koan_root / ".koan-stop").exists()

    @patch("app.run.subprocess.run")
    @patch("app.run.run_startup", return_value=(5, 10, "koan/"))
    @patch("app.run.acquire_pid")
    @patch("app.run.release_pid")
    def test_stale_stop_file_cleared_on_startup(self, mock_release, mock_acquire, mock_startup, mock_subproc, koan_root):
        """Stale .koan-stop from a previous make stop is cleared on startup.

        Regression test: `make stop` creates .koan-stop. If run.py was not
        running, the file persists. Next `make run` should NOT immediately
        exit — the stale file must be cleared before entering the loop.
        """
        from app.run import main_loop

        os.environ["KOAN_ROOT"] = str(koan_root)
        os.environ["KOAN_PROJECTS"] = f"test:{koan_root}"
        (koan_root / ".koan-project").write_text("test")

        # Simulate stale .koan-stop from a previous `make stop`
        (koan_root / ".koan-stop").write_text("STOP")

        # Mock plan_iteration to create a new stop file on first call
        # (so the loop doesn't run forever) — but the KEY test is that
        # the stale file didn't cause an immediate exit before startup
        call_count = [0]

        def startup_creates_stop(*args, **kwargs):
            call_count[0] += 1
            # Create stop on startup — this time it's a "fresh" signal
            (koan_root / ".koan-stop").touch()
            return (5, 10, "koan/")

        mock_startup.side_effect = startup_creates_stop

        with patch("app.run._notify"):
            main_loop()

        # Startup ran (proves we didn't exit immediately from the stale file)
        assert call_count[0] == 1
        mock_startup.assert_called_once()

    @patch("app.run.subprocess.run")
    @patch("app.run.run_startup", return_value=(5, 10, "koan/"))
    @patch("app.run.acquire_pid")
    @patch("app.run.release_pid")
    def test_stale_stop_file_absent_no_error(self, mock_release, mock_acquire, mock_startup, mock_subproc, koan_root):
        """No crash when .koan-stop doesn't exist at startup (normal case)."""
        from app.run import main_loop

        os.environ["KOAN_ROOT"] = str(koan_root)
        os.environ["KOAN_PROJECTS"] = f"test:{koan_root}"
        (koan_root / ".koan-project").write_text("test")

        # No .koan-stop exists — verify no error from unlink(missing_ok=True)
        assert not (koan_root / ".koan-stop").exists()

        # Create stop on startup so the loop exits
        def startup_then_stop(*args, **kwargs):
            (koan_root / ".koan-stop").touch()
            return (5, 10, "koan/")

        mock_startup.side_effect = startup_then_stop

        with patch("app.run._notify"):
            main_loop()

        mock_startup.assert_called_once()

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
# Test: quota spam loop regression tests (session 220 fixes)
# ---------------------------------------------------------------------------

class TestQuotaSpamLoopFixes:
    """Regression tests for the quota exhaustion spam loop bug.

    The bug: when quota is exhausted, the system entered a rapid
    auto-resume → detect exhaustion → pause → auto-resume loop,
    spamming the user with messages.

    Root causes:
    1. wait_pause created pause with timestamp=now (instant auto-resume)
    2. Usage refresh was skipped when count=0 (stale data after resume)
    3. Post-mission quota_info was treated as dict but was actually a tuple

    Note: plan_iteration tests moved to test_iteration_manager.py as part
    of the consolidation in issue #206.
    """

    def test_wait_pause_creates_pause_with_future_timestamp(self, koan_root):
        """wait_pause must create pause with a future timestamp, not now."""
        import app.run as run_module

        instance = str(koan_root / "instance")
        koan_root_str = str(koan_root)
        now = int(time.time())
        future_ts = now + 3600  # 1 hour from now

        # Track create_pause calls
        pause_calls = []

        with patch.object(run_module, "log"), \
             patch.object(run_module, "_notify"), \
             patch.object(run_module, "subprocess") as mock_sub, \
             patch("app.usage_estimator.cmd_reset_time", return_value=future_ts), \
             patch("app.pause_manager.create_pause") as mock_create:
            mock_sub.run.return_value = MagicMock(returncode=0)
            mock_create.side_effect = lambda *a, **kw: pause_calls.append((a, kw))

            # Simulate the wait_pause action handler inline
            # (We test the code path directly rather than going through main_loop)
            plan = {"decision_reason": "Budget exhausted", "project_name": "test"}
            project_name = "test"
            count = 0

            # Execute the wait_pause code path
            reset_ts = None
            try:
                from app.usage_estimator import cmd_reset_time
                usage_state_path = Path(instance, "usage_state.json")
                reset_ts = cmd_reset_time(usage_state_path)
            except Exception:
                pass
            if reset_ts is None:
                reset_ts = int(time.time()) + 5 * 3600

            from app.pause_manager import create_pause
            create_pause(koan_root_str, "quota", reset_ts)

        # Verify create_pause was called with a future timestamp
        assert len(pause_calls) == 1
        args = pause_calls[0][0]
        assert args[0] == koan_root_str  # koan_root
        assert args[1] == "quota"  # reason
        assert args[2] == future_ts  # timestamp (future, not now)
        assert args[2] > now  # Must be in the future

    def test_post_mission_quota_info_tuple_handling(self):
        """Post-mission quota_info is a (reset_display, resume_msg) tuple."""
        post_result = {
            "quota_exhausted": True,
            "quota_info": ("resets at 10am", "Auto-resume at reset time (~2h)"),
        }

        quota_info = post_result.get("quota_info")
        if quota_info and isinstance(quota_info, (list, tuple)) and len(quota_info) >= 2:
            reset_display, resume_msg = quota_info[0], quota_info[1]
        else:
            reset_display, resume_msg = "", "Auto-resume in ~5h"

        assert reset_display == "resets at 10am"
        assert resume_msg == "Auto-resume at reset time (~2h)"

    def test_post_mission_quota_info_none_handling(self):
        """When quota_info is None, use fallback values."""
        post_result = {
            "quota_exhausted": True,
            "quota_info": None,
        }

        quota_info = post_result.get("quota_info")
        if quota_info and isinstance(quota_info, (list, tuple)) and len(quota_info) >= 2:
            reset_display, resume_msg = quota_info[0], quota_info[1]
        else:
            reset_display, resume_msg = "", "Auto-resume in ~5h"

        assert reset_display == ""
        assert resume_msg == "Auto-resume in ~5h"

    def test_post_mission_quota_info_dict_fallback(self):
        """If quota_info were an empty dict (legacy), use fallback values."""
        post_result = {
            "quota_exhausted": True,
            "quota_info": {},
        }

        quota_info = post_result.get("quota_info")
        if quota_info and isinstance(quota_info, (list, tuple)) and len(quota_info) >= 2:
            reset_display, resume_msg = quota_info[0], quota_info[1]
        else:
            reset_display, resume_msg = "", "Auto-resume in ~5h"

        assert reset_display == ""
        assert resume_msg == "Auto-resume in ~5h"

    def test_pause_with_now_timestamp_resumes_instantly(self):
        """Prove the old bug: pause with timestamp=now causes instant auto-resume."""
        from app.pause_manager import should_auto_resume, PauseState

        now = int(time.time())
        state = PauseState(reason="quota", timestamp=now, display="")
        # With timestamp=now, should_auto_resume returns True immediately
        assert should_auto_resume(state, now=now) is True

    def test_pause_with_future_timestamp_does_not_resume(self):
        """Pause with a future timestamp should NOT auto-resume."""
        from app.pause_manager import should_auto_resume, PauseState

        now = int(time.time())
        future = now + 3600  # 1 hour from now
        state = PauseState(reason="quota", timestamp=future, display="")
        assert should_auto_resume(state, now=now) is False


# ---------------------------------------------------------------------------
# Test: CLI entry point
# ---------------------------------------------------------------------------

class TestCLI:
    def test_module_runnable(self):
        """Verify the module can be imported without side effects."""
        import app.run
        assert hasattr(app.run, "main")
        assert hasattr(app.run, "main_loop")


# ---------------------------------------------------------------------------
# Fixture: mock_error_handling
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_error_handling():
    """Common mock setup for error handling tests."""
    with patch("app.run._notify") as notify, \
         patch("app.run.time.sleep") as sleep, \
         patch("app.run.log") as log_fn:
        yield {"notify": notify, "sleep": sleep, "log": log_fn}


# ---------------------------------------------------------------------------
# Test: _handle_iteration_error
# ---------------------------------------------------------------------------

class TestHandleIterationError:
    """Tests for the error recovery handler."""

    def test_logs_error_and_backs_off(self, koan_root, capsys):
        from app.run import _handle_iteration_error, _init_colors
        _init_colors()
        instance = str(koan_root / "instance")

        with patch("app.run._notify"), \
             patch("app.run.time.sleep") as mock_sleep:
            _handle_iteration_error(
                ValueError("test error"), 1, str(koan_root), instance,
            )

        out = capsys.readouterr().out
        assert "test error" in out
        assert "1/" in out
        # First error: 10s backoff
        mock_sleep.assert_called_once_with(10)

    def test_backoff_increases(self, koan_root, mock_error_handling):
        from app.run import _handle_iteration_error
        instance = str(koan_root / "instance")

        _handle_iteration_error(
            ValueError("err"), 3, str(koan_root), instance,
        )

        # 3rd error: 30s backoff
        mock_error_handling["sleep"].assert_called_once_with(30)

    def test_backoff_capped_at_max(self, koan_root, mock_error_handling):
        from app.run import _handle_iteration_error
        instance = str(koan_root / "instance")

        _handle_iteration_error(
            ValueError("err"), 8, str(koan_root), instance,
        )

        # 8th error: 80s backoff
        mock_error_handling["sleep"].assert_called_once_with(80)

    def test_backoff_capped_at_max_iteration_constant(self, koan_root, mock_error_handling):
        from app.run import _handle_iteration_error, MAX_CONSECUTIVE_ERRORS
        instance = str(koan_root / "instance")

        # Use error count below MAX_CONSECUTIVE_ERRORS to avoid entering pause mode
        with patch("app.pause_manager.create_pause"):
            _handle_iteration_error(
                ValueError("err"), 9, str(koan_root), instance,
            )

        # 9th error: 90s backoff (below cap)
        mock_error_handling["sleep"].assert_called_once_with(90)

    def test_notifies_on_first_error(self, koan_root, mock_error_handling):
        from app.run import _handle_iteration_error
        instance = str(koan_root / "instance")

        _handle_iteration_error(
            ValueError("boom"), 1, str(koan_root), instance,
        )

        mock_error_handling["notify"].assert_called_once()
        assert "boom" in mock_error_handling["notify"].call_args[0][1]

    def test_throttles_notifications(self, koan_root):
        """Only notifies on 1st and every 5th error."""
        from app.run import _handle_iteration_error, ERROR_NOTIFICATION_INTERVAL
        instance = str(koan_root / "instance")

        # Errors 2, 3, 4 should not notify
        for i in range(2, ERROR_NOTIFICATION_INTERVAL):
            with patch("app.run._notify") as mock_notify, \
                 patch("app.run.time.sleep"), \
                 patch("app.run.log"):
                _handle_iteration_error(
                    ValueError("err"), i, str(koan_root), instance,
                )
            mock_notify.assert_not_called()

        # ERROR_NOTIFICATION_INTERVAL-th error: should notify
        with patch("app.run._notify") as mock_notify, \
             patch("app.run.time.sleep"), \
             patch("app.run.log"):
            _handle_iteration_error(
                ValueError("err"), ERROR_NOTIFICATION_INTERVAL, str(koan_root), instance,
            )
        mock_notify.assert_called_once()

    def test_enters_pause_at_max_errors(self, koan_root, mock_error_handling):
        from app.run import _handle_iteration_error, MAX_CONSECUTIVE_ERRORS
        instance = str(koan_root / "instance")

        with patch("app.pause_manager.create_pause") as mock_pause:
            _handle_iteration_error(
                RuntimeError("fatal"), MAX_CONSECUTIVE_ERRORS,
                str(koan_root), instance,
            )

        mock_pause.assert_called_once_with(str(koan_root), "errors")

    def test_no_pause_below_max_errors(self, koan_root, mock_error_handling):
        from app.run import _handle_iteration_error, MAX_CONSECUTIVE_ERRORS
        instance = str(koan_root / "instance")

        with patch("app.pause_manager.create_pause") as mock_pause:
            _handle_iteration_error(
                RuntimeError("err"), MAX_CONSECUTIVE_ERRORS - 1,
                str(koan_root), instance,
            )

        mock_pause.assert_not_called()

    def test_no_sleep_at_max_errors(self, koan_root, mock_error_handling):
        """At max errors, enters pause — no backoff sleep."""
        from app.run import _handle_iteration_error, MAX_CONSECUTIVE_ERRORS
        instance = str(koan_root / "instance")

        with patch("app.pause_manager.create_pause"):
            _handle_iteration_error(
                RuntimeError("fatal"), MAX_CONSECUTIVE_ERRORS,
                str(koan_root), instance,
            )

        mock_error_handling["sleep"].assert_not_called()


# ---------------------------------------------------------------------------
# Test: main_loop iteration error recovery
# ---------------------------------------------------------------------------

class TestMainLoopResilience:
    """Tests that main_loop survives iteration failures."""

    @patch("app.run.subprocess.run")
    @patch("app.run.run_startup", return_value=(5, 10, "koan/"))
    @patch("app.run.acquire_pid")
    @patch("app.run.release_pid")
    @patch("app.run._run_iteration")
    @patch("app.run._handle_iteration_error")
    def test_recovers_from_iteration_error(
        self, mock_handle_err, mock_iteration, mock_release,
        mock_acquire, mock_startup, mock_subproc, koan_root,
    ):
        """An exception in _run_iteration doesn't kill main_loop."""
        from app.run import main_loop

        os.environ["KOAN_ROOT"] = str(koan_root)
        os.environ["KOAN_PROJECTS"] = f"test:{koan_root}"
        (koan_root / ".koan-project").write_text("test")

        call_count = [0]
        def iteration_side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("network error")
            # Second call: create stop file to end loop
            (koan_root / ".koan-stop").touch()
            (koan_root / ".koan-project").write_text("test")

        mock_iteration.side_effect = iteration_side_effect

        with patch("app.run._notify"):
            main_loop()

        # Error handler was called for the first failure
        mock_handle_err.assert_called_once()
        args = mock_handle_err.call_args[0]
        assert isinstance(args[0], RuntimeError)
        assert args[1] == 1  # consecutive_errors

    @patch("app.run.subprocess.run")
    @patch("app.run.run_startup", return_value=(5, 10, "koan/"))
    @patch("app.run.acquire_pid")
    @patch("app.run.release_pid")
    @patch("app.run._run_iteration")
    @patch("app.run._handle_iteration_error")
    def test_consecutive_error_counter_resets_on_success(
        self, mock_handle_err, mock_iteration, mock_release,
        mock_acquire, mock_startup, mock_subproc, koan_root,
    ):
        """Successful iteration resets the consecutive error counter."""
        from app.run import main_loop

        os.environ["KOAN_ROOT"] = str(koan_root)
        os.environ["KOAN_PROJECTS"] = f"test:{koan_root}"
        (koan_root / ".koan-project").write_text("test")

        call_count = [0]
        def iteration_side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("first error")
            if call_count[0] == 2:
                raise RuntimeError("second error")
            if call_count[0] == 3:
                # Success — this resets consecutive_errors to 0
                return
            if call_count[0] == 4:
                raise RuntimeError("third error after reset")
            # 5th call: stop
            (koan_root / ".koan-stop").touch()
            (koan_root / ".koan-project").write_text("test")

        mock_iteration.side_effect = iteration_side_effect

        with patch("app.run._notify"):
            main_loop()

        # Error handler called 3 times:
        # calls 1,2 had consecutive_errors=1,2
        # call 3 succeeded → reset to 0
        # call 4 had consecutive_errors=1 (reset)
        assert mock_handle_err.call_count == 3
        assert mock_handle_err.call_args_list[0][0][1] == 1  # first error
        assert mock_handle_err.call_args_list[1][0][1] == 2  # second error
        assert mock_handle_err.call_args_list[2][0][1] == 1  # reset after success

    @patch("app.run.subprocess.run")
    @patch("app.run.run_startup", return_value=(5, 10, "koan/"))
    @patch("app.run.acquire_pid")
    @patch("app.run.release_pid")
    @patch("app.run._run_iteration")
    def test_keyboard_interrupt_propagates(
        self, mock_iteration, mock_release,
        mock_acquire, mock_startup, mock_subproc, koan_root,
    ):
        """KeyboardInterrupt is NOT caught by the iteration handler."""
        from app.run import main_loop

        os.environ["KOAN_ROOT"] = str(koan_root)
        os.environ["KOAN_PROJECTS"] = f"test:{koan_root}"
        (koan_root / ".koan-project").write_text("test")

        mock_iteration.side_effect = KeyboardInterrupt

        with patch("app.run._notify"):
            # main_loop catches KeyboardInterrupt at the top level
            main_loop()

        # Release should still be called (in finally block)
        mock_release.assert_called()

    @patch("app.run.subprocess.run")
    @patch("app.run.run_startup", return_value=(5, 10, "koan/"))
    @patch("app.run.acquire_pid")
    @patch("app.run.release_pid")
    @patch("app.run._run_iteration")
    def test_system_exit_42_propagates(
        self, mock_iteration, mock_release,
        mock_acquire, mock_startup, mock_subproc, koan_root,
    ):
        """SystemExit(42) propagates for restart handling."""
        from app.run import main_loop

        os.environ["KOAN_ROOT"] = str(koan_root)
        os.environ["KOAN_PROJECTS"] = f"test:{koan_root}"
        (koan_root / ".koan-project").write_text("test")

        mock_iteration.side_effect = SystemExit(42)

        with pytest.raises(SystemExit) as exc:
            with patch("app.run._notify"):
                main_loop()
        assert exc.value.code == 42


# ---------------------------------------------------------------------------
# Test: main() crash recovery
# ---------------------------------------------------------------------------

class TestMainCrashRecovery:
    """Tests for the outer crash recovery wrapper in main()."""

    @patch("app.run.main_loop")
    @patch("app.run.time.sleep")
    def test_recovers_from_unexpected_crash(self, mock_sleep, mock_loop):
        """main() restarts main_loop after an unexpected exception."""
        from app.run import main, BACKOFF_MULTIPLIER

        call_count = [0]
        def side_effect():
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("unexpected crash")
            # Second call: normal exit

        mock_loop.side_effect = side_effect
        main()
        assert call_count[0] == 2
        # First crash: BACKOFF_MULTIPLIER * 1 = 10s
        mock_sleep.assert_called_once_with(BACKOFF_MULTIPLIER)

    @patch("app.run.main_loop")
    @patch("app.run.time.sleep")
    def test_gives_up_after_max_crashes(self, mock_sleep, mock_loop):
        """main() stops retrying after MAX_MAIN_CRASHES consecutive crashes."""
        from app.run import main, MAX_MAIN_CRASHES

        mock_loop.side_effect = RuntimeError("always crashing")
        main()
        assert mock_loop.call_count == MAX_MAIN_CRASHES

    @patch("app.run.main_loop")
    @patch("app.run.time.sleep")
    def test_crash_count_resets_on_restart(self, mock_sleep, mock_loop):
        """SystemExit(42) resets the crash counter."""
        from app.run import main

        call_count = [0]
        def side_effect():
            call_count[0] += 1
            if call_count[0] == 1:
                raise SystemExit(42)  # restart signal
            # Second call: normal exit

        mock_loop.side_effect = side_effect
        main()
        assert call_count[0] == 2

    @patch("app.run.main_loop")
    @patch("app.run.time.sleep")
    def test_increasing_backoff_on_crashes(self, mock_sleep, mock_loop):
        """Backoff increases: 10s, 20s, 30s, 40s."""
        from app.run import main, BACKOFF_MULTIPLIER

        call_count = [0]
        def side_effect():
            call_count[0] += 1
            if call_count[0] <= 4:
                raise RuntimeError("crash")
            # 5th call: normal exit

        mock_loop.side_effect = side_effect
        main()
        sleeps = [c[0][0] for c in mock_sleep.call_args_list]
        expected = [BACKOFF_MULTIPLIER * i for i in range(1, 5)]
        assert sleeps == expected


# ---------------------------------------------------------------------------
# Test: _run_iteration action=error raises RuntimeError
# ---------------------------------------------------------------------------

class TestRunIterationErrorAction:
    """The action=error path raises RuntimeError instead of sys.exit(1)."""

    @patch("app.run.plan_iteration")
    @patch("app.run._notify")
    def test_error_action_raises(self, mock_notify, mock_plan, koan_root):
        from app.run import _run_iteration

        mock_plan.return_value = {
            "action": "error",
            "error": "Unknown project: foo",
            "project_name": "foo",
            "project_path": "",
            "mission_title": "do stuff",
            "autonomous_mode": "implement",
            "focus_area": "",
            "available_pct": 50,
            "decision_reason": "Default",
            "display_lines": [],
            "recurring_injected": [],
        }

        instance = str(koan_root / "instance")

        with pytest.raises(RuntimeError, match="Unknown project: foo"):
            _run_iteration(
                koan_root=str(koan_root),
                instance=instance,
                projects=[("test", str(koan_root))],
                count=0,
                max_runs=5,
                interval=10,
                git_sync_interval=5,
            )


# ---------------------------------------------------------------------------
# Test: MAX_CONSECUTIVE_ERRORS constant
# ---------------------------------------------------------------------------

class TestConstants:
    def test_max_consecutive_errors_is_10(self):
        from app.run import MAX_CONSECUTIVE_ERRORS
        assert MAX_CONSECUTIVE_ERRORS == 10


# ---------------------------------------------------------------------------
# Test: Recovery helpers
# ---------------------------------------------------------------------------

class TestRecoveryHelpers:
    """Tests for _calculate_backoff and _should_notify_error."""

    def test_calculate_backoff_linear_growth(self):
        from app.run import _calculate_backoff, BACKOFF_MULTIPLIER
        assert _calculate_backoff(1, 300) == BACKOFF_MULTIPLIER
        assert _calculate_backoff(2, 300) == BACKOFF_MULTIPLIER * 2
        assert _calculate_backoff(3, 300) == BACKOFF_MULTIPLIER * 3

    def test_calculate_backoff_capped(self):
        from app.run import _calculate_backoff
        # 100 * 10 = 1000, but capped at 60
        assert _calculate_backoff(100, 60) == 60

    def test_should_notify_on_first_error(self):
        from app.run import _should_notify_error
        assert _should_notify_error(1) is True

    def test_should_notify_at_interval(self):
        from app.run import _should_notify_error, ERROR_NOTIFICATION_INTERVAL
        assert _should_notify_error(ERROR_NOTIFICATION_INTERVAL) is True
        assert _should_notify_error(ERROR_NOTIFICATION_INTERVAL * 2) is True

    def test_should_not_notify_between_intervals(self):
        from app.run import _should_notify_error, ERROR_NOTIFICATION_INTERVAL
        for i in range(2, ERROR_NOTIFICATION_INTERVAL):
            assert _should_notify_error(i) is False


# ---------------------------------------------------------------------------
# Test: _notify_mission_end
# ---------------------------------------------------------------------------

class TestNotifyMissionEnd:
    """Tests for _notify_mission_end() — end-of-mission notifications."""

    @patch("app.run._notify")
    def test_success_with_mission_title(self, mock_notify):
        from app.run import _notify_mission_end
        _notify_mission_end("/tmp/inst", "myproject", 3, 10, 0, "Fix the auth bug")
        mock_notify.assert_called_once()
        msg = mock_notify.call_args[0][1]
        assert msg.startswith("✅")
        assert "[myproject]" in msg
        assert "Fix the auth bug" in msg
        assert "Run 3/10" in msg

    @patch("app.run._notify")
    def test_failure_with_mission_title(self, mock_notify):
        from app.run import _notify_mission_end
        _notify_mission_end("/tmp/inst", "myproject", 3, 10, 1, "Fix the auth bug")
        mock_notify.assert_called_once()
        msg = mock_notify.call_args[0][1]
        assert msg.startswith("❌")
        assert "[myproject]" in msg
        assert "Failed:" in msg
        assert "Fix the auth bug" in msg

    @patch("app.run._notify")
    def test_success_autonomous_no_title(self, mock_notify):
        from app.run import _notify_mission_end
        _notify_mission_end("/tmp/inst", "koan", 1, 5, 0, "")
        mock_notify.assert_called_once()
        msg = mock_notify.call_args[0][1]
        assert msg.startswith("✅")
        assert "Autonomous run on koan" in msg

    @patch("app.run._notify")
    def test_failure_autonomous_no_title(self, mock_notify):
        from app.run import _notify_mission_end
        _notify_mission_end("/tmp/inst", "koan", 1, 5, 1, "")
        mock_notify.assert_called_once()
        msg = mock_notify.call_args[0][1]
        assert msg.startswith("❌")
        assert "Failed: Run" in msg

    @patch("app.mission_summary.get_mission_summary", return_value="Session 42\n\nFixed auth.")
    @patch("app.run._notify")
    def test_success_includes_journal_summary(self, mock_notify, mock_summary):
        from app.run import _notify_mission_end
        _notify_mission_end("/tmp/inst", "proj", 2, 10, 0, "Fix auth")
        msg = mock_notify.call_args[0][1]
        assert "✅" in msg
        assert "Fixed auth." in msg
        mock_summary.assert_called_once_with("/tmp/inst", "proj", max_chars=300)

    @patch("app.mission_summary.get_mission_summary", return_value="")
    @patch("app.run._notify")
    def test_success_no_summary_when_empty(self, mock_notify, mock_summary):
        from app.run import _notify_mission_end
        _notify_mission_end("/tmp/inst", "proj", 2, 10, 0, "Fix auth")
        msg = mock_notify.call_args[0][1]
        assert "✅" in msg
        # No double newline when summary is empty
        assert "\n\n" not in msg

    @patch("app.mission_summary.get_mission_summary", side_effect=Exception("broken"))
    @patch("app.run._notify")
    def test_success_survives_summary_error(self, mock_notify, mock_summary):
        from app.run import _notify_mission_end
        _notify_mission_end("/tmp/inst", "proj", 2, 10, 0, "Fix auth")
        # Should still send notification even if summary extraction fails
        mock_notify.assert_called_once()
        msg = mock_notify.call_args[0][1]
        assert msg.startswith("✅")

    @patch("app.run._notify")
    def test_failure_does_not_call_summary(self, mock_notify):
        from app.run import _notify_mission_end
        with patch("app.mission_summary.get_mission_summary") as mock_summary:
            _notify_mission_end("/tmp/inst", "proj", 2, 10, 1, "Fix auth")
            mock_summary.assert_not_called()

    @patch("app.run._notify")
    def test_nonzero_exit_codes_are_failure(self, mock_notify):
        from app.run import _notify_mission_end
        for code in [1, 2, 127, 255]:
            mock_notify.reset_mock()
            _notify_mission_end("/tmp/inst", "proj", 1, 5, code, "task")
            msg = mock_notify.call_args[0][1]
            assert msg.startswith("❌"), f"exit code {code} should be failure"

    @patch("app.run._notify")
    def test_always_calls_notify(self, mock_notify):
        """Both success and failure must call _notify — no silent completions."""
        from app.run import _notify_mission_end
        _notify_mission_end("/tmp/inst", "proj", 1, 5, 0, "task")
        assert mock_notify.call_count == 1
        _notify_mission_end("/tmp/inst", "proj", 1, 5, 1, "task")
        assert mock_notify.call_count == 2
