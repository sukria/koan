"""Tests for session_tracker.py — session outcome tracking and staleness detection."""

import json
from pathlib import Path

import pytest

from app.session_tracker import (
    classify_session,
    record_outcome,
    get_recent_outcomes,
    get_staleness_score,
    get_staleness_warning,
    get_project_freshness,
    _extract_summary,
    _load_outcomes,
    MAX_OUTCOMES,
)


@pytest.fixture
def tracker_env(tmp_path):
    """Create a minimal environment for session tracker testing."""
    instance = tmp_path / "instance"
    instance.mkdir()
    return str(instance)


# --- classify_session ---

class TestClassifySession:
    """Tests for session classification logic."""

    def test_empty_content(self):
        assert classify_session("") == "empty"

    def test_productive_with_branch(self):
        content = "Branch `koan/fix-auth` pushed. Tests pass. PR #42 created."
        assert classify_session(content) == "productive"

    def test_productive_with_implementation(self):
        content = "Implemented new feature. Added 15 tests. Branch pushed."
        assert classify_session(content) == "productive"

    def test_empty_verification_session(self):
        content = (
            "Verification session — 28 koan/* branches pending merge, "
            "codebase healthy. No code changes. Legitimate waiting state. "
            "All work blocked on merge reviews."
        )
        assert classify_session(content) == "empty"

    def test_empty_housekeeping(self):
        content = (
            "Housekeeping only. No actionable work found. "
            "Same state as previous sessions. Waiting state."
        )
        assert classify_session(content) == "empty"

    def test_blocked_on_merges(self):
        content = (
            "All 5 branches verified. Blocked on merge reviews. "
            "No new issues found."
        )
        assert classify_session(content) == "blocked"

    def test_productive_with_fixes(self):
        content = "Fixed 4 failing SEO tests. Cleaned up imports. Branch pushed."
        assert classify_session(content) == "productive"

    def test_productive_refactoring(self):
        content = "Refactored PII encryption. Migrated datetime.utcnow(). Tests pass."
        assert classify_session(content) == "productive"

    def test_french_no_code(self):
        content = "Pas de code — mission analytique. Issue #105 created."
        assert classify_session(content) == "empty"  # "pas de code" = no code produced

    def test_analytical_with_branch_is_productive(self):
        content = "Mission analytique. Branch pushed with analysis report."
        assert classify_session(content) == "productive"

    def test_strong_empty_overrides_weak_productive(self):
        """Many empty signals should override a few productive ones."""
        content = (
            "Verification session. No code changes. "
            "Same state as sessions 20-36. Legitimate waiting state. "
            "Merge queue is only bottleneck. Created a comment."
        )
        assert classify_session(content) == "empty"

    def test_merge_queue_without_productive(self):
        content = "Merge queue is the bottleneck. All branches verified clean."
        assert classify_session(content) == "blocked"

    def test_default_productive(self):
        """Ambiguous content defaults to productive."""
        content = "Explored the codebase. Read several files."
        assert classify_session(content) == "productive"

    def test_identical_session_keyword(self):
        content = "Identical session to previous. No code."
        assert classify_session(content) == "empty"


# --- _extract_summary ---

class TestExtractSummary:

    def test_empty_content(self):
        assert _extract_summary("") == ""

    def test_skips_headers_and_metadata(self):
        content = """# Autonomous run
Project: koan
Started: 2026-02-21 11:13:50
Run: 3/3
Mode: deep

---
11:13 — Reading context files
"""
        assert _extract_summary(content) == "11:13 — Reading context files"

    def test_truncates_long_lines(self):
        content = "A" * 200
        result = _extract_summary(content)
        assert len(result) <= 123  # 120 + "..."
        assert result.endswith("...")


# --- record_outcome ---

def _mock_atomic_write(path, content):
    """Test-safe atomic_write that just writes directly."""
    Path(path).write_text(content)


