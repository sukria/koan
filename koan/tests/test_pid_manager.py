"""Tests for pid_manager — exclusive PID file enforcement."""

import fcntl
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.pid_manager import (
    _pidfile_path,
    _read_pid,
    _is_process_alive,
    acquire_pidfile,
    release_pidfile,
    acquire_pid,
    release_pid,
    check_pidfile,
    stop_processes,
    PROCESS_NAMES,
)


# ---------------------------------------------------------------------------
# _pidfile_path
# ---------------------------------------------------------------------------


class TestPidfilePath:
    def test_run_process(self, tmp_path):
        assert _pidfile_path(tmp_path, "run") == tmp_path / ".koan-pid-run"

    def test_awake_process(self, tmp_path):
        assert _pidfile_path(tmp_path, "awake") == tmp_path / ".koan-pid-awake"

    def test_custom_name(self, tmp_path):
        assert _pidfile_path(tmp_path, "foo") == tmp_path / ".koan-pid-foo"


# ---------------------------------------------------------------------------
# _read_pid
# ---------------------------------------------------------------------------


class TestReadPid:
    def test_reads_valid_pid(self, tmp_path):
        pidfile = tmp_path / "test.pid"
        pidfile.write_text("12345")
        assert _read_pid(pidfile) == 12345

    def test_reads_pid_with_whitespace(self, tmp_path):
        pidfile = tmp_path / "test.pid"
        pidfile.write_text("  12345  \n")
        assert _read_pid(pidfile) == 12345

    def test_returns_none_for_empty_file(self, tmp_path):
        pidfile = tmp_path / "test.pid"
        pidfile.write_text("")
        assert _read_pid(pidfile) is None

    def test_returns_none_for_invalid_content(self, tmp_path):
        pidfile = tmp_path / "test.pid"
        pidfile.write_text("not-a-pid")
        assert _read_pid(pidfile) is None

    def test_returns_none_for_missing_file(self, tmp_path):
        pidfile = tmp_path / "nonexistent.pid"
        assert _read_pid(pidfile) is None


# ---------------------------------------------------------------------------
# _is_process_alive
# ---------------------------------------------------------------------------


class TestIsProcessAlive:
    def test_current_process_is_alive(self):
        assert _is_process_alive(os.getpid()) is True

    def test_nonexistent_pid(self):
        # PID 99999999 is almost certainly not running
        assert _is_process_alive(99999999) is False

    def test_pid_zero_is_special(self):
        # PID 0 on macOS/Linux sends signal to process group
        # _is_process_alive should handle this gracefully
        result = _is_process_alive(0)
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# acquire_pidfile (flock-based, for Python processes)
# ---------------------------------------------------------------------------


class TestAcquirePidfile:
    def test_creates_pidfile_with_current_pid(self, tmp_path):
        fh = acquire_pidfile(tmp_path, "awake")
        pidfile = tmp_path / ".koan-pid-awake"
        assert pidfile.exists()
        assert pidfile.read_text().strip() == str(os.getpid())
        release_pidfile(fh, tmp_path, "awake")

    def test_returns_open_file_handle(self, tmp_path):
        fh = acquire_pidfile(tmp_path, "awake")
        assert not fh.closed
        release_pidfile(fh, tmp_path, "awake")

    def test_flock_is_exclusive(self, tmp_path):
        """Second acquire should fail when first holds the lock."""
        fh = acquire_pidfile(tmp_path, "awake")

        with pytest.raises(SystemExit) as exc_info:
            acquire_pidfile(tmp_path, "awake")

        assert exc_info.value.code == 1
        release_pidfile(fh, tmp_path, "awake")

    def test_error_message_includes_pid(self, tmp_path, capsys):
        fh = acquire_pidfile(tmp_path, "awake")

        with pytest.raises(SystemExit):
            acquire_pidfile(tmp_path, "awake")

        captured = capsys.readouterr()
        assert "awake" in captured.err
        assert "already running" in captured.err
        assert str(os.getpid()) in captured.err
        release_pidfile(fh, tmp_path, "awake")

    def test_overwrites_stale_pidfile(self, tmp_path):
        """If no lock is held, acquire should succeed even if file exists."""
        pidfile = tmp_path / ".koan-pid-awake"
        pidfile.write_text("99999")  # stale PID

        fh = acquire_pidfile(tmp_path, "awake")
        assert pidfile.read_text().strip() == str(os.getpid())
        release_pidfile(fh, tmp_path, "awake")

    def test_different_process_names_dont_conflict(self, tmp_path):
        fh_run = acquire_pidfile(tmp_path, "run")
        fh_awake = acquire_pidfile(tmp_path, "awake")

        assert (tmp_path / ".koan-pid-run").exists()
        assert (tmp_path / ".koan-pid-awake").exists()

        release_pidfile(fh_run, tmp_path, "run")
        release_pidfile(fh_awake, tmp_path, "awake")


