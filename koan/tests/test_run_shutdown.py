"""Tests for run.sh is_shutdown_requested bash function."""

import os
import subprocess


def _run_shutdown_check(tmp_dir, start_time, pythonpath):
    """Run the is_shutdown_requested bash function and return exit code."""
    script = f"""
PYTHON="python3"
KOAN_ROOT="{tmp_dir}"
PROCESS_START_TIME={start_time}

is_shutdown_requested() {{
  "$PYTHON" -c "
from app.shutdown_manager import is_shutdown_requested
import sys
sys.exit(0 if is_shutdown_requested('$KOAN_ROOT', $PROCESS_START_TIME) else 1)
" 2>/dev/null
}}

if is_shutdown_requested; then
  echo "SHUTDOWN"
  exit 0
else
  echo "NO_SHUTDOWN"
  exit 1
fi
"""
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": pythonpath},
    )
    return result


class TestRunShShutdownCheck:
    """Test the is_shutdown_requested function as used in run.sh."""

    def _get_pythonpath(self):
        """Get PYTHONPATH for koan app imports."""
        return os.path.join(os.path.dirname(__file__), "..")

    def test_no_shutdown_file(self, tmp_path):
        result = _run_shutdown_check(str(tmp_path), 1000, self._get_pythonpath())
        assert result.returncode == 1
        assert "NO_SHUTDOWN" in result.stdout

    def test_valid_shutdown(self, tmp_path):
        start_time = 1000
        (tmp_path / ".koan-shutdown").write_text("2000")
        result = _run_shutdown_check(str(tmp_path), start_time, self._get_pythonpath())
        assert result.returncode == 0
        assert "SHUTDOWN" in result.stdout

    def test_stale_shutdown_ignored(self, tmp_path):
        start_time = 2000
        (tmp_path / ".koan-shutdown").write_text("1000")
        result = _run_shutdown_check(str(tmp_path), start_time, self._get_pythonpath())
        assert result.returncode == 1
        assert "NO_SHUTDOWN" in result.stdout
