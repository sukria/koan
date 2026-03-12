"""Prompt audit module for Kōan.

Audits system and skill prompts for clarity, redundancy, staleness,
and effectiveness. Collects prompt metrics, reads recent signal data
from post-mission hooks, invokes Claude for structured analysis, and
writes findings to shared-journal.md.

Can be run standalone (python -m app.prompt_audit <instance_dir>)
or triggered as a recurring mission.

Signal collection: a companion hook (instance/hooks/prompt_audit_signals.py)
logs post-mission metadata to JSONL files for correlation analysis.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.prompts import load_prompt, PROMPT_DIR
from app.utils import atomic_write


# ---------------------------------------------------------------------------
# Prompt discovery
# ---------------------------------------------------------------------------

def discover_prompts(koan_root: Path) -> List[Dict]:
    """Discover all prompt files under koan/.

    Scans system-prompts/ and any skill prompt directories.

    Returns:
        List of dicts with keys: path, name, category, lines, words,
        sections, placeholders, last_modified.
    """
    results = []

    # System prompts
    sys_prompts_dir = koan_root / "koan" / "system-prompts"
    if sys_prompts_dir.is_dir():
        for f in sorted(sys_prompts_dir.glob("*.md")):
            results.append(_analyze_prompt_file(f, "system-prompt"))

    # Skill prompts (core)
    skills_dir = koan_root / "koan" / "skills" / "core"
    if skills_dir.is_dir():
        for prompt_file in sorted(skills_dir.rglob("prompts/*.md")):
            skill_name = prompt_file.parent.parent.name
            results.append(
                _analyze_prompt_file(prompt_file, f"skill/{skill_name}")
            )

    # Exclude the audit prompt itself to avoid meta-recursion
    results = [r for r in results if r["name"] != "prompt-audit"]

    return results


def _analyze_prompt_file(path: Path, category: str) -> Dict:
    """Compute metrics for a single prompt file."""
    content = path.read_text(encoding="utf-8")
    lines = content.splitlines()
    words = len(content.split())

    # Count markdown sections (## headers)
    sections = sum(1 for line in lines if re.match(r"^#{1,4}\s", line))

    # Count placeholders ({KEY})
    placeholders = len(re.findall(r"\{[A-Z_]+\}", content))

    # Last modified (from filesystem)
    mtime = datetime.fromtimestamp(os.path.getmtime(path))

    return {
        "path": str(path),
        "name": path.stem,
        "category": category,
        "lines": len(lines),
        "words": words,
        "sections": sections,
        "placeholders": placeholders,
        "last_modified": mtime.strftime("%Y-%m-%d"),
    }


# ---------------------------------------------------------------------------
# Signal data (from post-mission hooks)
# ---------------------------------------------------------------------------

def read_signals(instance_dir: Path, days: int = 7) -> List[Dict]:
    """Read recent prompt-audit signal data from JSONL files.

    Args:
        instance_dir: Path to instance directory
        days: Number of days to look back

    Returns:
        List of signal dicts from JSONL files
    """
    signals = []
    journal_dir = instance_dir / "journal"
    if not journal_dir.is_dir():
        return signals

    cutoff = datetime.now() - timedelta(days=days)

    for day_dir in sorted(journal_dir.iterdir()):
        if not day_dir.is_dir():
            continue
        # Parse date from directory name
        try:
            dir_date = datetime.strptime(day_dir.name, "%Y-%m-%d")
            if dir_date < cutoff:
                continue
        except ValueError:
            continue

        signal_file = day_dir / "prompt-audit-signals.jsonl"
        if not signal_file.exists():
            continue

        try:
            for line in signal_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    signals.append(json.loads(line))
        except (json.JSONDecodeError, OSError):
            continue

    return signals


def summarize_signals(signals: List[Dict]) -> str:
    """Create a human-readable summary of signal data.

    Args:
        signals: List of signal dicts from JSONL files

    Returns:
        Formatted summary string, or "No signal data available" if empty
    """
    if not signals:
        return "No signal data available (first audit run — no post-mission signals collected yet)."

    total = len(signals)
    successes = sum(1 for s in signals if s.get("exit_code") == 0)
    failures = total - successes

    # Average duration
    durations = [s.get("duration_minutes", 0) for s in signals if s.get("duration_minutes")]
    avg_duration = sum(durations) / len(durations) if durations else 0

    # Group by project
    by_project: Dict[str, int] = {}
    for s in signals:
        proj = s.get("project_name", "unknown")
        by_project[proj] = by_project.get(proj, 0) + 1

    # Group by mode
    by_mode: Dict[str, int] = {}
    for s in signals:
        mode = s.get("autonomous_mode", "unknown")
        by_mode[mode] = by_mode.get(mode, 0) + 1

    lines = [
        f"Signal summary (last 7 days, {total} missions):",
        f"- Success rate: {successes}/{total} ({100 * successes // total if total else 0}%)",
        f"- Average duration: {avg_duration:.1f} min",
        f"- By project: {', '.join(f'{k}: {v}' for k, v in sorted(by_project.items()))}",
        f"- By mode: {', '.join(f'{k}: {v}' for k, v in sorted(by_mode.items()))}",
    ]

    if failures > 0:
        # Show recent failure titles
        failed = [s.get("mission_title", "?") for s in signals if s.get("exit_code") != 0]
        lines.append(f"- Recent failures: {', '.join(failed[-5:])}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Prompt list formatting
# ---------------------------------------------------------------------------

def format_prompt_list(prompts: List[Dict], max_prompts: int = 10) -> str:
    """Format prompt metadata for inclusion in the audit prompt.

    Args:
        prompts: List of prompt metadata dicts
        max_prompts: Maximum number of prompts to include (sample if more)

    Returns:
        Formatted string listing prompts with metrics
    """
    # Sample if too many — prioritize system prompts and recently modified
    if len(prompts) > max_prompts:
        # Always include all system prompts, sample from skills
        system = [p for p in prompts if p["category"] == "system-prompt"]
        skills = [p for p in prompts if p["category"] != "system-prompt"]
        remaining = max_prompts - len(system)
        if remaining > 0 and skills:
            # Sort by last_modified descending, take most recent
            skills.sort(key=lambda p: p["last_modified"], reverse=True)
            prompts = system + skills[:remaining]
        else:
            prompts = system[:max_prompts]

    lines = []
    for p in prompts:
        lines.append(
            f"- **{p['name']}** ({p['category']}) — "
            f"{p['lines']} lines, {p['words']} words, "
            f"{p['sections']} sections, {p['placeholders']} placeholders, "
            f"modified: {p['last_modified']}"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Audit execution
# ---------------------------------------------------------------------------

def build_audit_prompt(
    prompts: List[Dict],
    signals: List[Dict],
    prompt_contents: Optional[Dict[str, str]] = None,
) -> str:
    """Build the full audit prompt from template + context.

    Args:
        prompts: List of prompt metadata dicts
        signals: List of signal dicts
        prompt_contents: Optional dict of prompt name -> content (for inline review)

    Returns:
        Complete audit prompt string
    """
    prompt_list = format_prompt_list(prompts)
    signal_summary = summarize_signals(signals)

    # Include actual prompt contents for review (truncated)
    content_section = ""
    if prompt_contents:
        parts = []
        for name, content in prompt_contents.items():
            # Truncate to first 200 lines to control token cost
            lines = content.splitlines()[:200]
            truncated = "\n".join(lines)
            if len(content.splitlines()) > 200:
                truncated += "\n... (truncated)"
            parts.append(f"### {name}\n```\n{truncated}\n```")
        content_section = "\n\n".join(parts)

    return load_prompt(
        "prompt-audit",
        PROMPT_LIST=prompt_list,
        SIGNAL_SUMMARY=signal_summary,
        PROMPT_CONTENTS=content_section,
    )


def run_audit(
    koan_root: Path,
    instance_dir: Path,
    max_prompts: int = 10,
    max_turns: int = 3,
    timeout: int = 120,
) -> Tuple[str, List[Dict]]:
    """Run the full prompt audit pipeline.

    Args:
        koan_root: Path to koan root directory
        instance_dir: Path to instance directory
        max_prompts: Maximum prompts to audit per run
        max_turns: Claude conversation turn limit
        timeout: Subprocess timeout in seconds

    Returns:
        Tuple of (audit report string, list of prompt metadata)
    """
    # 1. Discover prompts
    prompts = discover_prompts(koan_root)
    if not prompts:
        return "No prompt files found to audit.", []

    # 2. Read signal data
    signals = read_signals(instance_dir)

    # 3. Load prompt contents for selected prompts
    selected = prompts[:max_prompts]
    prompt_contents = {}
    for p in selected:
        try:
            prompt_contents[p["name"]] = Path(p["path"]).read_text(encoding="utf-8")
        except OSError:
            continue

    # 4. Build audit prompt
    audit_prompt = build_audit_prompt(selected, signals, prompt_contents)

    # 5. Invoke Claude
    try:
        from app.cli_provider import build_full_command
        cmd = build_full_command(
            prompt=audit_prompt,
            max_turns=max_turns,
        )
        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip(), prompts
        else:
            err = result.stderr[:300] if result.stderr else "no output"
            return f"Audit failed (exit {result.returncode}): {err}", prompts
    except subprocess.TimeoutExpired:
        return "Audit timed out — try reducing max_prompts.", prompts
    except Exception as e:
        return f"Audit error: {e}", prompts


# ---------------------------------------------------------------------------
# Report writing
# ---------------------------------------------------------------------------

def save_audit_report(instance_dir: Path, report: str) -> Path:
    """Save audit report to shared-journal.md.

    Args:
        instance_dir: Path to instance directory
        report: Audit report content

    Returns:
        Path to the shared-journal.md file
    """
    journal_path = instance_dir / "shared-journal.md"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    entry = f"\n\n## Prompt Audit — {timestamp}\n\n{report}\n"

    existing = journal_path.read_text(encoding="utf-8") if journal_path.exists() else ""
    atomic_write(journal_path, existing + entry)

    return journal_path


def extract_actionable_findings(report: str) -> List[Dict]:
    """Extract action-level findings from audit report for issue creation.

    Parses lines containing severity markers (🔴 action, 🟡 warning, 🔵 info).

    Args:
        report: Audit report text

    Returns:
        List of dicts with keys: severity, description
    """
    findings = []
    severity_map = {
        "🔴": "action",
        "🟡": "warning",
        "🔵": "info",
        "action": "action",
        "warning": "warning",
        "info": "info",
    }

    for line in report.splitlines():
        line = line.strip()
        if not line:
            continue
        for marker, severity in severity_map.items():
            if marker in line.lower() or line.startswith(marker):
                findings.append({
                    "severity": severity,
                    "description": line.lstrip("- ").strip(),
                })
                break

    return findings


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    """CLI entry point: python -m app.prompt_audit <instance_dir> [--notify] [--create-issues]"""
    if len(sys.argv) < 2:
        print(
            "Usage: prompt_audit.py <instance_dir> [--notify] [--create-issues]",
            file=sys.stderr,
        )
        sys.exit(1)

    instance_dir = Path(sys.argv[1])
    notify = "--notify" in sys.argv
    create_issues = "--create-issues" in sys.argv

    if not instance_dir.exists():
        print(f"[prompt_audit] Instance directory not found: {instance_dir}", file=sys.stderr)
        sys.exit(1)

    koan_root = Path(os.environ.get("KOAN_ROOT", ""))
    if not koan_root.is_dir():
        print("[prompt_audit] KOAN_ROOT not set or invalid.", file=sys.stderr)
        sys.exit(1)

    print("[prompt_audit] Starting prompt audit...")
    report, prompts = run_audit(koan_root, instance_dir)

    if report:
        save_audit_report(instance_dir, report)
        print(f"[prompt_audit] Report saved to shared-journal.md ({len(prompts)} prompts audited)")
        print(report)

        if notify:
            outbox_path = instance_dir / "outbox.md"
            # Truncate for Telegram
            summary = report[:500] + ("..." if len(report) > 500 else "")
            message = f"📋 Prompt audit complete — {len(prompts)} prompts reviewed.\n\n{summary}"
            atomic_write(outbox_path, message)
            print("[prompt_audit] Notification sent to outbox")

        if create_issues:
            findings = extract_actionable_findings(report)
            action_items = [f for f in findings if f["severity"] == "action"]
            if action_items:
                print(f"[prompt_audit] {len(action_items)} actionable findings found.")
                _create_github_issue(action_items)
            else:
                print("[prompt_audit] No actionable findings — skipping issue creation.")
    else:
        print("[prompt_audit] No report generated.")


def _create_github_issue(findings: List[Dict]) -> None:
    """Create a GitHub issue from actionable findings.

    Args:
        findings: List of finding dicts with severity and description
    """
    today = datetime.now().strftime("%Y-%m-%d")
    items = "\n".join(f"- [ ] {f['description']}" for f in findings)
    body = f"""## Prompt Audit Findings — {today}

The automated prompt audit found {len(findings)} actionable items:

### Action Items
{items}

### Details
See shared-journal.md for the full audit report.

---
🤖 Created by Kōan from prompt audit session
"""
    try:
        result = subprocess.run(
            ["gh", "issue", "create",
             "--title", f"Prompt audit: {len(findings)} actionable findings ({today})",
             "--body", body],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if result.returncode == 0:
            print(f"[prompt_audit] GitHub issue created: {result.stdout.strip()}")
        else:
            print(f"[prompt_audit] Failed to create issue: {result.stderr[:200]}", file=sys.stderr)
    except Exception as e:
        print(f"[prompt_audit] Issue creation error: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
