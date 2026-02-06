"""Telegram command handlers — extracted from awake.py for maintainability.

Each handler corresponds to a /command. Pure functions that read shared state
from bridge_state and send responses via send_telegram.
"""

import fcntl
import re
import subprocess
import time
from datetime import datetime

from app.bridge_state import (
    KOAN_ROOT,
    INSTANCE_DIR,
    MISSIONS_FILE,
    SOUL,
    TELEGRAM_HISTORY_FILE,
)
from app.notify import send_telegram
from app.utils import (
    get_known_projects,
    insert_pending_mission,
    parse_project,
    save_telegram_message,
    get_fast_reply_model,
)


def handle_command(text: str, run_in_worker, handle_chat):
    """Handle /commands locally — no Claude needed.

    Args:
        text: The raw command text from Telegram.
        run_in_worker: Callback to run a function in a worker thread.
        handle_chat: Callback to handle a chat message (for unknown commands).
    """
    cmd = text.strip().lower()

    # /chat forces chat mode — bypass mission classification
    if cmd.startswith("/chat"):
        chat_text = text[5:].strip()
        if not chat_text:
            send_telegram("Usage: /chat <message>\nForces chat mode for messages that look like missions.")
            return
        run_in_worker(handle_chat, chat_text)
        return

    if cmd == "/stop":
        (KOAN_ROOT / ".koan-stop").write_text("STOP")
        send_telegram("Stop requested. Current mission will complete, then Kōan will stop.")
        return

    if cmd == "/pause":
        pause_file = KOAN_ROOT / ".koan-pause"
        if pause_file.exists():
            send_telegram("Already paused. /resume to unpause.")
        else:
            pause_file.write_text("PAUSE")
            send_telegram("Paused. No missions will run. /resume to unpause.")
        return

    if cmd == "/status":
        status = build_status()
        send_telegram(status)
        return

    if cmd == "/resume":
        handle_resume()
        return

    if cmd == "/verbose":
        verbose_file = KOAN_ROOT / ".koan-verbose"
        verbose_file.write_text("VERBOSE")
        send_telegram("Verbose mode ON. I'll send you each progress update.")
        return

    if cmd == "/silent":
        verbose_file = KOAN_ROOT / ".koan-verbose"
        if verbose_file.exists():
            verbose_file.unlink()
            send_telegram("Verbose mode OFF. Silent until conclusion.")
        else:
            send_telegram("Already in silent mode.")
        return

    if cmd == "/sparring":
        handle_sparring()
        return

    if cmd.startswith("/reflect "):
        handle_reflect(text[9:].strip())
        return

    if cmd == "/ping":
        handle_ping()
        return

    if cmd.startswith("/log") or cmd.startswith("/journal"):
        # Extract args after command name
        if cmd.startswith("/journal"):
            args = text[8:].strip()
        else:
            args = text[4:].strip()
        handle_log(args)
        return

    if cmd == "/help":
        handle_help()
        return

    if cmd == "/usage":
        run_in_worker(handle_usage)
        return

    if cmd.startswith("/mission"):
        handle_mission_command(text)
        return

    # Unknown command — pass to Claude as chat
    handle_chat(text)


def build_status() -> str:
    """Build status message grouped by project."""
    from app.missions import group_by_project

    parts = ["\U0001f4ca Kōan Status"]

    # Run loop status — FIRST, most important info
    pause_file = KOAN_ROOT / ".koan-pause"
    stop_file = KOAN_ROOT / ".koan-stop"

    if pause_file.exists():
        parts.append("\n\u23f8\ufe0f **PAUSED** — No missions being executed")
        parts.append("   /resume to continue")
    elif stop_file.exists():
        parts.append("\n\u26d4 **STOP REQUESTED** — Finishing current work")
    else:
        parts.append("\n\u25b6\ufe0f **ACTIVE** — Run loop running")

    status_file = KOAN_ROOT / ".koan-status"
    if status_file.exists():
        parts.append(f"   Loop: {status_file.read_text().strip()}")

    # Parse missions by project
    if MISSIONS_FILE.exists():
        content = MISSIONS_FILE.read_text()
        missions_by_project = group_by_project(content)

        if missions_by_project:
            for project in sorted(missions_by_project.keys()):
                missions = missions_by_project[project]
                pending = missions["pending"]
                in_progress = missions["in_progress"]

                if pending or in_progress:
                    parts.append(f"\n**{project}**")
                    if in_progress:
                        parts.append(f"  In progress: {len(in_progress)}")
                        for m in in_progress[:2]:
                            # Remove project tag from display
                            display = re.sub(r'\[projec?t:[a-zA-Z0-9_-]+\]\s*', '', m)
                            parts.append(f"    {display}")
                    if pending:
                        parts.append(f"  Pending: {len(pending)}")
                        for m in pending[:3]:
                            display = re.sub(r'\[projec?t:[a-zA-Z0-9_-]+\]\s*', '', m)
                            parts.append(f"    {display}")

    return "\n".join(parts)


