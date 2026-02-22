"""Kōan stats skill — session outcome statistics per project."""

import json
from collections import Counter
from pathlib import Path


def handle(ctx):
    """Show session productivity stats, optionally filtered by project."""
    instance_dir = ctx.instance_dir
    project_filter = ctx.args.strip() if ctx.args else ""

    outcomes = _load_outcomes(instance_dir / "session_outcomes.json")
    if not outcomes:
        return "No session data yet. Stats will appear after the first completed run."

    if project_filter:
        filtered = [o for o in outcomes if o.get("project") == project_filter]
        if not filtered:
            known = sorted(set(o.get("project", "") for o in outcomes))
            return (
                f"No data for '{project_filter}'.\n"
                f"Known projects: {', '.join(known)}"
            )
        return _format_project_detail(project_filter, filtered)

    return _format_overview(outcomes)


def _load_outcomes(path: Path) -> list:
    """Load session outcomes from JSON file."""
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _format_overview(outcomes: list) -> str:
    """Format a cross-project overview."""
    by_project = {}
    for o in outcomes:
        project = o.get("project", "unknown")
        by_project.setdefault(project, []).append(o)

    total = len(outcomes)
    total_productive = sum(1 for o in outcomes if o.get("outcome") == "productive")
    total_empty = sum(1 for o in outcomes if o.get("outcome") == "empty")
    total_blocked = sum(1 for o in outcomes if o.get("outcome") == "blocked")

    pct = int(total_productive / max(1, total) * 100)

    lines = [
        "Session Stats",
        f"  Total: {total} sessions | {pct}% productive",
        f"  {total_productive} productive | {total_empty} empty | {total_blocked} blocked",
        "",
    ]

    # Per-project summary sorted by session count
    sorted_projects = sorted(by_project.items(), key=lambda x: -len(x[1]))
    for project, project_outcomes in sorted_projects:
        count = len(project_outcomes)
        productive = sum(1 for o in project_outcomes if o.get("outcome") == "productive")
        staleness = _consecutive_non_productive(project_outcomes)
        p_pct = int(productive / max(1, count) * 100)

        status = ""
        if staleness >= 5:
            status = " !!!"
        elif staleness >= 3:
            status = " !"

        lines.append(f"  {project}: {count} ({p_pct}% productive){status}")

    lines.append("")
    lines.append("Use /stats <project> for details.")

    return "\n".join(lines)


def _format_project_detail(project: str, outcomes: list) -> str:
    """Format detailed stats for a single project."""
    total = len(outcomes)
    productive = sum(1 for o in outcomes if o.get("outcome") == "productive")
    empty = sum(1 for o in outcomes if o.get("outcome") == "empty")
    blocked = sum(1 for o in outcomes if o.get("outcome") == "blocked")
    pct = int(productive / max(1, total) * 100)

    # Mode breakdown
    mode_counter = Counter(o.get("mode", "unknown") for o in outcomes)

    # Duration stats
    durations = [o.get("duration_minutes", 0) for o in outcomes if o.get("duration_minutes")]
    avg_duration = int(sum(durations) / max(1, len(durations))) if durations else 0

    # Staleness
    staleness = _consecutive_non_productive(outcomes)

    lines = [
        f"Stats: {project}",
        f"  Sessions: {total} | {pct}% productive",
        f"  {productive} productive | {empty} empty | {blocked} blocked",
    ]

    if staleness > 0:
        if staleness >= 5:
            lines.append(f"  Staleness: {staleness} consecutive non-productive")
        elif staleness >= 3:
            lines.append(f"  Staleness: {staleness} (approaching limit)")

    lines.append("")

    # Mode breakdown
    lines.append("By mode:")
    for mode in ("deep", "implement", "review", "wait"):
        count = mode_counter.get(mode, 0)
        if count > 0:
            mode_outcomes = [o for o in outcomes if o.get("mode") == mode]
            mode_productive = sum(1 for o in mode_outcomes if o.get("outcome") == "productive")
            lines.append(f"  {mode}: {count} ({mode_productive} productive)")

    # Show unknown modes if any
    for mode, count in mode_counter.items():
        if mode not in ("deep", "implement", "review", "wait") and count > 0:
            lines.append(f"  {mode}: {count}")

    if avg_duration > 0:
        lines.append(f"\nAvg duration: {avg_duration} min")

    # Last 5 sessions
    recent = outcomes[-5:]
    lines.append("\nRecent:")
    for o in reversed(recent):
        outcome = o.get("outcome", "?")
        mode = o.get("mode", "?")
        ts = o.get("timestamp", "?")
        if "T" in ts:
            ts = ts.split("T")[1][:5]
        summary = o.get("summary", "")
        if len(summary) > 50:
            summary = summary[:47] + "..."

        icon = "+" if outcome == "productive" else "-" if outcome == "empty" else "~"
        line = f"  {icon} {ts} [{mode}]"
        if summary:
            line += f" {summary}"
        lines.append(line)

    return "\n".join(lines)


def _consecutive_non_productive(outcomes: list) -> int:
    """Count consecutive non-productive sessions from the end."""
    count = 0
    for o in reversed(outcomes):
        if o.get("outcome") == "productive":
            break
        count += 1
    return count
