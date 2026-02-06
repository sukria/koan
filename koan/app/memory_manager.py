#!/usr/bin/env python3
"""
Koan — Memory manager

Handles memory scope isolation and periodic cleanup:
- Scoped summary: filter summary.md to only show relevant project sessions
- Summary compaction: keep last N sessions per project, archive older ones
- Learnings dedup: remove duplicate lines from learnings files
- Journal archival: compact old daily journals into monthly digests
- Learnings cap: truncate oversized learnings to keep most recent entries

Designed to scale: a 1-year instance with 20 runs/day across 3 projects
produces ~200K lines of journal. Without compaction, context loading and
git operations degrade. This module keeps growth bounded.

Usage from shell:
    python3 memory_manager.py <instance_dir> <command> [args...]

Commands:
    scoped-summary <project_name>   Print summary.md filtered to project-relevant sessions
    compact <max_sessions>          Compact summary.md, keeping last N sessions per date
    cleanup-learnings <project>     Remove duplicate lines from learnings.md
    archive-journals [days]         Archive journals older than N days (default 30)
    cleanup                         Run all cleanup tasks
"""

import re
import shutil
import sys
from collections import defaultdict
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

from app.utils import atomic_write


# ---------------------------------------------------------------------------
# Pure parsing helpers (stateless, no instance_dir needed)
# ---------------------------------------------------------------------------

def parse_summary_sessions(content: str) -> List[Tuple[str, str, str]]:
    """Parse summary.md into (date_header, session_text, project_hint) tuples.

    Each entry is a paragraph under a ## date header. The project_hint is
    extracted from "(projet: X)" or "(project: X)" markers, or empty if none.
    """
    sessions = []
    current_date = ""
    current_lines: List[str] = []

    for line in content.splitlines():
        if line.startswith("## "):
            # Flush previous
            if current_lines and current_date:
                _flush_sessions(current_date, current_lines, sessions)
            current_date = line
            current_lines = []
        else:
            current_lines.append(line)

    # Flush last
    if current_lines and current_date:
        _flush_sessions(current_date, current_lines, sessions)

    return sessions


def _flush_sessions(date_header: str, lines: List[str], sessions: list):
    """Split lines into individual session paragraphs and append to sessions."""
    current_paragraph: List[str] = []

    for line in lines:
        if line.strip() == "" and current_paragraph:
            text = "\n".join(current_paragraph)
            project = _extract_project_hint(text)
            sessions.append((date_header, text, project))
            current_paragraph = []
        elif line.strip():
            current_paragraph.append(line)

    if current_paragraph:
        text = "\n".join(current_paragraph)
        project = _extract_project_hint(text)
        sessions.append((date_header, text, project))