# ---------------------------------------------------------------------------
# release_pidfile
# ---------------------------------------------------------------------------


class TestReleasePidfile:
    def test_removes_pidfile(self, tmp_path):
        fh = acquire_pidfile(tmp_path, "awake")
        release_pidfile(fh, tmp_path, "awake")
        assert not (tmp_path / ".koan-pid-awake").exists()

    def test_closes_file_handle(self, tmp_path):
        fh = acquire_pidfile(tmp_path, "awake")
        release_pidfile(fh, tmp_path, "awake")
        assert fh.closed

    def test_lock_released_after_release(self, tmp_path):
        """After release, a new acquire should succeed."""
        fh1 = acquire_pidfile(tmp_path, "awake")
        release_pidfile(fh1, tmp_path, "awake")

        fh2 = acquire_pidfile(tmp_path, "awake")
        assert not fh2.closed
        release_pidfile(fh2, tmp_path, "awake")

    def test_tolerates_already_removed_file(self, tmp_path):
        fh = acquire_pidfile(tmp_path, "awake")
        (tmp_path / ".koan-pid-awake").unlink()
        # Should not raise
        release_pidfile(fh, tmp_path, "awake")


# ---------------------------------------------------------------------------
# acquire_pid (PID-liveness-based, for bash processes)
# ---------------------------------------------------------------------------


class TestAcquirePid:
    def test_creates_pidfile_with_given_pid(self, tmp_path):
        acquire_pid(tmp_path, "run", 12345)
        pidfile = tmp_path / ".koan-pid-run"
        assert pidfile.exists()
        assert pidfile.read_text() == "12345"

    def test_overwrites_stale_pid(self, tmp_path):
        """If existing PID is not alive, overwrite."""
        pidfile = tmp_path / ".koan-pid-run"
        pidfile.write_text("99999999")  # dead PID
        acquire_pid(tmp_path, "run", 12345)
        assert pidfile.read_text() == "12345"

    def test_aborts_if_pid_is_alive(self, tmp_path):
        """If existing PID is alive and different, abort."""
        current_pid = os.getpid()
        pidfile = tmp_path / ".koan-pid-run"
        pidfile.write_text(str(current_pid))

        with pytest.raises(SystemExit) as exc_info:
            acquire_pid(tmp_path, "run", 99999)

        assert exc_info.value.code == 1

    def test_error_message_includes_running_pid(self, tmp_path, capsys):
        current_pid = os.getpid()
        pidfile = tmp_path / ".koan-pid-run"
        pidfile.write_text(str(current_pid))

        with pytest.raises(SystemExit):
            acquire_pid(tmp_path, "run", 99999)

        captured = capsys.readouterr()
        assert str(current_pid) in captured.err
        assert "already running" in captured.err

    def test_same_pid_allowed(self, tmp_path):
        """Same PID writing again (re-exec) should succeed."""
        current_pid = os.getpid()
        pidfile = tmp_path / ".koan-pid-run"
        pidfile.write_text(str(current_pid))

        # Should not raise
        acquire_pid(tmp_path, "run", current_pid)
        assert pidfile.read_text() == str(current_pid)

    def test_no_existing_file(self, tmp_path):
        """First acquisition with no existing file."""
        acquire_pid(tmp_path, "run", 42)
        assert (tmp_path / ".koan-pid-run").read_text() == "42"


# ---------------------------------------------------------------------------
# release_pid
# ---------------------------------------------------------------------------


