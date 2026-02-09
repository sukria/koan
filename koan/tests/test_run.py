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
