"""Koan status skill — consolidates /status, /ping, /usage."""

import re
import subprocess
from pathlib import Path


def handle(ctx):
    """Dispatch to the appropriate subcommand."""
    cmd = ctx.command_name
    if cmd == "ping":
        return _handle_ping(ctx)
    elif cmd == "usage":
        return _handle_usage(ctx)
    else:
        return _handle_status(ctx)


def _handle_status(ctx) -> str:
    """Build status message grouped by project."""
    from app.missions import group_by_project

    koan_root = ctx.koan_root
    instance_dir = ctx.instance_dir
    missions_file = instance_dir / "missions.md"

    parts = ["Koan Status"]

    pause_file = koan_root / ".koan-pause"
    stop_file = koan_root / ".koan-stop"
    pause_reason_file = koan_root / ".koan-pause-reason"

    if stop_file.exists():
        parts.append("\n⛔ Mode: Stopping")
    elif pause_file.exists():
        reason = ""
        if pause_reason_file.exists():
            reason = pause_reason_file.read_text().strip().split("\n")[0]
        if reason == "quota":
            parts.append("\n⏸️ Mode: Paused (quota exhausted)")
        elif reason == "max_runs":
            parts.append("\n⏸️ Mode: Paused (max runs reached)")
        else:
            parts.append("\n⏸️ Mode: Paused")
        parts.append("  /resume to unpause")
    else:
        parts.append("\n🟢 Mode: Working")

    status_file = koan_root / ".koan-status"
    if status_file.exists():
        loop_status = status_file.read_text().strip()
        if loop_status:
            parts.append(f"  Loop: {loop_status}")

    if missions_file.exists():
        content = missions_file.read_text()
        missions_by_project = group_by_project(content)

        if missions_by_project:
            for project in sorted(missions_by_project.keys()):
                missions = missions_by_project[project]
                pending = missions["pending"]
                in_progress = missions["in_progress"]

                if pending or in_progress:
                    parts.append(f"\n{project}")
                    if in_progress:
                        parts.append(f"  In progress: {len(in_progress)}")
                        for m in in_progress[:2]:
                            display = re.sub(r'\[projec?t:[a-zA-Z0-9_-]+\]\s*', '', m)
                            parts.append(f"    {display}")
                    if pending:
                        parts.append(f"  Pending: {len(pending)}")
                        for m in pending[:3]:
                            display = re.sub(r'\[projec?t:[a-zA-Z0-9_-]+\]\s*', '', m)
                            parts.append(f"    {display}")

    return "\n".join(parts)


def _handle_ping(ctx) -> str:
    """Check if the run loop is alive."""
    koan_root = ctx.koan_root

    try:
        result = subprocess.run(
            ["pgrep", "-f", "run\\.sh"],
            capture_output=True, text=True, timeout=5,
        )
        run_loop_alive = result.returncode == 0
    except Exception:
        run_loop_alive = False

    pause_file = koan_root / ".koan-pause"
    stop_file = koan_root / ".koan-stop"

    if run_loop_alive and stop_file.exists():
        return "⏹️ Run loop is stopping after current mission."
    elif run_loop_alive and pause_file.exists():
        return "⏸️ Run loop is paused. /resume to unpause."
    elif run_loop_alive:
        status_file = koan_root / ".koan-status"
        if status_file.exists():
            loop_status = status_file.read_text().strip()
            if loop_status:
                return f"✅ OK — {loop_status}"
        return "✅ OK"
    else:
        return "❌ Run loop is not running.\n\nTo restart:\n  make run &"


def _handle_usage(ctx) -> str:
    """Build usage status. Returns raw data for the caller to format."""
    instance_dir = ctx.instance_dir
    missions_file = instance_dir / "missions.md"

    usage_text = "No quota data available."
    usage_path = instance_dir / "usage.md"
    if usage_path.exists():
        usage_text = usage_path.read_text().strip() or usage_text

    missions_text = "No missions."
    if missions_file.exists():
        from app.missions import parse_sections
        sections = parse_sections(missions_file.read_text())
        parts = []
        in_progress = sections.get("in_progress", [])
        pending = sections.get("pending", [])
        done = sections.get("done", [])
        if in_progress:
            parts.append("In progress:\n" + "\n".join(in_progress[:5]))
        if pending:
            parts.append(f"Pending ({len(pending)}):\n" + "\n".join(pending[:5]))
        if done:
            parts.append(f"Done: {len(done)}")
        if parts:
            missions_text = "\n\n".join(parts)

    pending_text = "No run in progress."
    pending_path = instance_dir / "journal" / "pending.md"
    if pending_path.exists():
        content = pending_path.read_text().strip()
        if content:
            if len(content) > 1500:
                pending_text = "...\n" + content[-1500:]
            else:
                pending_text = content

    return f"Quota:\n{usage_text}\n\nMissions:\n{missions_text}\n\nCurrent:\n{pending_text}"