class TestReleasePid:
    def test_removes_pidfile(self, tmp_path):
        pidfile = tmp_path / ".koan-pid-run"
        pidfile.write_text("12345")
        release_pid(tmp_path, "run")
        assert not pidfile.exists()

    def test_tolerates_missing_file(self, tmp_path):
        # Should not raise
        release_pid(tmp_path, "run")


# ---------------------------------------------------------------------------
# check_pidfile
# ---------------------------------------------------------------------------


class TestCheckPidfile:
    def test_returns_none_when_no_file(self, tmp_path):
        assert check_pidfile(tmp_path, "awake") is None

    def test_returns_pid_when_flock_held(self, tmp_path):
        """If a Python process holds the flock, return its PID."""
        fh = acquire_pidfile(tmp_path, "awake")
        pid = check_pidfile(tmp_path, "awake")
        assert pid == os.getpid()
        release_pidfile(fh, tmp_path, "awake")

    def test_returns_none_when_flock_released(self, tmp_path):
        """After release, check should return None."""
        fh = acquire_pidfile(tmp_path, "awake")
        release_pidfile(fh, tmp_path, "awake")
        assert check_pidfile(tmp_path, "awake") is None

    def test_returns_pid_for_alive_bash_process(self, tmp_path):
        """If PID file exists with alive PID (no flock), return PID."""
        pidfile = tmp_path / ".koan-pid-run"
        pidfile.write_text(str(os.getpid()))
        pid = check_pidfile(tmp_path, "run")
        assert pid == os.getpid()

    def test_returns_none_for_dead_bash_process(self, tmp_path):
        """If PID file exists but PID is dead, return None."""
        pidfile = tmp_path / ".koan-pid-run"
        pidfile.write_text("99999999")
        assert check_pidfile(tmp_path, "run") is None

    def test_returns_none_for_empty_file(self, tmp_path):
        pidfile = tmp_path / ".koan-pid-run"
        pidfile.write_text("")
        assert check_pidfile(tmp_path, "run") is None

    def test_returns_none_for_corrupt_file(self, tmp_path):
        pidfile = tmp_path / ".koan-pid-run"
        pidfile.write_text("not-a-number")
        assert check_pidfile(tmp_path, "run") is None


# ---------------------------------------------------------------------------
# CLI interface (__main__)
# ---------------------------------------------------------------------------


class TestCLI:
    def _run_cli(self, *args, cwd=None):
        """Run pid_manager as a module and return the result."""
        cmd = [sys.executable, "-m", "app.pid_manager"] + list(args)
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).parent.parent)
        return subprocess.run(
            cmd, capture_output=True, text=True, env=env, cwd=cwd
        )

    def test_acquire_pid_cli(self, tmp_path):
        result = self._run_cli("acquire-pid", "run", str(tmp_path), "12345")
        assert result.returncode == 0
        pidfile = tmp_path / ".koan-pid-run"
        assert pidfile.read_text() == "12345"

    def test_acquire_pid_cli_blocks_duplicate(self, tmp_path):
        """Second acquire with alive PID should fail."""
        pidfile = tmp_path / ".koan-pid-run"
        pidfile.write_text(str(os.getpid()))  # alive PID

        result = self._run_cli("acquire-pid", "run", str(tmp_path), "99999")
        assert result.returncode == 1
        assert "already running" in result.stderr

    def test_release_pid_cli(self, tmp_path):
        pidfile = tmp_path / ".koan-pid-run"
        pidfile.write_text("12345")

        result = self._run_cli("release-pid", "run", str(tmp_path))
        assert result.returncode == 0
        assert not pidfile.exists()

    def test_check_cli_not_running(self, tmp_path):
        result = self._run_cli("check", "run", str(tmp_path))
        assert result.returncode == 0
        assert "not_running" in result.stdout

    def test_check_cli_running(self, tmp_path):
        pidfile = tmp_path / ".koan-pid-run"
        pidfile.write_text(str(os.getpid()))

        result = self._run_cli("check", "run", str(tmp_path))
        assert result.returncode == 0
        assert f"running:{os.getpid()}" in result.stdout

    def test_missing_args(self):
        result = self._run_cli("acquire-pid")
        assert result.returncode == 2

    def test_unknown_action(self, tmp_path):
        result = self._run_cli("unknown", "run", str(tmp_path))
        assert result.returncode == 2

    def test_acquire_pid_missing_pid_arg(self, tmp_path):
        result = self._run_cli("acquire-pid", "run", str(tmp_path))
        assert result.returncode == 2
        assert "requires a PID" in result.stderr


