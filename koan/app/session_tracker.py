"""Kōan — Session outcome tracking for smarter autonomous decisions.

Records what each session accomplished and detects "stale" projects
where consecutive sessions produce no actionable work. This breaks the
pattern of 17 consecutive verification sessions by giving the agent
(and the iteration planner) concrete feedback on recent productivity.

Data is stored in instance/session_outcomes.json (append-only, capped).

Integration points:
- Write: mission_runner.run_post_mission() records after each session
- Read: deep_research.py injects staleness warnings into agent prompt
- Read: iteration_manager.py weights project selection by freshness
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# Maximum entries to keep in session_outcomes.json (rolling window)
MAX_OUTCOMES = 200

# Keywords that signal an empty/non-productive session
_EMPTY_KEYWORDS = [
    "verification session",
    "no code",
    "no new code",
    "waiting state",
    "legitimate waiting state",
    "blocked on merge",
    "housekeeping only",
    "no actionable work",
    "same state",
    "same pattern",
    "no code changes",
    "nothing actionable",
    "merge queue",
    "all work blocked",
    "pas de code",
    "identical session",
    "no changes needed",
]

# Strong productive signals — multi-word phrases that are unambiguous
_STRONG_PRODUCTIVE = [
    "branch pushed",
    "branch `koan/",
    "branch koan",
    "pr #",
    "pr created",
    "draft pr",
    "tests pass",
]

# Weaker productive signals — single words that need context
_WEAK_PRODUCTIVE_RE = re.compile(
    r"\b(?:implemented|refactored|migrated)\b", re.IGNORECASE
)


def classify_session(journal_content: str) -> str:
    """Classify a session as productive, empty, or blocked.

    Uses keyword matching on the journal/pending content to determine
    whether the session produced actionable output.

    Strategy: strong signals win immediately, weak signals are counted.
    Empty phrases are multi-word (specific), so they're reliable.
    Single-word productive signals ("added", "fixed") are too ambiguous
    and were removed in favor of strong multi-word patterns.

    Args:
        journal_content: The session's journal entry or pending.md content.

    Returns:
        "productive", "empty", or "blocked"
    """
    if not journal_content:
        return "empty"

    lower = journal_content.lower()

    # Strong productive signals — any one is conclusive
    if any(kw in lower for kw in _STRONG_PRODUCTIVE):
        return "productive"

    # Count empty signals (multi-word phrases, low false positive rate)
    empty_score = sum(1 for kw in _EMPTY_KEYWORDS if kw in lower)

    # Strong empty overrides everything (including blocked keywords)
    if empty_score >= 3:
        return "empty"

    # Blocked keywords with insufficient productive evidence → blocked
    has_blocked = (
        "blocked on merge" in lower
        or "merge queue" in lower
        or "all work blocked" in lower
    )
    weak_productive = len(_WEAK_PRODUCTIVE_RE.findall(lower))
    if has_blocked and weak_productive < 1:
        return "blocked"

    # Moderate empty signals → empty
    if empty_score >= 2:
        return "empty"

    # Weak productive signals → productive
    if weak_productive >= 1:
        return "productive"

    # Single empty signal → empty
    if empty_score >= 1:
        return "empty"

    # Default: benefit of the doubt
    return "productive"


def _extract_summary(journal_content: str, max_chars: int = 120) -> str:
    """Extract a brief summary from journal content.

    Looks for the first substantive line (not a header, not empty).
    """
    if not journal_content:
        return ""

    for line in journal_content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("---"):
            continue
        if line.startswith("Project:") or line.startswith("Started:"):
            continue
        if line.startswith("Run:") or line.startswith("Mode:"):
            continue
        # Found a content line
        if len(line) > max_chars:
            return line[:max_chars] + "..."
        return line

    return ""


def record_outcome(
    instance_dir: str,
    project: str,
    mode: str,
    duration_minutes: int,
    journal_content: str,
) -> dict:
    """Record a session outcome to session_outcomes.json.

    Args:
        instance_dir: Path to instance directory.
        project: Project name.
        mode: Autonomous mode (review/implement/deep).
        duration_minutes: Session duration in minutes.
        journal_content: The session's journal/pending content for classification.

    Returns:
        The recorded outcome dict.
    """
    outcome_type = classify_session(journal_content)
    summary = _extract_summary(journal_content)

    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "project": project,
        "mode": mode,
        "duration_minutes": duration_minutes,
        "outcome": outcome_type,
        "summary": summary,
    }

    outcomes_path = Path(instance_dir) / "session_outcomes.json"

    # Load existing outcomes
    outcomes = _load_outcomes(outcomes_path)

    # Append and cap
    outcomes.append(entry)
    if len(outcomes) > MAX_OUTCOMES:
        outcomes = outcomes[-MAX_OUTCOMES:]

    # Write atomically
    try:
        from app.utils import atomic_write
        atomic_write(outcomes_path, json.dumps(outcomes, indent=2))
    except Exception as e:
        print(f"[session_tracker] Failed to write outcomes: {e}", file=sys.stderr)

    return entry


def _load_outcomes(outcomes_path: Path) -> list:
    """Load outcomes from JSON file. Returns empty list on error."""
    if not outcomes_path.exists():
        return []
    try:
        data = json.loads(outcomes_path.read_text())
        if not isinstance(data, list):
            print(
                f"[session_tracker] Unexpected JSON type: {type(data).__name__}",
                file=sys.stderr,
            )
            return []
        return data
    except (json.JSONDecodeError, OSError) as e:
        print(f"[session_tracker] Failed to read outcomes: {e}", file=sys.stderr)
        return []


def get_recent_outcomes(
    instance_dir: str,
    project: str,
    limit: int = 10,
) -> List[dict]:
    """Get the last N outcomes for a project.

    Args:
        instance_dir: Path to instance directory.
        project: Project name to filter by.
        limit: Maximum number of outcomes to return.

    Returns:
        List of outcome dicts, most recent last.
    """
    outcomes_path = Path(instance_dir) / "session_outcomes.json"
    all_outcomes = _load_outcomes(outcomes_path)

    project_outcomes = [o for o in all_outcomes if o.get("project") == project]
    return project_outcomes[-limit:]


def get_staleness_score(instance_dir: str, project: str) -> int:
    """Count consecutive empty/blocked sessions for a project.

    Counts backwards from the most recent session. Stops at the first
    productive session.

    Args:
        instance_dir: Path to instance directory.
        project: Project name.

    Returns:
        Number of consecutive non-productive sessions. 0 = fresh.
    """
    recent = get_recent_outcomes(instance_dir, project, limit=20)
    if not recent:
        return 0

    count = 0
    for outcome in reversed(recent):
        if outcome.get("outcome") == "productive":
            break
        count += 1

    return count


def get_staleness_warning(instance_dir: str, project: str) -> str:
    """Generate a human-readable staleness warning if appropriate.

    Args:
        instance_dir: Path to instance directory.
        project: Project name.

    Returns:
        Warning string, or empty string if project is fresh.
    """
    score = get_staleness_score(instance_dir, project)
    if score < 3:
        return ""

    recent = get_recent_outcomes(instance_dir, project, limit=score + 1)
    empty_summaries = [
        o.get("summary", "")
        for o in recent[-score:]
        if o.get("outcome") != "productive"
    ]

    # Build contextual warning
    if score >= 5:
        intensity = "CRITICAL"
        advice = (
            "This project has had {score} consecutive non-productive sessions. "
            "STOP doing verification/housekeeping. Either:\n"
            "  1. Find genuinely NEW work (a bug, a missing feature, an architectural issue)\n"
            "  2. Skip this project entirely and work on something else\n"
            "  3. Write a strategic proposal or analysis that adds real value"
        )
    elif score >= 3:
        intensity = "WARNING"
        advice = (
            "Last {score} sessions found nothing actionable. "
            "Avoid repeating the same checks. Look for genuinely new work, "
            "or consider that this project may not need attention right now."
        )
    else:
        return ""

    lines = [
        f"### {intensity}: Project Staleness Detected",
        "",
        advice.format(score=score),
        "",
    ]

    if empty_summaries:
        lines.append("Recent non-productive sessions:")
        for s in empty_summaries[-3:]:  # Show last 3
            if s:
                lines.append(f"  - {s[:100]}")
        lines.append("")

    return "\n".join(lines)


def get_project_freshness(
    instance_dir: str,
    projects: List[Tuple[str, str]],
) -> Dict[str, int]:
    """Get freshness scores for all projects (for weighted selection).

    Returns a dict mapping project name to a weight (higher = fresher).
    Fresh projects get weight 10, stale projects get progressively less.
    Projects with staleness >= 5 get weight 1 (minimal chance).

    Args:
        instance_dir: Path to instance directory.
        projects: List of (name, path) tuples.

    Returns:
        Dict mapping project name to weight (1-10).
    """
    weights = {}
    for name, _ in projects:
        score = get_staleness_score(instance_dir, name)
        if score == 0:
            weights[name] = 10
        elif score == 1:
            weights[name] = 8
        elif score == 2:
            weights[name] = 6
        elif score <= 4:
            weights[name] = 3
        else:
            weights[name] = 1  # Heavily deprioritized

    return weights
