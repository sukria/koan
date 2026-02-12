"""Kōan status skill — consolidates /status, /ping, /usage."""

import re

# Telegram lines wrap around 40-45 chars on mobile; 60 is a good balance
# between readability and information density.
_MAX_MISSION_DISPLAY_LEN = 60


def _truncate(text: str, max_len: int = _MAX_MISSION_DISPLAY_LEN) -> str:
    """Truncate mission text for Telegram display, adding ellipsis if needed."""
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip() + "…"


def _needs_ollama() -> bool:
    """Return True if the configured provider requires ollama serve."""
    try:
        from app.provider import get_provider_name
        return get_provider_name() in ("local", "ollama")
    except Exception:
        return False


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

    parts = ["Kōan Status"]

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

    # Show focus mode if active
    try:
        from app.focus_manager import check_focus
        focus_state = check_focus(str(koan_root))
        if focus_state:
            parts.append(f"  🎯 Focus: missions only ({focus_state.remaining_display()} remaining)")
    except Exception:
        pass

    # Show process health when ollama is needed
    if _needs_ollama():
        from app.pid_manager import check_pidfile
        ollama_pid = check_pidfile(koan_root, "ollama")
        if ollama_pid:
            parts.append(f"  🦙 Ollama: running (PID {ollama_pid})")
        else:
            parts.append(f"  🦙 Ollama: not running")

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
                            parts.append(f"    {_truncate(display)}")
                    if pending:
                        parts.append(f"  Pending: {len(pending)}")
                        for m in pending[:3]:
                            display = re.sub(r'\[projec?t:[a-zA-Z0-9_-]+\]\s*', '', m)
                            parts.append(f"    {_truncate(display)}")

    return "\n".join(parts)


def _handle_ping(ctx) -> str:
    """Check if run and awake processes are alive using PID files."""
    from app.pid_manager import check_pidfile

    koan_root = ctx.koan_root
    run_pid = check_pidfile(koan_root, "run")
    awake_pid = check_pidfile(koan_root, "awake")

    pause_file = koan_root / ".koan-pause"
    stop_file = koan_root / ".koan-stop"

    lines = []

    # --- Runner status ---
    if run_pid:
        if stop_file.exists():
            lines.append(f"⏹️ Runner: stopping (PID {run_pid})")
        elif pause_file.exists():
            lines.append(f"⏸️ Runner: paused (PID {run_pid})")
            lines.append("  /resume to unpause")
        else:
            status_file = koan_root / ".koan-status"
            loop_status = ""
            if status_file.exists():
                loop_status = status_file.read_text().strip()
            if loop_status:
                lines.append(f"✅ Runner: {loop_status} (PID {run_pid})")
            else:
                lines.append(f"✅ Runner: alive (PID {run_pid})")
    else:
        lines.append("❌ Runner: not running")
        lines.append("  make run &")

    # --- Bridge status ---
    if awake_pid:
        lines.append(f"✅ Bridge: alive (PID {awake_pid})")
    else:
        lines.append("❌ Bridge: not running")
        lines.append("  make awake &")

    # --- Ollama status (only for local/ollama providers) ---
    if _needs_ollama():
        ollama_pid = check_pidfile(koan_root, "ollama")
        if ollama_pid:
            lines.append(f"✅ Ollama: alive (PID {ollama_pid})")
        else:
            lines.append("❌ Ollama: not running")
            lines.append("  ollama serve &")

    return "\n".join(lines)


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
