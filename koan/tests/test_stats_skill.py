"""Tests for the /stats core skill — session outcome statistics."""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.skills import SkillContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx(tmp_path, args=""):
    """Create a SkillContext for /stats."""
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir(exist_ok=True)
    return SkillContext(
        koan_root=tmp_path,
        instance_dir=instance_dir,
        command_name="stats",
        args=args,
    )


def _write_outcomes(instance_dir, outcomes):
    """Write session_outcomes.json."""
    path = instance_dir / "session_outcomes.json"
    path.write_text(json.dumps(outcomes))
    return path


def _make_outcome(project="koan", mode="implement", outcome="productive",
                  duration=10, summary="branch pushed", hours_ago=0):
    """Build a session outcome entry."""
    ts = datetime.now() - timedelta(hours=hours_ago)
    return {
        "timestamp": ts.isoformat(timespec="seconds"),
        "project": project,
        "mode": mode,
        "duration_minutes": duration,
        "outcome": outcome,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Tests: No data
# ---------------------------------------------------------------------------

class TestNoData:
    def test_no_outcomes_file(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        from skills.core.stats.handler import handle
        result = handle(ctx)
        assert "No session data" in result

    def test_empty_outcomes_file(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        _write_outcomes(ctx.instance_dir, [])
        from skills.core.stats.handler import handle
        result = handle(ctx)
        assert "No session data" in result

    def test_corrupt_json(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        (ctx.instance_dir / "session_outcomes.json").write_text("{bad json")
        from skills.core.stats.handler import handle
        result = handle(ctx)
        assert "No session data" in result


# ---------------------------------------------------------------------------
# Tests: Overview (no project filter)
# ---------------------------------------------------------------------------

class TestOverview:
    def test_single_project(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        outcomes = [
            _make_outcome(outcome="productive", hours_ago=3),
            _make_outcome(outcome="productive", hours_ago=2),
            _make_outcome(outcome="empty", hours_ago=1),
        ]
        _write_outcomes(ctx.instance_dir, outcomes)
        from skills.core.stats.handler import handle
        result = handle(ctx)
        assert "Session Stats" in result
        assert "3 sessions" in result
        assert "66%" in result  # 2/3
        assert "koan" in result

    def test_multi_project(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        outcomes = [
            _make_outcome(project="koan", outcome="productive"),
            _make_outcome(project="koan", outcome="productive"),
            _make_outcome(project="backend", outcome="empty"),
            _make_outcome(project="backend", outcome="productive"),
        ]
        _write_outcomes(ctx.instance_dir, outcomes)
        from skills.core.stats.handler import handle
        result = handle(ctx)
        assert "4 sessions" in result
        assert "koan" in result
        assert "backend" in result
        assert "/stats <project>" in result

    def test_staleness_warning_in_overview(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        outcomes = [
            _make_outcome(project="stale", outcome="productive", hours_ago=10),
        ] + [
            _make_outcome(project="stale", outcome="empty", hours_ago=i)
            for i in range(5)
        ]
        _write_outcomes(ctx.instance_dir, outcomes)
        from skills.core.stats.handler import handle
        result = handle(ctx)
        assert "!!!" in result  # 5 consecutive non-productive

    def test_staleness_mild_in_overview(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        outcomes = [
            _make_outcome(project="mild", outcome="productive", hours_ago=10),
        ] + [
            _make_outcome(project="mild", outcome="empty", hours_ago=i)
            for i in range(3)
        ]
        _write_outcomes(ctx.instance_dir, outcomes)
        from skills.core.stats.handler import handle
        result = handle(ctx)
        # Should have single ! but not !!!
        lines = result.split("\n")
        mild_line = [l for l in lines if "mild" in l][0]
        assert "!" in mild_line
        assert "!!!" not in mild_line

    def test_blocked_count(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        outcomes = [
            _make_outcome(outcome="productive"),
            _make_outcome(outcome="blocked"),
            _make_outcome(outcome="empty"),
        ]
        _write_outcomes(ctx.instance_dir, outcomes)
        from skills.core.stats.handler import handle
        result = handle(ctx)
        assert "1 blocked" in result

    def test_projects_sorted_by_count(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        outcomes = [
            _make_outcome(project="alpha"),
            _make_outcome(project="beta"),
            _make_outcome(project="beta"),
            _make_outcome(project="beta"),
        ]
        _write_outcomes(ctx.instance_dir, outcomes)
        from skills.core.stats.handler import handle
        result = handle(ctx)
        lines = result.split("\n")
        # Project lines have format "  project: N (X% productive)" — skip header lines
        project_lines = [
            l.strip() for l in lines
            if "% productive)" in l and not l.strip().startswith("Total")
        ]
        # beta (3 sessions) should come before alpha (1 session)
        assert project_lines[0].startswith("beta")


# ---------------------------------------------------------------------------
# Tests: Project detail (with filter)
# ---------------------------------------------------------------------------

class TestProjectDetail:
    def test_unknown_project(self, tmp_path):
        ctx = _make_ctx(tmp_path, args="nonexistent")
        outcomes = [_make_outcome(project="koan")]
        _write_outcomes(ctx.instance_dir, outcomes)
        from skills.core.stats.handler import handle
        result = handle(ctx)
        assert "No data for 'nonexistent'" in result
        assert "koan" in result  # suggests known projects

    def test_detail_header(self, tmp_path):
        ctx = _make_ctx(tmp_path, args="koan")
        outcomes = [
            _make_outcome(outcome="productive", hours_ago=2),
            _make_outcome(outcome="empty", hours_ago=1),
        ]
        _write_outcomes(ctx.instance_dir, outcomes)
        from skills.core.stats.handler import handle
        result = handle(ctx)
        assert "Stats: koan" in result
        assert "2" in result  # 2 sessions
        assert "50%" in result  # 1/2

    def test_mode_breakdown(self, tmp_path):
        ctx = _make_ctx(tmp_path, args="koan")
        outcomes = [
            _make_outcome(mode="implement", outcome="productive"),
            _make_outcome(mode="implement", outcome="empty"),
            _make_outcome(mode="deep", outcome="productive"),
        ]
        _write_outcomes(ctx.instance_dir, outcomes)
        from skills.core.stats.handler import handle
        result = handle(ctx)
        assert "By mode:" in result
        assert "implement: 2" in result
        assert "deep: 1" in result

    def test_duration_average(self, tmp_path):
        ctx = _make_ctx(tmp_path, args="koan")
        outcomes = [
            _make_outcome(duration=10),
            _make_outcome(duration=20),
            _make_outcome(duration=30),
        ]
        _write_outcomes(ctx.instance_dir, outcomes)
        from skills.core.stats.handler import handle
        result = handle(ctx)
        assert "Avg duration: 20 min" in result

    def test_recent_sessions_shown(self, tmp_path):
        ctx = _make_ctx(tmp_path, args="koan")
        outcomes = [
            _make_outcome(outcome="productive", summary="PR created", hours_ago=i)
            for i in range(7)
        ]
        _write_outcomes(ctx.instance_dir, outcomes)
        from skills.core.stats.handler import handle
        result = handle(ctx)
        assert "Recent:" in result
        # Should show at most 5 recent
        recent_lines = [l for l in result.split("\n") if l.strip().startswith("+") or l.strip().startswith("-")]
        assert len(recent_lines) == 5

    def test_recent_icons(self, tmp_path):
        ctx = _make_ctx(tmp_path, args="koan")
        outcomes = [
            _make_outcome(outcome="productive", summary="tests pass"),
            _make_outcome(outcome="empty", summary="no changes"),
            _make_outcome(outcome="blocked", summary="merge queue"),
        ]
        _write_outcomes(ctx.instance_dir, outcomes)
        from skills.core.stats.handler import handle
        result = handle(ctx)
        assert "+" in result  # productive
        assert "-" in result  # empty
        assert "~" in result  # blocked

    def test_staleness_in_detail(self, tmp_path):
        ctx = _make_ctx(tmp_path, args="koan")
        outcomes = [
            _make_outcome(outcome="productive", hours_ago=10),
        ] + [
            _make_outcome(outcome="empty", hours_ago=i)
            for i in range(5)
        ]
        _write_outcomes(ctx.instance_dir, outcomes)
        from skills.core.stats.handler import handle
        result = handle(ctx)
        assert "Staleness: 5" in result

    def test_no_staleness_when_fresh(self, tmp_path):
        ctx = _make_ctx(tmp_path, args="koan")
        outcomes = [_make_outcome(outcome="productive")]
        _write_outcomes(ctx.instance_dir, outcomes)
        from skills.core.stats.handler import handle
        result = handle(ctx)
        assert "Staleness" not in result

    def test_summary_truncation(self, tmp_path):
        ctx = _make_ctx(tmp_path, args="koan")
        long_summary = "x" * 100
        outcomes = [_make_outcome(summary=long_summary)]
        _write_outcomes(ctx.instance_dir, outcomes)
        from skills.core.stats.handler import handle
        result = handle(ctx)
        assert "..." in result

    def test_zero_duration_handled(self, tmp_path):
        ctx = _make_ctx(tmp_path, args="koan")
        outcomes = [_make_outcome(duration=0)]
        _write_outcomes(ctx.instance_dir, outcomes)
        from skills.core.stats.handler import handle
        result = handle(ctx)
        # Should not crash, and should not show avg duration
        assert "Stats: koan" in result


# ---------------------------------------------------------------------------
# Tests: Internal helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_consecutive_non_productive_all_productive(self):
        from skills.core.stats.handler import _consecutive_non_productive
        outcomes = [{"outcome": "productive"}, {"outcome": "productive"}]
        assert _consecutive_non_productive(outcomes) == 0

    def test_consecutive_non_productive_all_empty(self):
        from skills.core.stats.handler import _consecutive_non_productive
        outcomes = [{"outcome": "empty"}, {"outcome": "empty"}, {"outcome": "empty"}]
        assert _consecutive_non_productive(outcomes) == 3

    def test_consecutive_non_productive_mixed(self):
        from skills.core.stats.handler import _consecutive_non_productive
        outcomes = [
            {"outcome": "productive"},
            {"outcome": "empty"},
            {"outcome": "blocked"},
        ]
        # Last 2 are non-productive
        assert _consecutive_non_productive(outcomes) == 2

    def test_consecutive_non_productive_ends_productive(self):
        from skills.core.stats.handler import _consecutive_non_productive
        outcomes = [
            {"outcome": "empty"},
            {"outcome": "empty"},
            {"outcome": "productive"},
        ]
        assert _consecutive_non_productive(outcomes) == 0

    def test_consecutive_non_productive_empty_list(self):
        from skills.core.stats.handler import _consecutive_non_productive
        assert _consecutive_non_productive([]) == 0

    def test_load_outcomes_missing_file(self, tmp_path):
        from skills.core.stats.handler import _load_outcomes
        assert _load_outcomes(tmp_path / "missing.json") == []
