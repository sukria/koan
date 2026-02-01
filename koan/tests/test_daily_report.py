"""Tests for daily_report.py — report generation, mission parsing, time logic."""

from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from app.daily_report import (
    should_send_report,
    _read_journal,
    _parse_completed_missions,
    _count_pending_missions,
    generate_report,
)


# ---------------------------------------------------------------------------
# should_send_report
# ---------------------------------------------------------------------------

class TestShouldSendReport:
    def test_morning_window(self, tmp_path):
        morning = datetime(2026, 2, 1, 8, 0)
        with patch("app.daily_report.datetime") as mock_dt, \
             patch("app.daily_report.REPORT_MARKER", tmp_path / ".marker"):
            mock_dt.now.return_value = morning
            mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
            result = should_send_report()
        assert result == "morning"

    def test_outside_window(self, tmp_path):
        noon = datetime(2026, 2, 1, 12, 0)
        with patch("app.daily_report.datetime") as mock_dt, \
             patch("app.daily_report.REPORT_MARKER", tmp_path / ".marker"):
            mock_dt.now.return_value = noon
            mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
            result = should_send_report()
        assert result is None

    def test_evening_with_quota(self, tmp_path):
        evening = datetime(2026, 2, 1, 21, 0)
        quota_file = tmp_path / ".koan-quota-reset"
        quota_file.write_text("resets 7am")
        with patch("app.daily_report.datetime") as mock_dt, \
             patch("app.daily_report.REPORT_MARKER", tmp_path / ".marker"), \
             patch("app.daily_report.KOAN_ROOT", tmp_path):
            mock_dt.now.return_value = evening
            mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
            result = should_send_report()
        assert result == "evening"

    def test_no_duplicate_report(self, tmp_path):
        morning = datetime(2026, 2, 1, 8, 0)
        marker = tmp_path / ".marker"
        marker.write_text("2026-02-01")
        with patch("app.daily_report.datetime") as mock_dt, \
             patch("app.daily_report.REPORT_MARKER", marker):
            mock_dt.now.return_value = morning
            mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
            result = should_send_report()
        assert result is None


# ---------------------------------------------------------------------------
# _read_journal
# ---------------------------------------------------------------------------

class TestReadJournal:
    def test_nested_journal(self, tmp_path):
        with patch("app.daily_report.INSTANCE_DIR", tmp_path):
            journal_dir = tmp_path / "journal" / "2026-02-01"
            journal_dir.mkdir(parents=True)
            (journal_dir / "koan.md").write_text("## Session 28\nDid stuff.")
            result = _read_journal(date(2026, 2, 1))
        assert "Session 28" in result
        assert "[koan]" in result

    def test_missing_journal(self, tmp_path):
        with patch("app.daily_report.INSTANCE_DIR", tmp_path):
            result = _read_journal(date(2026, 2, 1))
        assert result == ""


# ---------------------------------------------------------------------------
# _parse_completed_missions
# ---------------------------------------------------------------------------

class TestParseCompletedMissions:
    def test_bold_entries(self, tmp_path):
        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n"
            "## Terminées\n\n"
            "- **Fix IDOR** (session 22)\n"
            "- **Dunning emails** — session 20\n"
            "- Old plain entry\n"
        )
        with patch("app.daily_report.MISSIONS_FILE", missions_file):
            result = _parse_completed_missions()
        assert len(result) == 2
        assert "Fix IDOR" in result[0]
        assert "Dunning emails" in result[1]

    def test_empty_done_section(self, tmp_path):
        missions_file = tmp_path / "missions.md"
        missions_file.write_text("# Missions\n\n## Terminées\n\n")
        with patch("app.daily_report.MISSIONS_FILE", missions_file):
            result = _parse_completed_missions()
        assert result == []


# ---------------------------------------------------------------------------
# _count_pending_missions
# ---------------------------------------------------------------------------

class TestCountPendingMissions:
    def test_count(self, tmp_path):
        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n"
            "## En attente\n\n"
            "- task 1\n"
            "- task 2\n\n"
            "## En cours\n\n"
        )
        with patch("app.daily_report.MISSIONS_FILE", missions_file):
            assert _count_pending_missions() == 2

    def test_no_pending(self, tmp_path):
        missions_file = tmp_path / "missions.md"
        missions_file.write_text("# Missions\n\n## En attente\n\n(aucune)\n\n## En cours\n\n")
        with patch("app.daily_report.MISSIONS_FILE", missions_file):
            assert _count_pending_missions() == 0


# ---------------------------------------------------------------------------
# generate_report
# ---------------------------------------------------------------------------

class TestGenerateReport:
    def test_morning_report(self, tmp_path):
        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n"
            "## En attente\n\n"
            "- task 1\n\n"
            "## En cours\n\n"
            "### Big project (PRIO)\n"
            "- sub-item\n\n"
            "## Terminées\n\n"
            "- **Done thing** (session 1)\n"
        )
        with patch("app.daily_report.MISSIONS_FILE", missions_file), \
             patch("app.daily_report.INSTANCE_DIR", tmp_path):
            report = generate_report("morning")

        assert "Rapport du" in report
        assert "Done thing" in report
        assert "En attente: 1" in report
        assert "Big project" in report
        assert "-- Koan" in report

    def test_evening_report(self, tmp_path):
        missions_file = tmp_path / "missions.md"
        missions_file.write_text("# Missions\n\n## En attente\n\n## En cours\n\n## Terminées\n\n")
        with patch("app.daily_report.MISSIONS_FILE", missions_file), \
             patch("app.daily_report.INSTANCE_DIR", tmp_path):
            report = generate_report("evening")

        assert "Bilan de la journee" in report
        assert "-- Koan" in report
