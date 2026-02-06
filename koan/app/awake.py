#!/usr/bin/env python3
"""
Kōan Telegram Bridge — v2

Fast-response architecture:
- Polls Telegram every 3s (configurable)
- Chat messages → lightweight Claude call → instant reply
- Mission-like messages → written to missions.md → ack sent immediately
- Outbox flushed every cycle (no more waiting for next poll)
- /stop, /status handled locally (no Claude needed)
"""

import fcntl
import os
import re
import subprocess
import sys
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Tuple

import requests

from app.format_outbox import format_for_telegram, load_soul, load_human_prefs, load_memory_context
from app.health_check import write_heartbeat
from app.notify import send_telegram
from app.utils import (
    load_dotenv,
    parse_project as _parse_project,
    insert_pending_mission,
    get_known_projects,
    save_telegram_message,
    load_recent_telegram_history,
    format_conversation_history,
    compact_telegram_history,
    get_chat_tools,
    get_tools_description,
    get_model_config,
    build_claude_flags,
    get_fast_reply_model,
)

load_dotenv()

BOT_TOKEN = os.environ.get("KOAN_TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("KOAN_TELEGRAM_CHAT_ID", "")
POLL_INTERVAL = int(os.environ.get("KOAN_BRIDGE_INTERVAL", "3"))
CHAT_TIMEOUT = int(os.environ.get("KOAN_CHAT_TIMEOUT", "180"))

KOAN_ROOT = Path(os.environ["KOAN_ROOT"])
INSTANCE_DIR = KOAN_ROOT / "instance"
MISSIONS_FILE = INSTANCE_DIR / "missions.md"
OUTBOX_FILE = INSTANCE_DIR / "outbox.md"
TELEGRAM_HISTORY_FILE = INSTANCE_DIR / "telegram-history.jsonl"
TOPICS_FILE = INSTANCE_DIR / "previous-discussions-topics.json"
PROJECT_PATH = os.environ.get("KOAN_PROJECT_PATH", "")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Context loaded once at startup
SOUL = ""
soul_path = INSTANCE_DIR / "soul.md"
if soul_path.exists():
    SOUL = soul_path.read_text()

SUMMARY = ""
summary_path = INSTANCE_DIR / "memory" / "summary.md"
if summary_path.exists():
    SUMMARY = summary_path.read_text()


def check_config():
    if not BOT_TOKEN or not CHAT_ID:
        print("Error: Set KOAN_TELEGRAM_TOKEN and KOAN_TELEGRAM_CHAT_ID env vars.")
        sys.exit(1)
    if not INSTANCE_DIR.exists():
        print("Error: No instance/ directory. Run: cp -r instance.example instance")
        sys.exit(1)


def get_updates(offset=None):
    params = {"timeout": 30}
    if offset:
        params["offset"] = offset
    try:
        resp = requests.get(f"{TELEGRAM_API}/getUpdates", params=params, timeout=35)
        data = resp.json()
        return data.get("result", [])
    except (requests.RequestException, ValueError) as e:
        print(f"[awake] Telegram error: {e}")
        return []


# ---------------------------------------------------------------------------
# Message classification
# ---------------------------------------------------------------------------

# Patterns that indicate a mission (imperative, actionable request)
MISSION_PATTERNS = [
    r"^(implement|create|add|fix|audit|review|analyze|explore|build|write|run|deploy|test|refactor)\b",
    r"^mission\s*:",
]
MISSION_RE = re.compile("|".join(MISSION_PATTERNS), re.IGNORECASE)


def is_mission(text: str) -> bool:
    """Heuristic: does this message look like a mission assignment?"""
    # Explicit prefix always wins
    if text.lower().startswith("mission:") or text.lower().startswith("mission :"):
        return True
    # Long messages (>200 chars) that start with imperative verbs are likely missions
    if len(text) > 200 and MISSION_RE.match(text):
        return True
    # Short imperative sentences
    if MISSION_RE.match(text):
        return True
    return False


def is_command(text: str) -> bool:
    return text.startswith("/")


def parse_project(text: str) -> Tuple[Optional[str], str]:
    """Extract [project:name] or [projet:name] from message."""
    return _parse_project(text)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def handle_command(text: str):
    """Handle /commands locally — no Claude needed."""
    cmd = text.strip().lower()

    # /chat forces chat mode — bypass mission classification
    if cmd.startswith("/chat"):
        chat_text = text[5:].strip()
        if not chat_text:
            send_telegram("Usage: /chat <message>\nForces chat mode for messages that look like missions.")
            return
        _run_in_worker(handle_chat, chat_text)
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
        status = _build_status()
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
        _handle_sparring()
        return

    if cmd.startswith("/reflect "):
        _handle_reflect(text[9:].strip())
        return

    if cmd == "/ping":
        _handle_ping()
        return

    if cmd.startswith("/log") or cmd.startswith("/journal"):
        # Extract args after command name
        if cmd.startswith("/journal"):
            args = text[8:].strip()
        else:
            args = text[4:].strip()
        _handle_log(args)
        return

    if cmd == "/help":
        _handle_help()
        return

    if cmd == "/usage":
        _run_in_worker(_handle_usage)
        return

    if cmd.startswith("/mission"):
        _handle_mission_command(text)
        return

    # Unknown command — pass to Claude as chat
    handle_chat(text)


def _build_status() -> str:
    """Build status message grouped by project."""
    from app.missions import group_by_project

    parts = ["📊 Kōan Status"]

    # Run loop status — FIRST, most important info
    pause_file = KOAN_ROOT / ".koan-pause"
    stop_file = KOAN_ROOT / ".koan-stop"

    if pause_file.exists():
        parts.append("\n⏸️ **PAUSED** — No missions being executed")
        parts.append("   /resume to continue")
    elif stop_file.exists():
        parts.append("\n⛔ **STOP REQUESTED** — Finishing current work")
    else:
        parts.append("\n▶️ **ACTIVE** — Run loop running")

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

def _handle_ping():
    """Check if the run loop (make run) is alive and report status."""
    # Check if run.sh process is running
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
        send_telegram("⛔ Run loop is stopping after current mission.")
    elif run_loop_alive and pause_file.exists():
        send_telegram("⏸️ Run loop is paused. /resume to unpause.")
    elif run_loop_alive:
        send_telegram("✅")
    else:
        send_telegram("❌ Run loop is not running.\n\nTo restart:\n  make run &")


def _handle_log(args: str):
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


def _handle_help():
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


def _handle_usage():
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
            if result.returncode != 0:
                print(f"[awake] /usage Claude error (exit {result.returncode}): {result.stderr[:200]}")
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


def _handle_sparring():
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
            if result.returncode != 0:
                print(f"[awake] /sparring Claude error (exit {result.returncode}): {result.stderr[:200]}")
            elif result.stderr:
                print(f"[awake] /sparring Claude stderr: {result.stderr[:500]}")
            send_telegram("Nothing compelling to say right now. Come back later.")
    except subprocess.TimeoutExpired:
        send_telegram("Timeout — my brain needs more time. Try again.")
    except Exception as e:
        print(f"[awake] Sparring error: {e}")
        send_telegram("Error during sparring. Try again.")


def _handle_reflect(message: str):
    """Handle /reflect command — write human's reflection to shared journal."""
    shared_journal = INSTANCE_DIR / "shared-journal.md"

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"\n## Alexis — {timestamp}\n\n{message}\n"

    # Append to shared journal
    import fcntl as _fcntl
    shared_journal.parent.mkdir(parents=True, exist_ok=True)
    with open(shared_journal, "a") as f:
        _fcntl.flock(f, _fcntl.LOCK_EX)
        f.write(entry)

    send_telegram("Noted in the shared journal. I'll reflect on it.")


def _handle_mission_command(text: str):
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
    ack_msg = f"✅ Mission received"
    if project:
        ack_msg += f" (project: {project})"
    ack_msg += f":\n\n{mission_text[:500]}"
    send_telegram(ack_msg)
    print(f"[awake] Mission queued: [{project or 'default'}] {mission_text[:60]}")


def _build_chat_prompt(text: str, *, lite: bool = False) -> str:
    """Build the prompt for a chat response.

    Args:
        text: The user's message.
        lite: If True, strip heavy context (journal, summary) to stay under budget.
    """
    # Load recent conversation history
    history = load_recent_telegram_history(TELEGRAM_HISTORY_FILE, max_messages=10)
    history_context = format_conversation_history(history)

    journal_context = ""
    if not lite:
        # Load today's journal for recent context
        from app.utils import read_all_journals
        journal_content = read_all_journals(INSTANCE_DIR, date.today())
        if journal_content:
            if len(journal_content) > 2000:
                journal_context = "...\n" + journal_content[-2000:]
            else:
                journal_context = journal_content

    # Load human preferences for personality context
    prefs_context = ""
    prefs_path = INSTANCE_DIR / "memory" / "global" / "human-preferences.md"
    if prefs_path.exists():
        prefs_context = prefs_path.read_text().strip()

    # Load live progress from pending.md (run in progress)
    pending_context = ""
    pending_path = INSTANCE_DIR / "journal" / "pending.md"
    if pending_path.exists():
        try:
            pending_content = pending_path.read_text()
            # Take last 1500 chars for recent progress
            if len(pending_content) > 1500:
                pending_context = "Live progress (pending.md, last entries):\n...\n" + pending_content[-1500:]
            else:
                pending_context = "Live progress (pending.md):\n" + pending_content
        except Exception:
            pass

    # Load current mission state (live sync with run loop)
    missions_context = ""
    if pending_context:
        missions_context = pending_context
    elif MISSIONS_FILE.exists():
        from app.missions import parse_sections
        sections = parse_sections(MISSIONS_FILE.read_text())
        in_progress = sections.get("in_progress", [])
        pending = sections.get("pending", [])
        if in_progress or pending:
            parts = []
            if in_progress:
                parts.append("In progress: " + "; ".join(in_progress[:3]))
            if pending:
                parts.append(f"Pending: {len(pending)} mission(s)")
            missions_context = "\n".join(parts)

    # Run loop status (CRITICAL for pause awareness)
    run_loop_status = ""
    pause_file = KOAN_ROOT / ".koan-pause"
    stop_file = KOAN_ROOT / ".koan-stop"
    if pause_file.exists():
        run_loop_status = "\n\nRun loop status: ⏸️ PAUSED — Missions are NOT being executed"
    elif stop_file.exists():
        run_loop_status = "\n\nRun loop status: ⛔ STOP REQUESTED — Finishing current work"
    else:
        run_loop_status = "\n\nRun loop status: ▶️ RUNNING"

    # Append run loop status to missions context
    if missions_context:
        missions_context += run_loop_status
    else:
        missions_context = f"No pending missions.{run_loop_status}"

    # Determine time-of-day for natural tone
    hour = datetime.now().hour
    if hour < 7:
        time_hint = "It's very early morning."
    elif hour < 12:
        time_hint = "It's morning."
    elif hour < 18:
        time_hint = "It's afternoon."
    elif hour < 22:
        time_hint = "It's evening."
    else:
        time_hint = "It's late night."

    # Load tools description
    tools_desc = get_tools_description()

    from app.prompts import load_prompt

    summary_budget = 0 if lite else 1500
    summary_block = f"Summary of past sessions:\n{SUMMARY[:summary_budget]}" if SUMMARY and summary_budget else ""
    prefs_block = f"About the human:\n{prefs_context}" if prefs_context else ""
    journal_block = f"Today's journal (excerpt):\n{journal_context}" if journal_context else ""
    missions_block = f"Current missions state:\n{missions_context}" if missions_context else ""

    # Load emotional memory for relationship-aware responses
    emotional_context = ""
    if not lite:
        emotional_path = INSTANCE_DIR / "memory" / "global" / "emotional-memory.md"
        if emotional_path.exists():
            content = emotional_path.read_text().strip()
            # Take last 800 chars — enough for tone, not too heavy
            if len(content) > 800:
                emotional_context = "...\n" + content[-800:]
            else:
                emotional_context = content

    prompt = load_prompt(
        "chat",
        SOUL=SOUL,
        TOOLS_DESC=tools_desc or "",
        PREFS=prefs_block,
        SUMMARY=summary_block,
        JOURNAL=journal_block,
        MISSIONS=missions_block,
        HISTORY=history_context or "",
        TIME_HINT=time_hint,
        TEXT=text,
    )

    # Inject emotional memory before the user message (if available)
    if emotional_context:
        prompt = prompt.replace(
            f"« {text} »",
            f"Emotional memory (relationship context, use to color your tone):\n{emotional_context}\n\nThe human sends you this message on Telegram:\n\n  « {text} »",
        )

    # Hard cap: if prompt exceeds 12k chars, force lite mode
    MAX_PROMPT_CHARS = 12000
    if len(prompt) > MAX_PROMPT_CHARS and not lite:
        return _build_chat_prompt(text, lite=True)

    return prompt


def _clean_chat_response(text: str) -> str:
    """Clean Claude CLI output for Telegram delivery.

    Strips error artifacts, markdown, and truncates for smartphone reading.
    """
    # Remove Claude CLI error lines
    lines = text.splitlines()
    lines = [l for l in lines if not re.match(r'^Error:.*max turns', l, re.IGNORECASE)]
    cleaned = "\n".join(lines).strip()

    # Strip markdown artifacts
    cleaned = cleaned.replace("```", "")
    cleaned = cleaned.replace("**", "")
    cleaned = cleaned.replace("__", "")
    cleaned = cleaned.replace("~~", "")
    # Strip heading markers
    cleaned = re.sub(r'^#{1,6}\s+', '', cleaned, flags=re.MULTILINE)

    # Truncate for smartphone (Telegram limit is 4096, keep 2000 for readability)
    if len(cleaned) > 2000:
        cleaned = cleaned[:1997] + "..."

    return cleaned.strip()


def handle_chat(text: str):
    """Lightweight Claude call for conversational messages — fast response.

    Uses restricted tools (Read/Glob/Grep by default) to prevent prompt
    injection attacks via Telegram messages. No Bash, Edit, or Write access.
    """
    # Save user message to history
    save_telegram_message(TELEGRAM_HISTORY_FILE, "user", text)

    prompt = _build_chat_prompt(text)
    chat_tools = get_chat_tools()  # Read-only tools for security
    models = get_model_config()
    chat_flags = build_claude_flags(model=models["chat"], fallback=models["fallback"])

    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--allowedTools", chat_tools, "--max-turns", "1"] + chat_flags,
            capture_output=True, text=True, timeout=CHAT_TIMEOUT,
            cwd=PROJECT_PATH or str(KOAN_ROOT),
        )
        response = _clean_chat_response(result.stdout.strip())
        if response:
            send_telegram(response)
            # Save assistant response to history
            save_telegram_message(TELEGRAM_HISTORY_FILE, "assistant", response)
            print(f"[awake] Chat reply: {response[:80]}...")
        elif result.returncode != 0:
            print(f"[awake] Claude error: {result.stderr[:200]}")
            error_msg = "Hmm, I couldn't formulate a response. Try again?"
            send_telegram(error_msg)
            save_telegram_message(TELEGRAM_HISTORY_FILE, "assistant", error_msg)
        else:
            print("[awake] Empty response from Claude.")
    except subprocess.TimeoutExpired:
        print(f"[awake] Claude timed out ({CHAT_TIMEOUT}s). Retrying with lite context...")
        # Retry with reduced context
        lite_prompt = _build_chat_prompt(text, lite=True)
        try:
            result = subprocess.run(
                ["claude", "-p", lite_prompt, "--allowedTools", chat_tools, "--max-turns", "1"] + chat_flags,
                capture_output=True, text=True, timeout=CHAT_TIMEOUT,
                cwd=PROJECT_PATH or str(KOAN_ROOT),
            )
            response = _clean_chat_response(result.stdout.strip())
            if response:
                send_telegram(response)
                save_telegram_message(TELEGRAM_HISTORY_FILE, "assistant", response)
                print(f"[awake] Chat reply (lite retry): {response[:80]}...")
            else:
                if result.stderr:
                    print(f"[awake] Lite retry stderr: {result.stderr[:500]}")
                timeout_msg = f"Timeout after {CHAT_TIMEOUT}s — try a shorter question, or send 'mission: ...' for complex tasks."
                send_telegram(timeout_msg)
                save_telegram_message(TELEGRAM_HISTORY_FILE, "assistant", timeout_msg)
        except subprocess.TimeoutExpired:
            timeout_msg = f"Timeout after {CHAT_TIMEOUT}s — try a shorter question, or send 'mission: ...' for complex tasks."
            send_telegram(timeout_msg)
            save_telegram_message(TELEGRAM_HISTORY_FILE, "assistant", timeout_msg)
        except Exception as e:
            print(f"[awake] Lite retry error: {e}")
            error_msg = "Something went wrong — try again?"
            send_telegram(error_msg)
            save_telegram_message(TELEGRAM_HISTORY_FILE, "assistant", error_msg)
    except Exception as e:
        print(f"[awake] Claude error: {e}")


