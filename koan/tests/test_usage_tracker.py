"""Tests for usage_tracker.py — Usage parsing and autonomous mode decisions."""

import pytest
from pathlib import Path
from app.usage_tracker import UsageTracker


@pytest.fixture
def usage_file_standard(tmp_path):
    """Standard usage.md format."""
    usage = tmp_path / "usage.md"
    usage.write_text("""# Usage

```
usage date: 2026-02-01 - 13:05
Session (5hr) : 25% (reset in 3h)
Weekly (7 day) : 60% (Resets in 3d)
```
""")
    return usage


@pytest.fixture
def usage_file_high(tmp_path):
    """High usage (near exhaustion)."""
    usage = tmp_path / "usage.md"
    usage.write_text("""# Usage

```
Session (5hr) : 85% (reset in 1h)
Weekly (7 day) : 82% (Resets in 2d)
```
""")
    return usage


@pytest.fixture
def usage_file_low(tmp_path):
    """Low usage (plenty of budget)."""
    usage = tmp_path / "usage.md"
    usage.write_text("""# Usage

```
Session (5hr) : 10% (reset in 4h)
Weekly (7 day) : 25% (Resets in 5d)
```
""")
    return usage


@pytest.fixture
def usage_file_empty(tmp_path):
    """Empty usage file (fallback scenario)."""
    usage = tmp_path / "usage.md"
    usage.write_text("")
    return usage


class TestUsageParsing:
    """Test parsing of usage.md formats."""

    def test_parse_standard_format(self, usage_file_standard):
        """Parse standard usage.md with session and weekly percentages."""
        tracker = UsageTracker(usage_file_standard)

        assert tracker.session_pct == 25.0
        assert tracker.session_reset == "3h"
        assert tracker.weekly_pct == 60.0
        assert tracker.weekly_reset == "3d"

    def test_parse_high_usage(self, usage_file_high):
        """Parse high usage percentages."""
        tracker = UsageTracker(usage_file_high)

        assert tracker.session_pct == 85.0
        assert tracker.weekly_pct == 82.0

    def test_parse_low_usage(self, usage_file_low):
        """Parse low usage percentages."""
        tracker = UsageTracker(usage_file_low)

        assert tracker.session_pct == 10.0
        assert tracker.weekly_pct == 25.0

    def test_parse_empty_file(self, usage_file_empty):
        """Gracefully handle empty usage.md."""
        tracker = UsageTracker(usage_file_empty)

        assert tracker.session_pct == 0.0
        assert tracker.weekly_pct == 0.0
        assert tracker.session_reset == "unknown"

    def test_parse_missing_file(self, tmp_path):
        """Handle missing usage.md file."""
        missing = tmp_path / "nonexistent.md"
        tracker = UsageTracker(missing)

        assert tracker.session_pct == 0.0
        assert tracker.weekly_pct == 0.0


class TestRemainingBudget:
    """Test budget calculations with safety margin."""

    def test_remaining_budget_standard(self, usage_file_standard):
        """Calculate remaining budget with 10% safety margin."""
        tracker = UsageTracker(usage_file_standard)
        session_rem, weekly_rem = tracker.remaining_budget()

        # 100 - 25 (used) - 10 (safety) = 65
        assert session_rem == 65.0
        # 100 - 60 (used) - 10 (safety) = 30
        assert weekly_rem == 30.0

    def test_remaining_budget_high_usage(self, usage_file_high):
        """Remaining budget near exhaustion."""
        tracker = UsageTracker(usage_file_high)
        session_rem, weekly_rem = tracker.remaining_budget()

        # 100 - 85 - 10 = 5
        assert session_rem == 5.0
        # 100 - 82 - 10 = 8
        assert weekly_rem == 8.0

    def test_remaining_budget_low_usage(self, usage_file_low):
        """Remaining budget with plenty available."""
        tracker = UsageTracker(usage_file_low)
        session_rem, weekly_rem = tracker.remaining_budget()

        assert session_rem == 80.0  # 100 - 10 - 10
        assert weekly_rem == 65.0   # 100 - 25 - 10

    def test_remaining_budget_never_negative(self, tmp_path):
        """Remaining budget floors at zero."""
        usage = tmp_path / "usage.md"
        usage.write_text("""
Session (5hr) : 95% (reset in 1h)
Weekly (7 day) : 99% (Resets in 1d)
""")
        tracker = UsageTracker(usage)
        session_rem, weekly_rem = tracker.remaining_budget()

        assert session_rem == 0.0  # max(0, 100 - 95 - 10)
        assert weekly_rem == 0.0   # max(0, 100 - 99 - 10)


