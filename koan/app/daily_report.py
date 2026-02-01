#!/usr/bin/env python3
"""
Koan -- Daily report generator

Builds a concise Telegram-friendly digest of the day's activity.
Called from run.sh at session boundaries (morning or evening).

Usage from shell:
    python3 daily_report.py [--yesterday]

Usage from Python:
    from app.daily_report import generate_report, should_send_report
"""

import os
import re
import sys
from datetime import date, timedelta, datetime
from pathlib import Path
from typing import List, Optional

from app.notify import send_telegram


from app.utils import load_dotenv

load_dotenv()

KOAN_ROOT = Path(__file__).parent.parent
INSTANCE_DIR = KOAN_ROOT / "instance"
MISSIONS_FILE = INSTANCE_DIR / "missions.md"
REPORT_MARKER = KOAN_ROOT / ".koan-daily-report"


def should_send_report() -> Optional[str]:
    """Check if a daily report should be sent now.

    Returns:
        "morning" if 7-9am and no report sent today,
        "evening" if after 8pm and quota exhausted,
        None otherwise.
    """
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    # Don't send the same report twice in one day
    if REPORT_MARKER.exists():
        marker_date = REPORT_MARKER.read_text().strip()
        if marker_date == today:
            return None

    # Morning report: 7am-9am -> report on yesterday
    if 7 <= now.hour <= 9:
        return "morning"

    # Evening report: after 8pm if quota exhausted
    if now.hour >= 20:
        quota_file = KOAN_ROOT / ".koan-quota-reset"
        if quota_file.exists():
            return "evening"

    return None


def _read_journal(target_date: date) -> str:
    """Read all journal entries for a given date across all project subdirs."""
    journal_dir = INSTANCE_DIR / "journal" / target_date.strftime("%Y-%m-%d")
    if not journal_dir.exists():
        return ""

    parts = []
    # Check for flat file (legacy)
    flat = journal_dir.parent / f"{target_date.strftime('%Y-%m-%d')}.md"
    if flat.is_file():
        parts.append(flat.read_text())

    # Check nested per-project files
    if journal_dir.is_dir():
        for f in sorted(journal_dir.iterdir()):
            if f.suffix == ".md":
                parts.append(f"[{f.stem}]\n{f.read_text()}")

    return "\n\n---\n\n".join(parts)


def _parse_completed_missions() -> List[str]:
    """Extract recently completed missions from missions.md."""
    if not MISSIONS_FILE.exists():
        return []

    from app.missions import parse_sections

    content = MISSIONS_FILE.read_text()
    sections = parse_sections(content)
    completed = []
    for item in sections["done"]:
        first_line = item.split("\n")[0].strip()
        if first_line.startswith("- **"):
            title = re.sub(r"[*_]", "", first_line[2:]).strip()
            title = re.split(r"\s*[\(\—]", title)[0].strip()
            completed.append(title)

    return completed


def _count_pending_missions() -> int:
    """Count pending missions."""
    if not MISSIONS_FILE.exists():
        return 0

    from app.missions import count_pending

    return count_pending(MISSIONS_FILE.read_text())


def generate_report(report_type: str = "morning") -> str:
    """Generate a daily digest.

    Args:
        report_type: "morning" (yesterday's recap) or "evening" (today's recap)
    """
    if report_type == "morning":
        target = date.today() - timedelta(days=1)
        header = f"Rapport du {target.strftime('%d/%m/%Y')}"
    else:
        target = date.today()
        header = f"Bilan de la journee"

    journal = _read_journal(target)
    completed = _parse_completed_missions()
    pending = _count_pending_missions()

    lines = [f"-- {header} --", ""]

    # Completed missions
    if completed:
        lines.append("Missions terminees:")
        for m in completed[-5:]:  # Last 5 max
            lines.append(f"  . {m}")
        lines.append("")

    # Pending missions
    if pending > 0:
        lines.append(f"En attente: {pending} mission(s)")
        lines.append("")

    # Journal summary (extract key sections)
    if journal:
        # Extract section headers as activity summary
        activities = []
        for line in journal.splitlines():
            if line.startswith("## ") and not line.startswith("## Quota"):
                activity = line[3:].strip()
                # Remove timestamps
                activity = re.sub(r"\s*--\s*\d{2}:\d{2}(:\d{2})?", "", activity)
                if activity:
                    activities.append(activity)

        if activities:
            lines.append("Activite:")
            for a in activities[-6:]:  # Last 6 max
                lines.append(f"  . {a}")
            lines.append("")
    else:
        lines.append("Pas d'activite enregistree.")
        lines.append("")

    # In-progress long-running items
    if MISSIONS_FILE.exists():
        from app.missions import parse_sections

        content = MISSIONS_FILE.read_text()
        sections = parse_sections(content)
        long_running = []
        for item in sections["in_progress"]:
            first_line = item.split("\n")[0].strip()
            if first_line.startswith("### "):
                long_running.append(first_line[4:].strip())

        if long_running:
            lines.append("En cours (long):")
            for lr in long_running:
                lines.append(f"  . {lr}")
            lines.append("")

    lines.append("-- Koan")
    return "\n".join(lines)


def mark_report_sent():
    """Mark today's report as sent."""
    REPORT_MARKER.write_text(date.today().strftime("%Y-%m-%d"))


def send_daily_report(report_type: str = None) -> bool:
    """Check conditions and send daily report if appropriate.

    Args:
        report_type: Force a specific type ("morning"/"evening"), or None for auto-detect.

    Returns:
        True if a report was sent.
    """
    if report_type is None:
        report_type = should_send_report()

    if report_type is None:
        return False

    report = generate_report(report_type)
    success = send_telegram(report)

    if success:
        mark_report_sent()
        print(f"[daily-report] {report_type} report sent")
    else:
        print(f"[daily-report] Failed to send {report_type} report")

    return success


if __name__ == "__main__":
    forced_type = None
    if "--yesterday" in sys.argv or "--morning" in sys.argv:
        forced_type = "morning"
    elif "--evening" in sys.argv or "--today" in sys.argv:
        forced_type = "evening"

    if forced_type:
        report = generate_report(forced_type)
        print(report)
        print()
        success = send_telegram(report)
        if success:
            mark_report_sent()
        sys.exit(0 if success else 1)
    else:
        sent = send_daily_report()
        sys.exit(0 if sent else 1)
