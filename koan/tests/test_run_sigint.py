"""Tests for run.sh graceful CTRL-C handling (double-tap pattern).

Tests the on_sigint handler, wait_for_claude_task function, and the interaction
between signal handling and background process management.
"""

import subprocess

# Plain-text color vars (no TTY)
_COLORS_PLAIN = """
_C_RESET='' _C_BOLD='' _C_DIM=''
_C_RED='' _C_GREEN='' _C_YELLOW=''
_C_BLUE='' _C_MAGENTA='' _C_CYAN='' _C_WHITE=''
"""

_LOG_FUNCTION = """
log() {
  local cat="$1"; shift
  local color
  case "$cat" in
    koan)    color="${_C_CYAN}" ;;
    error)   color="${_C_BOLD}${_C_RED}" ;;
    *)       color="${_C_WHITE}" ;;
  esac
  echo -e "${color}[${cat}]${_C_RESET} $*"
}
"""

# The on_sigint handler from run.sh
_SIGINT_HANDLER = """
TASK_RUNNING=0
CTRL_C_FIRST_TIME=0
CLAUDE_PID=""
CTRL_C_TIMEOUT=10
count=0

cleanup() {
  # Kill child process if still running, then wait for it
  if [ -n "$CLAUDE_PID" ]; then
    kill "$CLAUDE_PID" 2>/dev/null
    wait "$CLAUDE_PID" 2>/dev/null
  fi
  log koan "Shutdown."
  echo "CLEANUP_CALLED"
  exit 0
}

on_sigint() {
  if [ "$TASK_RUNNING" -eq 0 ]; then
    cleanup
  fi

  local now
  now=$(date +%s)

  if [ "$CTRL_C_FIRST_TIME" -gt 0 ]; then
    local elapsed=$((now - CTRL_C_FIRST_TIME))
    if [ "$elapsed" -le "$CTRL_C_TIMEOUT" ]; then
      echo ""
      log koan "Confirmed. Aborting task..."
      CTRL_C_FIRST_TIME=0
      TASK_RUNNING=0
      [ -n "$CLAUDE_PID" ] && kill "$CLAUDE_PID" 2>/dev/null
      cleanup
    fi
  fi

  CTRL_C_FIRST_TIME=$now
  echo ""
  log koan "A task is running. Press CTRL-C again within ${CTRL_C_TIMEOUT}s to abort."
}

trap on_sigint INT
trap cleanup TERM
"""

_WAIT_FUNCTION = """
wait_for_claude_task() {
  TASK_RUNNING=1
  CTRL_C_FIRST_TIME=0

  while kill -0 "$CLAUDE_PID" 2>/dev/null; do
    wait "$CLAUDE_PID" 2>/dev/null || true
  done

  wait "$CLAUDE_PID" 2>/dev/null && CLAUDE_EXIT=0 || CLAUDE_EXIT=$?
  CLAUDE_PID=""
  TASK_RUNNING=0
  CTRL_C_FIRST_TIME=0
}
"""


def _build_script(body):
    """Build a test bash script with all signal handling components."""
    return f"""#!/bin/bash
{_COLORS_PLAIN}
{_LOG_FUNCTION}
{_SIGINT_HANDLER}
{_WAIT_FUNCTION}
{body}
"""


def _run_script(body, timeout=10):
    """Build and run a test script, return CompletedProcess."""
    script = _build_script(body)
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class TestOnSigintNoTask:
    """When no task is running, CTRL-C should trigger immediate cleanup."""

    def test_sigint_without_task_calls_cleanup(self):
        """SIGINT when TASK_RUNNING=0 exits via cleanup."""
        result = _run_script("""
TASK_RUNNING=0
# Send SIGINT to self after a short delay
(sleep 0.2; kill -INT $$ 2>/dev/null) &
# Use a wait loop instead of sleep (sleep would be killed by SIGINT)
for i in $(seq 1 20); do sleep 0.1; done
echo "SHOULD_NOT_REACH"
""")
        assert "CLEANUP_CALLED" in result.stdout
        assert "SHOULD_NOT_REACH" not in result.stdout