# ---------------------------------------------------------------------------
# Integration: awake.py startup with PID lock
# ---------------------------------------------------------------------------


class TestAwakeIntegration:
    """Test that awake.py's main() acquires the PID lock."""

    @patch("app.awake.check_config")
    @patch("app.awake.compact_telegram_history", return_value=0)
    @patch("app.awake.write_heartbeat")
    @patch("app.awake._get_registry")
    @patch("app.awake.get_updates", side_effect=KeyboardInterrupt)
    @patch("app.awake.send_telegram")
    @patch("app.awake.log")
    @patch("app.awake.KOAN_ROOT")
    @patch("app.awake.BOT_TOKEN", "test-token-12345678")
    @patch("app.awake.CHAT_ID", "123")
    @patch("app.awake.SOUL", "test soul")
    @patch("app.awake.SUMMARY", "test summary")
    def test_main_acquires_pidfile(
        self,
        mock_root,
        mock_log,
        mock_send,
        mock_updates,
        mock_registry,
        mock_heartbeat,
        mock_compact,
        mock_config,
        tmp_path,
    ):
        from app.awake import main

        mock_root.__truediv__ = lambda self, x: tmp_path / x
        mock_root.unlink = MagicMock()
        # Set up the Path-like behavior
        type(mock_root).__truediv__ = lambda self, other: tmp_path / other

        registry_mock = MagicMock()
        registry_mock.list_by_scope.return_value = []
        registry_mock.__len__ = lambda self: 0
        mock_registry.return_value = registry_mock

        # Patch at the module level so lazy imports inside main() get mocked
        with patch("app.pid_manager.acquire_pidfile") as mock_acquire, \
             patch("app.pid_manager.release_pidfile") as mock_release:
            mock_fh = MagicMock()
            mock_acquire.return_value = mock_fh

            with pytest.raises(SystemExit):
                main()

            mock_acquire.assert_called_once()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_acquire_after_crash_no_cleanup(self, tmp_path):
        """Simulate crash: file exists with old PID, no flock held."""
        pidfile = tmp_path / ".koan-pid-awake"
        pidfile.write_text("99999999")

        fh = acquire_pidfile(tmp_path, "awake")
        assert pidfile.read_text().strip() == str(os.getpid())
        release_pidfile(fh, tmp_path, "awake")

    def test_concurrent_run_and_awake(self, tmp_path):
        """run and awake PID files are independent."""
        acquire_pid(tmp_path, "run", 111)
        fh = acquire_pidfile(tmp_path, "awake")

        assert (tmp_path / ".koan-pid-run").read_text() == "111"
        assert (tmp_path / ".koan-pid-awake").read_text().strip() == str(os.getpid())

        release_pid(tmp_path, "run")
        release_pidfile(fh, tmp_path, "awake")

    def test_release_idempotent(self, tmp_path):
        """Multiple releases should not raise."""
        fh = acquire_pidfile(tmp_path, "awake")
        release_pidfile(fh, tmp_path, "awake")
        # Second release with closed fh — should not raise
        release_pidfile(fh, tmp_path, "awake")


# ---------------------------------------------------------------------------
# stop_processes
# ---------------------------------------------------------------------------