class TestRecordOutcome:

    def test_records_productive(self, tracker_env, monkeypatch):
        monkeypatch.setattr("app.utils.atomic_write", _mock_atomic_write)

        entry = record_outcome(
            tracker_env, "koan", "deep", 15,
            "Implemented session tracker. Branch pushed. Tests pass.",
        )
        assert entry["outcome"] == "productive"
        assert entry["project"] == "koan"
        assert entry["mode"] == "deep"
        assert entry["duration_minutes"] == 15

        # Verify file was written
        outcomes_path = Path(tracker_env) / "session_outcomes.json"
        data = json.loads(outcomes_path.read_text())
        assert len(data) == 1
        assert data[0]["outcome"] == "productive"

    def test_records_empty(self, tracker_env, monkeypatch):
        monkeypatch.setattr("app.utils.atomic_write", _mock_atomic_write)

        entry = record_outcome(
            tracker_env, "backend", "review", 5,
            "Verification session. No code. Waiting state.",
        )
        assert entry["outcome"] == "empty"

    def test_appends_to_existing(self, tracker_env, monkeypatch):
        monkeypatch.setattr("app.utils.atomic_write", _mock_atomic_write)

        outcomes_path = Path(tracker_env) / "session_outcomes.json"
        outcomes_path.write_text(json.dumps([
            {"timestamp": "2026-02-20T10:00:00", "project": "koan",
             "mode": "deep", "duration_minutes": 10,
             "outcome": "productive", "summary": "old session"}
        ]))

        record_outcome(
            tracker_env, "koan", "implement", 8,
            "Added tests. Branch pushed.",
        )

        data = json.loads(outcomes_path.read_text())
        assert len(data) == 2

    def test_caps_at_max(self, tracker_env, monkeypatch):
        monkeypatch.setattr("app.utils.atomic_write", _mock_atomic_write)

        # Pre-fill with MAX_OUTCOMES entries
        outcomes_path = Path(tracker_env) / "session_outcomes.json"
        existing = [
            {"timestamp": f"2026-02-{i:02d}T10:00:00", "project": "koan",
             "mode": "deep", "duration_minutes": 5,
             "outcome": "productive", "summary": f"session {i}"}
            for i in range(MAX_OUTCOMES)
        ]
        outcomes_path.write_text(json.dumps(existing))

        record_outcome(tracker_env, "koan", "deep", 5, "new session. branch pushed.")

        data = json.loads(outcomes_path.read_text())
        assert len(data) == MAX_OUTCOMES
        # The oldest entry should have been dropped
        assert data[-1]["summary"] == "new session. branch pushed."

    def test_handles_corrupt_file(self, tracker_env, monkeypatch):
        monkeypatch.setattr("app.utils.atomic_write", _mock_atomic_write)

        outcomes_path = Path(tracker_env) / "session_outcomes.json"
        outcomes_path.write_text("not json")

        entry = record_outcome(
            tracker_env, "koan", "deep", 5,
            "Fixed bug. Branch pushed.",
        )
        assert entry["outcome"] == "productive"

        # Should have overwritten with valid data
        data = json.loads(outcomes_path.read_text())
        assert len(data) == 1


# --- get_recent_outcomes ---

class TestGetRecentOutcomes:

    def test_no_file(self, tracker_env):
        result = get_recent_outcomes(tracker_env, "koan")
        assert result == []

    def test_filters_by_project(self, tracker_env):
        outcomes_path = Path(tracker_env) / "session_outcomes.json"
        outcomes_path.write_text(json.dumps([
            {"project": "koan", "outcome": "productive", "summary": "a"},
            {"project": "backend", "outcome": "empty", "summary": "b"},
            {"project": "koan", "outcome": "empty", "summary": "c"},
        ]))

        result = get_recent_outcomes(tracker_env, "koan")
        assert len(result) == 2
        assert result[0]["summary"] == "a"
        assert result[1]["summary"] == "c"

    def test_respects_limit(self, tracker_env):
        outcomes_path = Path(tracker_env) / "session_outcomes.json"
        outcomes_path.write_text(json.dumps([
            {"project": "koan", "outcome": "productive", "summary": str(i)}
            for i in range(20)
        ]))

        result = get_recent_outcomes(tracker_env, "koan", limit=5)
        assert len(result) == 5
        # Should be the last 5
        assert result[0]["summary"] == "15"


# --- get_staleness_score ---