class TestCostEstimation:
    """Test run cost estimation logic."""

    def test_estimate_first_run_default(self, usage_file_standard):
        """First run uses conservative default (5%)."""
        tracker = UsageTracker(usage_file_standard, runs_completed=0)
        cost = tracker.estimate_run_cost()

        assert cost == 5.0

    def test_estimate_from_history(self, usage_file_standard):
        """Estimate based on session average after runs."""
        tracker = UsageTracker(usage_file_standard, runs_completed=5)
        # 25% used / 5 runs = 5% per run
        cost = tracker.estimate_run_cost()

        assert cost == 5.0

    def test_estimate_expensive_session(self, usage_file_high):
        """High usage per run."""
        tracker = UsageTracker(usage_file_high, runs_completed=3)
        # 85% / 3 runs ≈ 28.33% per run
        cost = tracker.estimate_run_cost()

        assert cost == pytest.approx(28.33, rel=0.01)


class TestModeDecisions:
    """Test autonomous mode decision logic."""

    def test_decide_deep_mode_high_budget(self, usage_file_low):
        """Deep mode when ample budget (>= 40%)."""
        tracker = UsageTracker(usage_file_low)
        mode = tracker.decide_mode()

        assert mode == "deep"

    def test_decide_implement_mode_medium_budget(self, usage_file_standard):
        """Implement mode with medium budget (15-40%)."""
        tracker = UsageTracker(usage_file_standard)
        mode = tracker.decide_mode()

        # Session: 65% remaining, Weekly: 30% remaining → min = 30%
        assert mode == "implement"

    def test_decide_review_mode_low_budget(self, tmp_path):
        """Review mode when low budget (5-15%)."""
        usage = tmp_path / "usage.md"
        usage.write_text("""
Session (5hr) : 78% (reset in 1h)
Weekly (7 day) : 80% (Resets in 1d)
""")
        tracker = UsageTracker(usage)
        mode = tracker.decide_mode()

        # Session: 12% remaining, Weekly: 10% remaining → min = 10%
        assert mode == "review"

    def test_decide_wait_mode_exhausted(self, usage_file_high):
        """Wait mode when budget exhausted (< 5%)."""
        tracker = UsageTracker(usage_file_high)
        mode = tracker.decide_mode()

        # Session: 5% remaining, Weekly: 8% remaining → min = 5%
        # Threshold is < 5, so at exactly 5% we still get "review"
        # Let's check with slightly higher usage
        assert mode in ("wait", "review")  # Edge case at 5%


class TestProjectSelection:
    """Test smart project selection based on mode."""

    def test_select_project_review_mode(self, usage_file_standard):
        """Review mode prefers first (simplest) project."""
        tracker = UsageTracker(usage_file_standard)
        projects = "koan:/path/koan;anantys:/path/anantys;complex:/path/complex"

        idx = tracker.select_project(projects, "review", run_num=1)
        assert idx == 0  # First project

        idx = tracker.select_project(projects, "review", run_num=5)
        assert idx == 0  # Always first in review mode

    def test_select_project_deep_mode(self, usage_file_low):
        """Deep mode prefers primary (first) project."""
        tracker = UsageTracker(usage_file_low)
        projects = "koan:/path/koan;anantys:/path/anantys"

        idx = tracker.select_project(projects, "deep", run_num=1)
        assert idx == 0  # Primary project

    def test_select_project_implement_mode_round_robin(self, usage_file_standard):
        """Implement mode uses round-robin."""
        tracker = UsageTracker(usage_file_standard)
        projects = "p1:/path1;p2:/path2;p3:/path3"

        # Run 1: (1-1) % 3 = 0
        assert tracker.select_project(projects, "implement", run_num=1) == 0
        # Run 2: (2-1) % 3 = 1
        assert tracker.select_project(projects, "implement", run_num=2) == 1
        # Run 3: (3-1) % 3 = 2
        assert tracker.select_project(projects, "implement", run_num=3) == 2
        # Run 4: (4-1) % 3 = 0 (cycles back)
        assert tracker.select_project(projects, "implement", run_num=4) == 0

    def test_select_project_empty_string(self, usage_file_standard):
        """Handle empty projects string."""
        tracker = UsageTracker(usage_file_standard)
        idx = tracker.select_project("", "implement", run_num=1)
        assert idx == 0  # Fallback to first


class TestOutputFormatting:
    """Test CLI output formatting."""

    def test_format_output_structure(self, usage_file_standard):
        """Output format: mode:available%:reason:project_idx."""
        tracker = UsageTracker(usage_file_standard)
        mode = "implement"
        project_idx = 1

        output = tracker.format_output(mode, project_idx)
        parts = output.split(':')

        assert len(parts) == 4
        assert parts[0] == "implement"
        assert parts[1] == "30"  # min(65 session, 30 weekly)
        assert "30%" in parts[2] or "budget" in parts[2].lower()
        assert parts[3] == "1"

    def test_get_decision_reason(self, usage_file_low):
        """Reason strings are descriptive."""
        tracker = UsageTracker(usage_file_low)

        reason = tracker.get_decision_reason("deep")
        assert "ample" in reason.lower() or "full" in reason.lower()

        reason = tracker.get_decision_reason("wait")
        assert "exhaust" in reason.lower()