def _extract_project_hint(text: str) -> str:
    """Extract project name from session text like '(projet: koan)' or 'projet:koan'."""
    m = re.search(r"\(?\s*projec?t\s*:\s*([a-zA-Z0-9_-]+)\s*\)?", text, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    return ""


def _extract_session_digest(content: str) -> List[str]:
    """Extract a one-line digest per session from a journal file.

    Parses ## Session N headers and takes the first meaningful line after
    the ### sub-header (or the header itself if no sub-header).
    """
    digests = []
    current_header = ""
    found_sub = False

    for line in content.splitlines():
        if line.startswith("## Session") or line.startswith("## Mode"):
            if current_header and not found_sub:
                digests.append(current_header)
            current_header = line.strip()
            found_sub = False
        elif line.startswith("### ") and current_header:
            digests.append(f"{current_header} — {line.lstrip('#').strip()}")
            found_sub = True
            current_header = ""

    if current_header and not found_sub:
        digests.append(current_header)

    return digests


def _rebuild_sessions(title: str, sessions: List[Tuple[str, str, ...]]) -> str:
    """Rebuild summary content from a title and list of session tuples."""
    output_lines = []
    if title:
        output_lines.append(title)
        output_lines.append("")

    current_date = ""
    for entry in sessions:
        date_header = entry[0]
        text = entry[1]
        if date_header != current_date:
            if current_date:
                output_lines.append("")
            output_lines.append(date_header)
            output_lines.append("")
            current_date = date_header
        output_lines.append(text)
        output_lines.append("")

    return "\n".join(output_lines).rstrip() + "\n"


def _extract_title(content: str) -> str:
    """Extract the # title line from summary content."""
    for line in content.splitlines():
        if line.startswith("# ") and not line.startswith("## "):
            return line
    return ""


# ---------------------------------------------------------------------------
# MemoryManager class — encapsulates instance_dir state
# ---------------------------------------------------------------------------

class MemoryManager:
    """Manages memory operations for a koan instance directory.

    Encapsulates the instance_dir path so callers don't need to thread it
    through every function call. All operations are relative to this directory.
    """

    def __init__(self, instance_dir: str):
        self.instance_dir = Path(instance_dir)
        self.memory_dir = self.instance_dir / "memory"
        self.journal_dir = self.instance_dir / "journal"
        self.summary_path = self.memory_dir / "summary.md"
        self.projects_dir = self.memory_dir / "projects"

    def _learnings_path(self, project_name: str) -> Path:
        return self.projects_dir / project_name / "learnings.md"

    def scoped_summary(self, project_name: str) -> str:
        """Return summary.md content filtered to sessions relevant to a project.

        A session is relevant if:
        - It explicitly mentions the project (projet: X)
        - It has no project hint (pre-multi-project sessions, kept for all)
        """
        if not self.summary_path.exists():
            return ""

        content = self.summary_path.read_text()
        sessions = parse_summary_sessions(content)
        title = _extract_title(content)

        filtered = []
        project_lower = project_name.lower()
        for date_header, text, project_hint in sessions:
            if not project_hint or project_hint == project_lower:
                filtered.append((date_header, text))

        return _rebuild_sessions(title, filtered)

    def compact_summary(self, max_sessions: int = 10) -> int:
        """Keep only the last N sessions in summary.md. Returns removed count."""
        if not self.summary_path.exists():
            return 0

        content = self.summary_path.read_text()
        sessions = parse_summary_sessions(content)

        if len(sessions) <= max_sessions:
            return 0

        title = _extract_title(content)
        kept = sessions[-max_sessions:]
        removed = len(sessions) - len(kept)

        atomic_write(self.summary_path, _rebuild_sessions(title, kept))
        return removed

    def cleanup_learnings(self, project_name: str) -> int:
        """Remove duplicate lines from a project's learnings.md. Returns removed count."""
        learnings_path = self._learnings_path(project_name)
        if not learnings_path.exists():
            return 0

        content = learnings_path.read_text()
        lines = content.splitlines()

        seen = set()
        new_lines = []
        removed = 0

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#") or stripped == "":
                new_lines.append(line)
                continue

            if stripped in seen:
                removed += 1
            else:
                seen.add(stripped)
                new_lines.append(line)

        if removed > 0:
            atomic_write(learnings_path, "\n".join(new_lines) + "\n")

        return removed

    def archive_journals(
        self,
        archive_after_days: int = 30,
        delete_after_days: int = 90,
    ) -> Dict[str, int]:
        """Archive old journal entries and delete very old raw journals.

        Strategy (3 tiers):
        - Recent (< archive_after_days): untouched
        - Mid-age (archive_after_days..delete_after_days): extract session digests
          into monthly archive files, then delete raw daily dirs
        - Old (> delete_after_days): delete raw daily dirs (archives kept forever)

        Returns dict with stats: archived_days, deleted_days, archive_lines.
        """
        if not self.journal_dir.exists():
            return {"archived_days": 0, "deleted_days": 0, "archive_lines": 0}

        today = date.today()
        archive_cutoff = today - timedelta(days=archive_after_days)
        delete_cutoff = today - timedelta(days=delete_after_days)

        archived_days = 0
        deleted_days = 0
        archive_lines = 0

        monthly: Dict[Tuple[str, str], List[str]] = defaultdict(list)

        for entry in sorted(self.journal_dir.iterdir()):
            name = entry.name
            date_str = name.replace(".md", "") if name.endswith(".md") else name

            try:
                entry_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                continue

            if entry_date >= archive_cutoff:
                continue

            month_key = entry_date.strftime("%Y-%m")

            if entry.is_dir():
                for md_file in sorted(entry.glob("*.md")):
                    project = md_file.stem
                    content = md_file.read_text()
                    digests = _extract_session_digest(content)
                    if digests:
                        monthly[(month_key, project)].extend(
                            [f"  {date_str}: {d}" for d in digests]
                        )
                        archive_lines += len(digests)

                if entry_date < delete_cutoff:
                    shutil.rmtree(entry)
                    deleted_days += 1
                else:
                    shutil.rmtree(entry)
                    archived_days += 1

            elif entry.is_file() and entry.suffix == ".md":
                content = entry.read_text()
                digests = _extract_session_digest(content)
                if digests:
                    monthly[(month_key, "legacy")].extend(
                        [f"  {date_str}: {d}" for d in digests]
                    )
                    archive_lines += len(digests)

                if entry_date < delete_cutoff:
                    entry.unlink()
                    deleted_days += 1
                else:
                    entry.unlink()
                    archived_days += 1

        archives_dir = self.journal_dir / "archives"
        for (month, project), lines in monthly.items():
            month_dir = archives_dir / month
            month_dir.mkdir(parents=True, exist_ok=True)
            archive_file = month_dir / f"{project}.md"

            existing = set()
            if archive_file.exists():
                existing = set(archive_file.read_text().splitlines())

            new_lines = [l for l in lines if l not in existing]
            if new_lines:
                with open(archive_file, "a", encoding="utf-8") as f:
                    if not existing:
                        f.write(f"# Journal archive — {project} — {month}\n\n")
                    f.write("\n".join(new_lines) + "\n")

        return {
            "archived_days": archived_days,
            "deleted_days": deleted_days,
            "archive_lines": archive_lines,
        }

    def cap_learnings(self, project_name: str, max_lines: int = 200) -> int:
        """Truncate a learnings file to keep only the most recent entries.

        Keeps: the # header, then the last max_lines content lines.
        Returns number of lines removed.
        """
        learnings_path = self._learnings_path(project_name)
        if not learnings_path.exists():
            return 0

        content = learnings_path.read_text()
        lines = content.splitlines()

        headers = []
        content_lines = []
        in_header = True
        for line in lines:
            if in_header and (line.startswith("#") or line.strip() == ""):
                headers.append(line)
            else:
                in_header = False
                content_lines.append(line)

        if len(content_lines) <= max_lines:
            return 0

        removed = len(content_lines) - max_lines
        kept = content_lines[-max_lines:]

        result = headers + [f"\n_(oldest {removed} entries archived)_\n"] + kept
        atomic_write(learnings_path, "\n".join(result) + "\n")
        return removed

    def run_cleanup(
        self,
        max_sessions: int = 15,
        archive_after_days: int = 30,
        delete_after_days: int = 90,
        max_learnings_lines: int = 200,
    ) -> dict:
        """Run all cleanup tasks. Returns stats dict."""
        stats = {}
        stats["summary_compacted"] = self.compact_summary(max_sessions)

        if self.projects_dir.exists():
            for project_dir in self.projects_dir.iterdir():
                if project_dir.is_dir():
                    name = project_dir.name
                    removed = self.cleanup_learnings(name)
                    if removed > 0:
                        stats[f"learnings_dedup_{name}"] = removed
                    capped = self.cap_learnings(name, max_learnings_lines)
                    if capped > 0:
                        stats[f"learnings_capped_{name}"] = capped

        journal_stats = self.archive_journals(archive_after_days, delete_after_days)
        stats.update(journal_stats)

        return stats


# ---------------------------------------------------------------------------
# Module-level functions (backward compatibility)
# ---------------------------------------------------------------------------

def scoped_summary(instance_dir: str, project_name: str) -> str:
    """Return summary.md content filtered to sessions relevant to a project."""
    return MemoryManager(instance_dir).scoped_summary(project_name)


def compact_summary(instance_dir: str, max_sessions: int = 10) -> int:
    """Keep only the last N sessions in summary.md. Returns removed count."""
    return MemoryManager(instance_dir).compact_summary(max_sessions)


def cleanup_learnings(instance_dir: str, project_name: str) -> int:
    """Remove duplicate lines from a project's learnings.md. Returns removed count."""
    return MemoryManager(instance_dir).cleanup_learnings(project_name)


def archive_journals(
    instance_dir: str,
    archive_after_days: int = 30,
    delete_after_days: int = 90,
) -> Dict[str, int]:
    """Archive old journal entries and delete very old raw journals."""
    return MemoryManager(instance_dir).archive_journals(archive_after_days, delete_after_days)


def cap_learnings(instance_dir: str, project_name: str, max_lines: int = 200) -> int:
    """Truncate a learnings file to keep only the most recent entries."""
    return MemoryManager(instance_dir).cap_learnings(project_name, max_lines)


def run_cleanup(
    instance_dir: str,
    max_sessions: int = 15,
    archive_after_days: int = 30,
    delete_after_days: int = 90,
    max_learnings_lines: int = 200,
) -> dict:
    """Run all cleanup tasks. Returns stats dict."""
    return MemoryManager(instance_dir).run_cleanup(
        max_sessions, archive_after_days, delete_after_days, max_learnings_lines
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(
            f"Usage: {sys.argv[0]} <instance_dir> <command> [args...]",
            file=sys.stderr,
        )
        print(
            "Commands: scoped-summary <project>, compact [max], "
            "cleanup-learnings <project>, archive-journals [days], cleanup",
            file=sys.stderr,
        )
        sys.exit(1)

    instance = sys.argv[1]
    command = sys.argv[2]
    mgr = MemoryManager(instance)

    if command == "scoped-summary":
        if len(sys.argv) < 4:
            print("Error: project name required", file=sys.stderr)
            sys.exit(1)
        print(mgr.scoped_summary(sys.argv[3]))

    elif command == "compact":
        max_s = int(sys.argv[3]) if len(sys.argv) > 3 else 15
        removed = mgr.compact_summary(max_s)
        print(f"Compacted: {removed} sessions removed")

    elif command == "cleanup-learnings":
        if len(sys.argv) < 4:
            print("Error: project name required", file=sys.stderr)
            sys.exit(1)
        removed = mgr.cleanup_learnings(sys.argv[3])
        print(f"Deduped: {removed} lines removed")

    elif command == "archive-journals":
        days = int(sys.argv[3]) if len(sys.argv) > 3 else 30
        stats = mgr.archive_journals(archive_after_days=days)
        for k, v in stats.items():
            print(f"  {k}: {v}")

    elif command == "cleanup":
        max_s = int(sys.argv[3]) if len(sys.argv) > 3 else 15
        stats = mgr.run_cleanup(max_s)
        for k, v in stats.items():
            print(f"  {k}: {v}")

    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)