class TestStopProcesses:
    def test_no_processes_running(self, tmp_path):
        """When no processes are running, all results are not_running."""
        results = stop_processes(tmp_path)
        assert results["run"] == "not_running"
        assert results["awake"] == "not_running"

    def test_creates_stop_file(self, tmp_path):
        """stop_processes always creates .koan-stop signal file."""
        stop_processes(tmp_path)
        assert (tmp_path / ".koan-stop").exists()
        assert (tmp_path / ".koan-stop").read_text() == "STOP"

    def test_stops_running_subprocess(self, tmp_path):
        """SIGTERM a real subprocess and verify it exits."""
        # Start a sleep process to simulate a running koan process
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"]
        )
        pidfile = tmp_path / ".koan-pid-run"
        pidfile.write_text(str(proc.pid))

        results = stop_processes(tmp_path, timeout=3.0)
        assert results["run"] in ("stopped", "force_killed")
        assert results["awake"] == "not_running"
        # PID file should be cleaned up
        assert not pidfile.exists()
        # Reap child to verify it's gone
        proc.wait(timeout=5)
        assert proc.returncode is not None

    def test_force_kills_stubborn_process(self, tmp_path):
        """If SIGTERM doesn't work within timeout, SIGKILL is sent."""
        # Start a process that ignores SIGTERM and signals readiness via file
        ready_file = tmp_path / ".ready"
        proc = subprocess.Popen(
            [sys.executable, "-c",
             f"import signal, time, pathlib; "
             f"signal.signal(signal.SIGTERM, signal.SIG_IGN); "
             f"pathlib.Path('{ready_file}').write_text('ok'); "
             f"time.sleep(60)"]
        )
        pidfile = tmp_path / ".koan-pid-awake"
        pidfile.write_text(str(proc.pid))

        # Wait for child to install SIGTERM handler
        deadline = time.monotonic() + 5
        while not ready_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)

        results = stop_processes(tmp_path, timeout=1.0)
        assert results["awake"] == "force_killed"
        assert not pidfile.exists()
        # Reap the child to verify it's gone
        proc.wait(timeout=5)
        assert proc.returncode is not None

    def test_handles_already_dead_pid(self, tmp_path):
        """If PID in file is already dead, report not_running."""
        pidfile = tmp_path / ".koan-pid-run"
        pidfile.write_text("99999999")  # dead PID
        results = stop_processes(tmp_path)
        assert results["run"] == "not_running"

    def test_stops_both_processes(self, tmp_path):
        """Can stop both run and awake simultaneously."""
        procs = []
        for name in PROCESS_NAMES:
            proc = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"]
            )
            pidfile = tmp_path / f".koan-pid-{name}"
            pidfile.write_text(str(proc.pid))
            procs.append(proc)

        results = stop_processes(tmp_path, timeout=3.0)
        assert results["run"] in ("stopped", "force_killed")
        assert results["awake"] in ("stopped", "force_killed")

        for proc in procs:
            proc.wait(timeout=5)

    def test_cleans_up_pid_files_after_stop(self, tmp_path):
        """PID files are removed after stopping."""
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"]
        )
        pidfile = tmp_path / ".koan-pid-run"
        pidfile.write_text(str(proc.pid))

        stop_processes(tmp_path, timeout=3.0)
        assert not (tmp_path / ".koan-pid-run").exists()
        proc.wait(timeout=5)  # Clean up child

    def test_process_names_constant(self):
        """PROCESS_NAMES includes both expected processes."""
        assert "run" in PROCESS_NAMES
        assert "awake" in PROCESS_NAMES


# ---------------------------------------------------------------------------
# CLI: stop-all and status-all
# ---------------------------------------------------------------------------


class TestCLIStopAll:
    def _run_cli(self, *args):
        cmd = [sys.executable, "-m", "app.pid_manager"] + list(args)
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).parent.parent)
        return subprocess.run(cmd, capture_output=True, text=True, env=env)

    def test_stop_all_no_processes(self, tmp_path):
        result = self._run_cli("stop-all", str(tmp_path))
        assert result.returncode == 0
        assert "not running" in result.stdout
        assert "No processes were running." in result.stdout

    def test_stop_all_with_process(self, tmp_path):
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"]
        )
        pidfile = tmp_path / ".koan-pid-run"
        pidfile.write_text(str(proc.pid))

        result = self._run_cli("stop-all", str(tmp_path))
        assert result.returncode == 0
        # Process may be stopped or force-killed depending on timing
        assert "run: stopped" in result.stdout or "run: force killed" in result.stdout
        # Reap to clean up
        proc.wait(timeout=5)

    def test_status_all_no_processes(self, tmp_path):
        result = self._run_cli("status-all", str(tmp_path))
        assert result.returncode == 0
        assert "run: not running" in result.stdout
        assert "awake: not running" in result.stdout

    def test_status_all_with_running_process(self, tmp_path):
        pidfile = tmp_path / ".koan-pid-run"
        pidfile.write_text(str(os.getpid()))

        result = self._run_cli("status-all", str(tmp_path))
        assert result.returncode == 0
        assert f"run: running (PID {os.getpid()})" in result.stdout
        assert "awake: not running" in result.stdout