class TestGetStalenessScore:

    def test_no_data(self, tracker_env):
        assert get_staleness_score(tracker_env, "koan") == 0

    def test_all_productive(self, tracker_env):
        outcomes_path = Path(tracker_env) / "session_outcomes.json"
        outcomes_path.write_text(json.dumps([
            {"project": "koan", "outcome": "productive", "summary": str(i)}
            for i in range(5)
        ]))
        assert get_staleness_score(tracker_env, "koan") == 0

    def test_consecutive_empty(self, tracker_env):
        outcomes_path = Path(tracker_env) / "session_outcomes.json"
        outcomes_path.write_text(json.dumps([
            {"project": "koan", "outcome": "productive", "summary": "good"},
            {"project": "koan", "outcome": "empty", "summary": "bad1"},
            {"project": "koan", "outcome": "empty", "summary": "bad2"},
            {"project": "koan", "outcome": "blocked", "summary": "bad3"},
        ]))
        assert get_staleness_score(tracker_env, "koan") == 3

    def test_mixed_with_productive_break(self, tracker_env):
        outcomes_path = Path(tracker_env) / "session_outcomes.json"
        outcomes_path.write_text(json.dumps([
            {"project": "koan", "outcome": "empty", "summary": "old empty"},
            {"project": "koan", "outcome": "productive", "summary": "break"},
            {"project": "koan", "outcome": "empty", "summary": "new empty"},
        ]))
        # Only 1 consecutive empty (after the productive break)
        assert get_staleness_score(tracker_env, "koan") == 1

    def test_different_project_not_counted(self, tracker_env):
        outcomes_path = Path(tracker_env) / "session_outcomes.json"
        outcomes_path.write_text(json.dumps([
            {"project": "koan", "outcome": "productive", "summary": "good"},
            {"project": "backend", "outcome": "empty", "summary": "not koan"},
            {"project": "koan", "outcome": "empty", "summary": "bad"},
        ]))
        assert get_staleness_score(tracker_env, "koan") == 1


# --- get_staleness_warning ---

class TestGetStalenessWarning:

    def test_no_warning_for_fresh(self, tracker_env):
        assert get_staleness_warning(tracker_env, "koan") == ""

    def test_no_warning_under_threshold(self, tracker_env):
        outcomes_path = Path(tracker_env) / "session_outcomes.json"
        outcomes_path.write_text(json.dumps([
            {"project": "koan", "outcome": "empty", "summary": "e1"},
            {"project": "koan", "outcome": "empty", "summary": "e2"},
        ]))
        assert get_staleness_warning(tracker_env, "koan") == ""

    def test_warning_at_3(self, tracker_env):
        outcomes_path = Path(tracker_env) / "session_outcomes.json"
        outcomes_path.write_text(json.dumps([
            {"project": "koan", "outcome": "empty", "summary": "e1"},
            {"project": "koan", "outcome": "empty", "summary": "e2"},
            {"project": "koan", "outcome": "empty", "summary": "e3"},
        ]))
        warning = get_staleness_warning(tracker_env, "koan")
        assert "WARNING" in warning
        assert "3 sessions" in warning

    def test_critical_at_5(self, tracker_env):
        outcomes_path = Path(tracker_env) / "session_outcomes.json"
        outcomes_path.write_text(json.dumps([
            {"project": "koan", "outcome": "empty", "summary": f"e{i}"}
            for i in range(6)
        ]))
        warning = get_staleness_warning(tracker_env, "koan")
        assert "CRITICAL" in warning
        assert "STOP" in warning

    def test_warning_includes_summaries(self, tracker_env):
        outcomes_path = Path(tracker_env) / "session_outcomes.json"
        outcomes_path.write_text(json.dumps([
            {"project": "koan", "outcome": "empty", "summary": "verification again"},
            {"project": "koan", "outcome": "empty", "summary": "still waiting"},
            {"project": "koan", "outcome": "empty", "summary": "same state"},
        ]))
        warning = get_staleness_warning(tracker_env, "koan")
        assert "verification again" in warning or "still waiting" in warning


# --- get_project_freshness ---

class TestGetProjectFreshness:

    def test_all_fresh(self, tracker_env):
        """Projects with no history get max weight."""
        projects = [("koan", "/p/koan"), ("backend", "/p/backend")]
        weights = get_project_freshness(tracker_env, projects)
        assert weights["koan"] == 10
        assert weights["backend"] == 10

    def test_stale_project_lower_weight(self, tracker_env):
        outcomes_path = Path(tracker_env) / "session_outcomes.json"
        outcomes_path.write_text(json.dumps([
            {"project": "koan", "outcome": "productive", "summary": "good"},
            {"project": "backend", "outcome": "empty", "summary": "e1"},
            {"project": "backend", "outcome": "empty", "summary": "e2"},
            {"project": "backend", "outcome": "empty", "summary": "e3"},
            {"project": "backend", "outcome": "empty", "summary": "e4"},
            {"project": "backend", "outcome": "empty", "summary": "e5"},
        ]))
        projects = [("koan", "/p/koan"), ("backend", "/p/backend")]
        weights = get_project_freshness(tracker_env, projects)
        assert weights["koan"] == 10
        assert weights["backend"] == 1  # Very stale

    def test_medium_staleness(self, tracker_env):
        outcomes_path = Path(tracker_env) / "session_outcomes.json"
        outcomes_path.write_text(json.dumps([
            {"project": "koan", "outcome": "empty", "summary": "e1"},
            {"project": "koan", "outcome": "empty", "summary": "e2"},
        ]))
        projects = [("koan", "/p/koan")]
        weights = get_project_freshness(tracker_env, projects)
        assert weights["koan"] == 6  # staleness 2 → weight 6