def flush_outbox():
    """Relay messages from the run loop outbox. Uses file locking for concurrency.

    ALL outbox messages are formatted via Claude before sending to Telegram.
    This ensures consistent personality, French language, and conversational tone
    regardless of the message source (Claude session, run.sh, retrospective).
    """
    if not OUTBOX_FILE.exists():
        return
    try:
        with open(OUTBOX_FILE, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            content = f.read().strip()
            if content:
                # Format through Claude before sending
                formatted = _format_outbox_message(content)
                if send_telegram(formatted):
                    f.seek(0)
                    f.truncate()
                    # Show preview of sent message (first 150 chars)
                    preview = formatted[:150].replace("\n", " ")
                    if len(formatted) > 150:
                        preview += "..."
                    print(f"[awake] Outbox flushed: {preview}")
                else:
                    print("[awake] Outbox send failed — keeping messages for retry")
            fcntl.flock(f, fcntl.LOCK_UN)
    except BlockingIOError:
        # Another process holds the lock — skip this cycle
        pass
    except Exception as e:
        print(f"[awake] Outbox error: {e}")


def _format_outbox_message(raw_content: str) -> str:
    """Format outbox content via Claude with full personality context.

    Args:
        raw_content: Raw message text from outbox.md

    Returns:
        Formatted message ready for Telegram
    """
    try:
        soul = load_soul(INSTANCE_DIR)
        prefs = load_human_prefs(INSTANCE_DIR)
        memory = load_memory_context(INSTANCE_DIR)
        return format_for_telegram(raw_content, soul, prefs, memory)
    except Exception as e:
        print(f"[awake] Format error, sending raw: {e}")
        return raw_content


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Worker thread — runs handle_chat in background so polling stays responsive
# ---------------------------------------------------------------------------

_worker_thread: Optional[threading.Thread] = None
_worker_lock = threading.Lock()


def _run_in_worker(fn, *args):
    """Run fn(*args) in a background thread. One worker at a time."""
    global _worker_thread
    with _worker_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            send_telegram("Busy with a previous message. Try again in a moment.")
            return
        _worker_thread = threading.Thread(target=fn, args=args, daemon=True)
        _worker_thread.start()


def handle_message(text: str):
    text = text.strip()
    if not text:
        return

    if is_command(text):
        handle_command(text)
    elif is_mission(text):
        handle_mission(text)
    else:
        _run_in_worker(handle_chat, text)


def main():
    from app.banners import print_bridge_banner

    check_config()

    provider_name = "telegram" # about to become dynamic with provider abstraction
    print_bridge_banner(f"messaging bridge — {provider_name.lower()}")

    # Compact old conversation history to avoid context bleed across sessions
    compacted = compact_telegram_history(TELEGRAM_HISTORY_FILE, TOPICS_FILE)
    if compacted:
        print(f"[awake] Compacted {compacted} old messages at startup")
    # Purge stale heartbeat so health_check doesn't report STALE on restart
    heartbeat_file = KOAN_ROOT / ".koan-heartbeat"
    heartbeat_file.unlink(missing_ok=True)
    write_heartbeat(str(KOAN_ROOT))
    print(f"[awake] Token: ...{BOT_TOKEN[-8:]}")
    print(f"[awake] Chat ID: {CHAT_ID}")
    print(f"[awake] Soul: {len(SOUL)} chars loaded")
    print(f"[awake] Summary: {len(SUMMARY)} chars loaded")
    print(f"[awake] Polling every {POLL_INTERVAL}s (chat mode: fast reply)")
    offset = None

    try:
        while True:
            updates = get_updates(offset)
            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                text = msg.get("text", "")
                chat_id = str(msg.get("chat", {}).get("id", ""))
                if chat_id == CHAT_ID and text:
                    print(f"[awake] Received: {text[:60]}")
                    handle_message(text)

            flush_outbox()
            write_heartbeat(str(KOAN_ROOT))
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print("\n[awake] Shutting down.")
        sys.exit(0)


if __name__ == "__main__":
    main()
