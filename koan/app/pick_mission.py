#!/usr/bin/env python3
"""
Kōan — Intelligent mission picker

Uses a lightweight Claude call to read missions.md and pick the highest-priority
pending mission, with multi-project awareness.

Usage:
    python3 pick_mission.py <instance_dir> <projects_str> <run_num> <autonomous_mode> [last_project]

Output (stdout):
    project_name:mission title    — if a mission is picked
    (empty)                       — if autonomous mode (no pending missions)

Falls back to naive extraction if Claude call fails.
"""

import json
import subprocess
import sys
from pathlib import Path

from app.cli_provider import build_full_command
from app.prompts import get_prompt_path
from app.config import get_model_config


PROMPT_TEMPLATE_PATH = get_prompt_path("pick-mission")


def build_prompt(
    missions_content: str,
    projects_str: str,
    run_num: str,
    max_runs: str,
    autonomous_mode: str,
    last_project: str,
) -> str:
    """Build the picker prompt from template + context."""
    template = PROMPT_TEMPLATE_PATH.read_text()
    return (
        template
        .replace("{MISSIONS_CONTENT}", missions_content)
        .replace("{PROJECTS}", projects_str)
        .replace("{RUN_NUM}", str(run_num))
        .replace("{MAX_RUNS}", max_runs)
        .replace("{AUTONOMOUS_MODE}", autonomous_mode)
        .replace("{LAST_PROJECT}", last_project)
    )


def call_claude(prompt: str) -> str:
    """Call Claude CLI with the picker prompt. Returns raw text output."""
    # Get KOAN_ROOT for proper working directory
    import os
    koan_root = os.environ.get("KOAN_ROOT", "")

    models = get_model_config()
    cmd = build_full_command(
        prompt=prompt,
        model=models["lightweight"],
        max_turns=1,
        output_format="json",
    )
    from app.cli_exec import run_cli

    result = run_cli(
        cmd,
        cwd=koan_root if koan_root else None,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        print(f"[pick_mission] Claude error (exit {result.returncode}): {result.stderr[:200]}", file=sys.stderr)
        return ""

    # Parse JSON output
    try:
        data = json.loads(result.stdout)
        return data.get("result", "") or data.get("content", "") or data.get("text", "") or ""
    except (json.JSONDecodeError, AttributeError):
        return result.stdout.strip()


def parse_picker_output(raw: str) -> tuple:
    """Parse Claude's response into (project_name, mission_title) or (None, None).

    Expected format: mission:<project>:<title>
    Or: autonomous
    """
    for line in raw.strip().splitlines():
        line = line.strip().strip("`")
        if line.startswith("mission:"):
            parts = line.split(":", 2)
            if len(parts) == 3:
                project = parts[1].strip()
                title = parts[2].strip()
                if project and title:
                    return (project, title)
        if line == "autonomous":
            return (None, None)
    return (None, None)


def fallback_extract(missions_path: Path, projects_str: str) -> tuple:
    """Naive fallback: first pending mission line (old behavior)."""
    from app.missions import extract_next_pending

    if not missions_path.exists():
        return (None, None)

    content = missions_path.read_text()
    line = extract_next_pending(content)
    if not line:
        return (None, None)

    # Try to extract project from inline tag
    import re
    tag = re.search(r"\[projec?t:([a-zA-Z0-9_-]+)\]", line)
    if tag:
        project = tag.group(1)
        title = re.sub(r"\[projec?t:[a-zA-Z0-9_-]+\]\s*", "", line).lstrip("- ").strip()
    else:
        # Default to first project
        parts = projects_str.split(";")
        project = parts[0].split(":")[0] if parts else "default"
        title = line.lstrip("- ").strip()

    return (project, title)


def pick_mission(
    instance_dir: str,
    projects_str: str,
    run_num: str,
    autonomous_mode: str,
    last_project: str = "",
) -> str:
    """Pick next mission. Returns 'project:title' or empty string."""
    instance = Path(instance_dir)
    missions_path = instance / "missions.md"

    if not missions_path.exists():
        return ""

    missions_content = missions_path.read_text()

    # Quick check: any pending missions at all?
    from app.missions import count_pending
    pending_count = count_pending(missions_content)
    if pending_count == 0:
        return ""

    # Smart picker: use naive fallback when Claude call isn't worth the cost
    # Only invoke Claude when there are multiple missions AND multiple projects
    num_projects = len([p for p in projects_str.split(";") if p.strip()]) if projects_str else 1
    if pending_count <= 2 or num_projects <= 1:
        print("[pick_mission] Simple case — using fast fallback (no Claude call)", file=sys.stderr)
        project, title = fallback_extract(missions_path, projects_str)
        if project and title:
            return f"{project}:{title}"
        return ""

    # Build prompt and call Claude
    prompt = build_prompt(
        missions_content=missions_content,
        projects_str=projects_str,
        run_num=run_num,
        max_runs="20",  # Reasonable default, not critical for picking
        autonomous_mode=autonomous_mode,
        last_project=last_project,
    )

    raw = call_claude(prompt)
    project, title = parse_picker_output(raw)

    # Fallback if Claude didn't return a usable answer
    if project is None:
        print("[pick_mission] Claude picker failed, using fallback", file=sys.stderr)
        project, title = fallback_extract(missions_path, projects_str)

    if project and title:
        return f"{project}:{title}"
    return ""


if __name__ == "__main__":
    if len(sys.argv) < 5:
        print(
            f"Usage: {sys.argv[0]} <instance_dir> <projects_str> <run_num> <autonomous_mode> [last_project]",
            file=sys.stderr,
        )
        sys.exit(1)

    instance_dir = sys.argv[1]
    projects_str = sys.argv[2]
    run_num = sys.argv[3]
    autonomous_mode = sys.argv[4]
    last_project = sys.argv[5] if len(sys.argv) > 5 else ""

    result = pick_mission(instance_dir, projects_str, run_num, autonomous_mode, last_project)
    print(result)