# --- Integration with deep_research ---

class TestDeepResearchStaleness:

    def test_staleness_warning_in_format(self, tmp_path):
        """DeepResearch.format_for_agent() includes staleness warning."""
        instance = tmp_path / "instance"
        project_path = tmp_path / "project"
        (instance / "memory" / "projects" / "testproj").mkdir(parents=True)
        (instance / "journal").mkdir(parents=True)
        project_path.mkdir()

        # Create stale outcomes
        outcomes_path = instance / "session_outcomes.json"
        outcomes_path.write_text(json.dumps([
            {"project": "testproj", "outcome": "empty", "summary": f"e{i}"}
            for i in range(5)
        ]))

        from app.deep_research import DeepResearch
        research = DeepResearch(instance, "testproj", project_path)

        with pytest.MonkeyPatch.context() as m:
            m.setattr(research, "get_open_issues", lambda limit=10: [])
            output = research.format_for_agent()

        assert "CRITICAL" in output
        assert "STOP" in output


# --- Classification edge cases (improved classifier) ---

class TestClassifySessionEdgeCases:
    """Regression tests for the improved session classifier."""

    def test_generic_words_dont_override_empty(self):
        """Words like 'added', 'created', 'fixed' should NOT count as productive
        signals that override empty keywords."""
        content = "No code changes. Created a note. Added nothing useful. Fixed nothing."
        assert classify_session(content) == "empty"

    def test_strong_productive_overrides_empty(self):
        """A clear productive signal wins even with empty keywords present."""
        content = "No code changes in most areas, but branch pushed with fix."
        assert classify_session(content) == "productive"

    def test_many_empty_overrides_blocked(self):
        """When overwhelmingly empty, don't classify as blocked."""
        content = (
            "Verification session. No code changes. Same state. "
            "Legitimate waiting state. Merge queue full."
        )
        assert classify_session(content) == "empty"

    def test_blocked_with_single_signal(self):
        """A single blocked keyword with no productive work → blocked."""
        content = "All branches pending. Blocked on merge reviews."
        assert classify_session(content) == "blocked"

    def test_blocked_with_weak_productive(self):
        """Blocked + weak productive signal → productive (implemented beats blocked)."""
        content = "Blocked on merge for old PRs, but implemented a new module."
        assert classify_session(content) == "productive"

    def test_refactored_is_productive(self):
        content = "Refactored the config module. Cleaner interfaces."
        assert classify_session(content) == "productive"

    def test_migrated_is_productive(self):
        content = "Migrated from env vars to YAML config."
        assert classify_session(content) == "productive"

    def test_draft_pr_is_productive(self):
        content = "Draft PR submitted for review."
        assert classify_session(content) == "productive"

    def test_tests_pass_is_productive(self):
        content = "All tests pass after cleanup."
        assert classify_session(content) == "productive"

    def test_two_empty_signals_is_empty(self):
        content = "No code. Identical session to before."
        assert classify_session(content) == "empty"

    def test_single_empty_signal_is_empty(self):
        content = "No code today but thinking about next steps."
        assert classify_session(content) == "empty"


# --- _load_outcomes type validation ---

class TestLoadOutcomesValidation:
    """Tests for _load_outcomes type safety."""

    def test_dict_json_returns_empty(self, tmp_path):
        """A JSON object (not array) should be treated as corrupt."""
        outcomes_path = tmp_path / "session_outcomes.json"
        outcomes_path.write_text('{"not": "a list"}')
        assert _load_outcomes(outcomes_path) == []

    def test_string_json_returns_empty(self, tmp_path):
        outcomes_path = tmp_path / "session_outcomes.json"
        outcomes_path.write_text('"just a string"')
        assert _load_outcomes(outcomes_path) == []

    def test_valid_list_works(self, tmp_path):
        outcomes_path = tmp_path / "session_outcomes.json"
        outcomes_path.write_text('[{"a": 1}]')
        result = _load_outcomes(outcomes_path)
        assert len(result) == 1