class TestOnSigintWithTask:
    """When a task is running, first CTRL-C should warn, not kill."""

    def test_first_sigint_shows_warning(self):
        """First SIGINT during task shows warning message."""
        result = _run_script("""
TASK_RUNNING=1
# Send one SIGINT to self
(sleep 0.3; kill -INT $$ 2>/dev/null) &
# Sleep in a loop so we survive the interrupt
for i in $(seq 1 10); do sleep 0.2; done
echo "STILL_ALIVE"
""")
        assert "A task is running" in result.stdout
        assert "STILL_ALIVE" in result.stdout
        assert "CLEANUP_CALLED" not in result.stdout

    def test_double_sigint_within_timeout_calls_cleanup(self):
        """Two SIGINTs within timeout trigger cleanup."""
        result = _run_script("""
TASK_RUNNING=1
# Send two SIGINTs in quick succession via background subshell
# Use generous delays to avoid flakiness under load
(sleep 0.5; kill -INT $$ 2>/dev/null; sleep 1; kill -INT $$ 2>/dev/null) &
# Sleep in a loop so we survive between signals
for i in $(seq 1 40); do sleep 0.2; done
echo "SHOULD_NOT_REACH"
""")
        assert "A task is running" in result.stdout
        assert "Confirmed. Aborting task..." in result.stdout
        assert "CLEANUP_CALLED" in result.stdout

    def test_sigint_after_timeout_resets(self):
        """SIGINT after timeout expires is treated as a new first CTRL-C."""
        # Use 1s timeout with 3s gap to ensure date +%s difference is > 1
        result = _run_script("""
CTRL_C_TIMEOUT=1
TASK_RUNNING=1
# First SIGINT, then wait > timeout (3s ensures date +%s reports > 1), then second SIGINT
(sleep 0.3; kill -INT $$ 2>/dev/null; sleep 3; kill -INT $$ 2>/dev/null) &
# Sleep in a loop so we survive all signals (need > 3.3s total)
for i in $(seq 1 40); do sleep 0.2; done
echo "STILL_ALIVE_AFTER_RESET"
""", timeout=15)
        # Both SIGINTs should show warnings (second is a new first)
        assert result.stdout.count("A task is running") == 2
        assert "CLEANUP_CALLED" not in result.stdout
        assert "STILL_ALIVE_AFTER_RESET" in result.stdout


class TestWaitForClaudeTask:
    """Tests for the wait_for_claude_task function."""

    def test_wait_captures_exit_code_zero(self):
        """Successful child process returns exit code 0."""
        result = _run_script("""
sleep 0.5 &
CLAUDE_PID=$!
wait_for_claude_task
echo "EXIT=$CLAUDE_EXIT"
echo "TASK_RUNNING=$TASK_RUNNING"
""")
        assert "EXIT=0" in result.stdout
        assert "TASK_RUNNING=0" in result.stdout

    def test_wait_captures_nonzero_exit_code(self):
        """Failed child process returns its exit code."""
        result = _run_script("""
bash -c "exit 42" &
CLAUDE_PID=$!
wait_for_claude_task
echo "EXIT=$CLAUDE_EXIT"
""")
        assert "EXIT=42" in result.stdout

    def test_wait_sets_task_running(self):
        """wait_for_claude_task sets TASK_RUNNING=1 during wait, 0 after."""
        result = _run_script("""
sleep 0.1 &
CLAUDE_PID=$!
wait_for_claude_task
echo "AFTER_TASK_RUNNING=$TASK_RUNNING"
echo "AFTER_CLAUDE_PID=${CLAUDE_PID:-empty}"
""")
        assert "AFTER_TASK_RUNNING=0" in result.stdout
        assert "AFTER_CLAUDE_PID=empty" in result.stdout

    def test_wait_survives_sigint_during_task(self):
        """SIGINT during wait_for_claude_task warns but doesn't kill the child."""
        result = _run_script("""
# Start a background task that ignores SIGINT (like our claude pattern)
(trap '' INT; sleep 2; echo "CHILD_DONE") &
CLAUDE_PID=$!
# Send SIGINT to parent after 0.3s
(sleep 0.3; kill -INT $$ 2>/dev/null) &
wait_for_claude_task
echo "EXIT=$CLAUDE_EXIT"
echo "PARENT_SURVIVED"
""")
        assert "A task is running" in result.stdout
        assert "CHILD_DONE" in result.stdout
        assert "PARENT_SURVIVED" in result.stdout
        assert "EXIT=0" in result.stdout

    def test_double_sigint_kills_child_during_wait(self):
        """Double SIGINT during wait_for_claude_task terminates the child."""
        result = _run_script("""
# Long-running child that ignores SIGINT (stdout redirected to avoid pipe hang)
(trap '' INT; sleep 30) > /dev/null 2>&1 &
CLAUDE_PID=$!
# Send two SIGINTs
(sleep 0.3; kill -INT $$ 2>/dev/null; sleep 0.5; kill -INT $$ 2>/dev/null) &
wait_for_claude_task
echo "SHOULD_NOT_REACH"
""")
        assert "A task is running" in result.stdout
        assert "Confirmed. Aborting task..." in result.stdout
        assert "CLEANUP_CALLED" in result.stdout