def handle_ping():
    """Check if the run loop (make run) is alive and report status."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "run\\.sh"],
            capture_output=True, text=True, timeout=5,
        )
        run_loop_alive = result.returncode == 0
    except Exception:
        run_loop_alive = False

    pause_file = KOAN_ROOT / ".koan-pause"
    stop_file = KOAN_ROOT / ".koan-stop"

    if run_loop_alive and stop_file.exists():
        send_telegram("\u26d4 Run loop is stopping after current mission.")
    elif run_loop_alive and pause_file.exists():
        send_telegram("\u23f8\ufe0f Run loop is paused. /resume to unpause.")
    elif run_loop_alive:
        send_telegram("\u2705")
    else:
        send_telegram("\u274c Run loop is not running.\n\nTo restart:\n  make run &")


def handle_log(args: str):
    """Show the latest journal entry for a project.

    Usage:
        /log              — today's journal (all projects)
        /log koan         — today's journal for project koan
        /log koan yesterday — yesterday's journal for koan
        /log koan 2026-02-03 — specific date
    """
    from datetime import date as _date, timedelta
    from app.utils import get_latest_journal

    parts = args.split() if args else []
    project = None
    target_date = None

    if len(parts) >= 1:
        # First arg: project name (unless it looks like a date)
        if re.match(r'^\d{4}-\d{2}-\d{2}$', parts[0]):
            target_date = parts[0]
        elif parts[0] == "yesterday":
            target_date = (_date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            project = parts[0]

    if len(parts) >= 2 and target_date is None:
        # Second arg: date
        if parts[1] == "yesterday":
            target_date = (_date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        elif re.match(r'^\d{4}-\d{2}-\d{2}$', parts[1]):
            target_date = parts[1]

    result = get_latest_journal(INSTANCE_DIR, project=project, target_date=target_date)
    send_telegram(result)


def handle_help():
    """Send the list of available commands."""
    help_text = (
        "Kōan — Commands\n"
        "\n"
        "CONTROL\n"
        "/pause — pause (no new missions)\n"
        "/resume — resume after pause or quota exhausted\n"
        "/stop — stop Kōan after current mission\n"
        "\n"
        "MONITORING\n"
        "/status — quick status (missions, pause, loop)\n"
        "/usage — detailed status (quota, progress)\n"
        "/log [project] [date] — latest journal entry\n"
        "/ping — check if run loop is alive (✅/❌)\n"
        "/verbose — receive every progress update\n"
        "/silent — mute updates (default mode)\n"
        "\n"
        "INTERACTION\n"
        "/chat <msg> — force chat mode (bypass mission detection)\n"
        "/sparring — start a strategic sparring session\n"
        "/reflect <text> — note a reflection in the shared journal\n"
        "/help — this help\n"
        "\n"
        "MISSIONS\n"
        "/mission <desc> — create a mission (asks for project if ambiguous)\n"
        '"mission:" prefix or an action verb:\n'
        "  fix the login bug\n"
        "  implement dark mode\n"
        "  mission: refactor the auth module\n"
        "\n"
        "To target a project:\n"
        "  /mission [project:koan] fix the login bug\n"
        "  [project:koan] fix the login bug\n"
        "\n"
        "To force chat: /chat <message> (useful when your message looks like a mission)\n"
        "\n"
        "Any other message = free conversation."
    )
    send_telegram(help_text)


def handle_usage():
    """Build a rich status from usage.md + missions.md + pending.md, formatted by Claude."""
    # Gather raw data
    usage_text = "No quota data available."
    usage_path = INSTANCE_DIR / "usage.md"
    if usage_path.exists():
        usage_text = usage_path.read_text().strip() or usage_text

    missions_text = "No missions."
    if MISSIONS_FILE.exists():
        from app.missions import parse_sections
        sections = parse_sections(MISSIONS_FILE.read_text())
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
    pending_path = INSTANCE_DIR / "journal" / "pending.md"
    if pending_path.exists():
        content = pending_path.read_text().strip()
        if content:
            # Keep last 1500 chars
            if len(content) > 1500:
                pending_text = "...\n" + content[-1500:]
            else:
                pending_text = content

    from app.prompts import load_prompt
    prompt = load_prompt(
        "usage-status",
        SOUL=SOUL,
        USAGE=usage_text,
        MISSIONS=missions_text,
        PENDING=pending_text,
    )

    try:
        # Use fast_reply model (lightweight/Haiku) if configured
        fast_model = get_fast_reply_model()
        cmd = ["claude", "-p", prompt, "--max-turns", "1"]
        if fast_model:
            cmd.extend(["--model", fast_model])
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0 and result.stdout.strip():
            response = result.stdout.strip()
            # Clean markdown artifacts
            response = response.replace("**", "").replace("```", "").replace("##", "")
            response = re.sub(r'^#{1,6}\s+', '', response, flags=re.MULTILINE)
            send_telegram(response)
        else:
            if result.stderr:
                print(f"[awake] /usage Claude stderr: {result.stderr[:500]}")
            # Fallback: send raw data
            fallback = f"Quota: {usage_text[:200]}\n\nMissions: {missions_text[:300]}"
            send_telegram(fallback)
    except subprocess.TimeoutExpired:
        send_telegram("Timeout formatting /usage. Try again.")
    except Exception as e:
        print(f"[awake] Usage error: {e}")
        send_telegram("Error formatting /usage.")


def handle_resume():
    """Resume from pause or quota exhaustion."""
    pause_file = KOAN_ROOT / ".koan-pause"
    pause_reason_file = KOAN_ROOT / ".koan-pause-reason"
    quota_file = KOAN_ROOT / ".koan-quota-reset"  # Legacy, kept for compat

    if pause_file.exists():
        # Read pause reason and reset info for better messaging
        reason = "manual"
        reset_timestamp = None
        reset_display = ""

        if pause_reason_file.exists():
            lines = pause_reason_file.read_text().strip().split("\n")
            reason = lines[0] if lines else "manual"
            if len(lines) > 1:
                try:
                    reset_timestamp = int(lines[1])
                except ValueError:
                    pass
            if len(lines) > 2:
                reset_display = lines[2]

        pause_file.unlink(missing_ok=True)
        pause_reason_file.unlink(missing_ok=True)

        if reason == "quota":
            # Check if we're resuming before the reset time
            if reset_timestamp and time.time() < reset_timestamp:
                from app.reset_parser import time_until_reset
                remaining = time_until_reset(reset_timestamp)
                send_telegram(f"Unpaused (was: quota exhausted). Note: reset is in ~{remaining}. Run loop continues anyway.")
            else:
                send_telegram("Unpaused (was: quota exhausted). Quota should be reset. Run loop continues.")
        elif reason == "max_runs":
            send_telegram("Unpaused (was: max_runs). Run counter reset, loop continues.")
        else:
            send_telegram("Unpaused. Missions resume next cycle.")
        return

    # Legacy fallback: old .koan-quota-reset file (can be removed in future)
    if not quota_file.exists():
        send_telegram("No pause or quota hold detected. /status to check.")
        return

    try:
        lines = quota_file.read_text().strip().split("\n")
        reset_info = lines[0] if lines else "unknown time"
        paused_at = int(lines[1]) if len(lines) > 1 else 0

        hours_since_pause = (time.time() - paused_at) / 3600
        likely_reset = hours_since_pause >= 2

        if likely_reset:
            quota_file.unlink(missing_ok=True)
            send_telegram(f"Quota likely reset ({reset_info}, paused {hours_since_pause:.1f}h ago). Restart with: make run")
        else:
            send_telegram(f"Quota not reset yet ({reset_info}). Paused {hours_since_pause:.1f}h ago. Check back later.")
    except Exception as e:
        print(f"[awake] Error checking quota reset: {e}")
        send_telegram("Error checking quota. /status or check manually.")


def handle_sparring():
    """Launch a sparring session — strategic challenge, not code talk."""
    send_telegram("Sparring mode activated. I'm thinking...")

    from app.prompts import load_prompt

    # Load context for strategic sparring
    strategy = ""
    strategy_file = INSTANCE_DIR / "memory" / "global" / "strategy.md"
    if strategy_file.exists():
        strategy = strategy_file.read_text()

    emotional = ""
    emotional_file = INSTANCE_DIR / "memory" / "global" / "emotional-memory.md"
    if emotional_file.exists():
        emotional = emotional_file.read_text()[:1000]

    prefs = ""
    prefs_file = INSTANCE_DIR / "memory" / "global" / "human-preferences.md"
    if prefs_file.exists():
        prefs = prefs_file.read_text()

    # Recent missions for context
    recent_missions = ""
    if MISSIONS_FILE.exists():
        from app.missions import parse_sections
        sections = parse_sections(MISSIONS_FILE.read_text())
        in_progress = sections.get("in_progress", [])
        pending = sections.get("pending", [])
        parts = []
        if in_progress:
            parts.append("In progress:\n" + "\n".join(in_progress[:5]))
        if pending:
            parts.append("Pending:\n" + "\n".join(pending[:5]))
        recent_missions = "\n".join(parts)

    hour = datetime.now().hour
    time_hint = "It's late night." if hour >= 22 else "It's evening." if hour >= 18 else "It's afternoon." if hour >= 12 else "It's morning."

    prompt = load_prompt(
        "sparring",
        SOUL=SOUL,
        PREFS=prefs,
        STRATEGY=strategy,
        EMOTIONAL_MEMORY=emotional,
        RECENT_MISSIONS=recent_missions,
        TIME_HINT=time_hint,
    )

    try:
        # Use fast_reply model (lightweight/Haiku) if configured
        fast_model = get_fast_reply_model()
        cmd = ["claude", "-p", prompt, "--max-turns", "1"]
        if fast_model:
            cmd.extend(["--model", fast_model])
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0 and result.stdout.strip():
            response = result.stdout.strip()
            # Clean markdown
            response = response.replace("**", "").replace("```", "")
            send_telegram(response)
            save_telegram_message(TELEGRAM_HISTORY_FILE, "assistant", response)
        else:
            if result.stderr:
                print(f"[awake] /sparring Claude stderr: {result.stderr[:500]}")
            send_telegram("Nothing compelling to say right now. Come back later.")
    except subprocess.TimeoutExpired:
        send_telegram("Timeout — my brain needs more time. Try again.")
    except Exception as e:
        print(f"[awake] Sparring error: {e}")
        send_telegram("Error during sparring. Try again.")


def handle_reflect(message: str):
    """Handle /reflect command — write human's reflection to shared journal."""
    shared_journal = INSTANCE_DIR / "shared-journal.md"

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"\n## Alexis — {timestamp}\n\n{message}\n"

    # Append to shared journal
    shared_journal.parent.mkdir(parents=True, exist_ok=True)
    with open(shared_journal, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(entry)

    send_telegram("Noted in the shared journal. I'll reflect on it.")


def handle_mission_command(text: str):
    """Handle /mission <text> command — parity with 'mission:' keyword.

    Strips the /mission prefix, checks for project tag, and either queues
    the mission directly or asks the user to specify a project.
    """
    raw = text.strip()
    lower = raw.lower()
    if lower.startswith("/mission:"):
        mission_text = raw[9:].strip()
    elif lower.startswith("/mission "):
        mission_text = raw[9:].strip()
    elif lower == "/mission":
        mission_text = ""
    else:
        mission_text = raw[8:].strip()

    if not mission_text:
        send_telegram(
            "Usage: /mission <description>\n\n"
            "Examples:\n"
            "  /mission fix the login bug\n"
            "  /mission [project:koan] add retry logic\n"
        )
        return

    # Check if the text already has a project tag
    project, _ = parse_project(mission_text)

    if not project:
        known = get_known_projects()
        if len(known) > 1:
            project_list = "\n".join(f"  - {name}" for name in known)
            send_telegram(
                f"Which project for this mission?\n\n"
                f"{project_list}\n\n"
                f"Reply with the tag, e.g.:\n"
                f"  /mission [project:{known[0]}] {mission_text[:80]}"
            )
            return

    handle_mission(mission_text)


def handle_mission(text: str):
    """Append to missions.md with optional project tag."""
    # Parse project tag if present
    project, mission_text = parse_project(text)

    # Clean up the mission prefix
    if mission_text.lower().startswith("mission:"):
        mission_text = mission_text[8:].strip()
    elif mission_text.lower().startswith("mission :"):
        mission_text = mission_text[9:].strip()

    # Format mission entry with project tag if specified
    if project:
        mission_entry = f"- [project:{project}] {mission_text}"
    else:
        mission_entry = f"- {mission_text}"

    # Append to missions.md under pending section (with file locking)
    insert_pending_mission(MISSIONS_FILE, mission_entry)

    # Acknowledge with project info
    ack_msg = "\u2705 Mission received"
    if project:
        ack_msg += f" (project: {project})"
    ack_msg += f":\n\n{mission_text[:500]}"
    send_telegram(ack_msg)
    print(f"[awake] Mission queued: [{project or 'default'}] {mission_text[:60]}")