class TestChildSignalIsolation:
    """Tests that the (trap '' INT; exec cmd) pattern isolates the child."""

    def test_child_responds_to_sigterm(self):
        """Child that ignores SIGINT still responds to SIGTERM (kill default)."""
        result = _run_script("""
(trap '' INT; exec sleep 30) > /dev/null 2>&1 &
CHILD=$!
sleep 0.3
# SIGTERM (default signal for kill)
kill "$CHILD" 2>/dev/null
wait "$CHILD" 2>/dev/null
echo "CHILD_TERMINATED"
""")
        assert "CHILD_TERMINATED" in result.stdout


class TestTimeoutVariable:
    """Tests for the configurable CTRL_C_TIMEOUT."""

    def test_default_timeout_is_10(self):
        """Default CTRL_C_TIMEOUT should be 10 seconds."""
        result = _run_script("""
echo "TIMEOUT=$CTRL_C_TIMEOUT"
""")
        assert "TIMEOUT=10" in result.stdout

    def test_custom_timeout_in_warning_message(self):
        """Custom CTRL_C_TIMEOUT appears in the warning message."""
        result = _run_script("""
CTRL_C_TIMEOUT=5
TASK_RUNNING=1
(sleep 0.2; kill -INT $$ 2>/dev/null) &
for i in $(seq 1 10); do sleep 0.2; done
""")
        assert "within 5s to abort" in result.stdout


class TestCleanupKillsChild:
    """Tests that cleanup() properly kills the child process."""

    def test_cleanup_sends_kill_to_child(self):
        """cleanup() sends kill to CLAUDE_PID if set."""
        result = _run_script("""
# Use a short-lived sleep with stdout to /dev/null to avoid pipe hang
sleep 1 > /dev/null 2>&1 &
CLAUDE_PID=$!
cleanup
""")
        assert "CLEANUP_CALLED" in result.stdout
        assert "Shutdown" in result.stdout

    def test_cleanup_without_child(self):
        """cleanup() works fine when no child process is running."""
        result = _run_script("""
CLAUDE_PID=""
cleanup
""")
        assert "CLEANUP_CALLED" in result.stdout


class TestIntegration:
    """End-to-end integration tests."""

    def test_full_task_lifecycle_no_interrupt(self):
        """Task runs to completion without any interruption."""
        result = _run_script("""
# Simulate a short task
(trap '' INT; sleep 0.5; echo "TASK_OUTPUT") &
CLAUDE_PID=$!
wait_for_claude_task
echo "EXIT=$CLAUDE_EXIT"
echo "LIFECYCLE_COMPLETE"
""")
        assert "TASK_OUTPUT" in result.stdout
        assert "EXIT=0" in result.stdout
        assert "LIFECYCLE_COMPLETE" in result.stdout

    def test_full_task_lifecycle_single_interrupt(self):
        """Task survives a single CTRL-C and completes normally."""
        result = _run_script("""
# Simulate a task that takes 2s
(trap '' INT; sleep 2; echo "TASK_COMPLETED") &
CLAUDE_PID=$!
# Send one SIGINT during the task
(sleep 0.3; kill -INT $$ 2>/dev/null) &
wait_for_claude_task
echo "EXIT=$CLAUDE_EXIT"
echo "SURVIVED"
""")
        assert "A task is running" in result.stdout
        assert "TASK_COMPLETED" in result.stdout
        assert "SURVIVED" in result.stdout
        assert "CLEANUP_CALLED" not in result.stdout
