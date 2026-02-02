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
import json
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Tuple, List, Dict

import requests

from app.format_outbox import format_for_telegram, load_soul, load_human_prefs, load_memory_context
from app.health_check import write_heartbeat
from app.notify import send_telegram, format_and_send
from app.utils import (
    load_dotenv,
    parse_project as _parse_project,
    insert_pending_mission,
    save_telegram_message,
    load_recent_telegram_history,
    format_conversation_history,
    get_allowed_tools,
    get_tools_description,
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
    # Long messages (>200 chars) with imperative verbs are likely missions
    if len(text) > 200 and MISSION_RE.search(text):
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

    if cmd == "/stop":
        (KOAN_ROOT / ".koan-stop").write_text("STOP")
        format_and_send("Stop requested. Current mission will complete, then Kōan will stop.")
        return

    if cmd == "/pause":
        pause_file = KOAN_ROOT / ".koan-pause"
        if pause_file.exists():
            format_and_send("Kōan is already paused. Use /resume to unpause.")
        else:
            pause_file.write_text("PAUSE")
            format_and_send("Kōan paused. The run loop stays active but no missions will be executed. Use /resume to unpause.")
        return

    if cmd == "/status":
        status = _build_status()
        format_and_send(status)
        return

    if cmd == "/resume":
        handle_resume()
        return

    # Unknown command — pass to Claude as chat
    handle_chat(text)


def _build_status() -> str:
    """Build status message grouped by project."""
    from app.missions import group_by_project

    parts = ["📊 Kōan Status"]

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

    # Run loop status
    pause_file = KOAN_ROOT / ".koan-pause"
    if pause_file.exists():
        parts.append("\n⏸️ Paused (use /resume to unpause)")

    stop_file = KOAN_ROOT / ".koan-stop"
    if stop_file.exists():
        parts.append("\n⛔ Stop requested")

    status_file = KOAN_ROOT / ".koan-status"
    if status_file.exists():
        parts.append(f"\nLoop: {status_file.read_text().strip()}")

    return "\n".join(parts)


def handle_resume():
    """Resume from pause or quota exhaustion."""
    pause_file = KOAN_ROOT / ".koan-pause"
    quota_file = KOAN_ROOT / ".koan-quota-reset"

    if pause_file.exists():
        pause_file.unlink(missing_ok=True)
        format_and_send("Kōan unpaused. Missions will resume on the next loop iteration.")
        return

    if not quota_file.exists():
        format_and_send("No pause or quota hold detected. Kōan is either running or was stopped normally. Use /status to check current state.")
        return

    try:
        lines = quota_file.read_text().strip().split("\n")
        reset_info = lines[0] if lines else "unknown time"
        paused_at = int(lines[1]) if len(lines) > 1 else 0

        # Calculate time since pause (rough estimate)
        hours_since_pause = (time.time() - paused_at) / 3600

        # Parse reset time from message like "resets 7pm (Europe/Paris)"
        # This is a simple heuristic - we assume if several hours have passed, quota likely reset
        likely_reset = hours_since_pause >= 2

        if likely_reset:
            quota_file.unlink(missing_ok=True)  # Remove the quota marker
            format_and_send(f"Quota likely reset ({reset_info}, paused {hours_since_pause:.1f}h ago). To resume, run: make run. The run loop will start fresh.")
        else:
            format_and_send(f"Quota probably not reset yet ({reset_info}). Paused {hours_since_pause:.1f}h ago. Check back later.")
    except Exception as e:
        print(f"[awake] Error checking quota reset: {e}")
        format_and_send("Error checking quota status. Try /status or check manually.")


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
    format_and_send(ack_msg)
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

    summary_budget = 0 if lite else 1500

    # Build prompt with conversation history
    prompt_parts = [
        "You are Kōan — a sparring partner, not an assistant.",
        f"Here is your identity:\n\n{SOUL}\n",
        f"{tools_desc}\n" if tools_desc else "",
        f"About the human:\n{prefs_context}\n" if prefs_context else "",
        f"Summary of past sessions:\n{SUMMARY[:summary_budget]}\n" if SUMMARY and summary_budget else "",
        f"Today's journal (excerpt):\n{journal_context}\n" if journal_context else "",
        f"{history_context}\n" if history_context else "",
        f"{time_hint}\n",
        f"The human sends you this message on Telegram:\n\n  « {text} »\n",
        "Respond in the human's preferred language. Be direct, concise, natural — like texting a collaborator. "
        "You can be funny (dry humor), you can disagree, you can ask back. "
        "2-3 sentences max unless the question requires more. "
        "No markdown formatting — this is Telegram, keep it plain.\n"
    ]
    prompt = "\n".join([p for p in prompt_parts if p])

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

    # Truncate for smartphone
    if len(cleaned) > 500:
        cleaned = cleaned[:497] + "..."

    return cleaned.strip()


def handle_chat(text: str):
    """Lightweight Claude call for conversational messages — fast response."""
    # Save user message to history
    save_telegram_message(TELEGRAM_HISTORY_FILE, "user", text)

    prompt = _build_chat_prompt(text)
    allowed_tools = get_allowed_tools()

    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--allowedTools", allowed_tools, "--max-turns", "1"],
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
            format_and_send(error_msg)
            save_telegram_message(TELEGRAM_HISTORY_FILE, "assistant", error_msg)
        else:
            print("[awake] Empty response from Claude.")
    except subprocess.TimeoutExpired:
        print(f"[awake] Claude timed out ({CHAT_TIMEOUT}s). Retrying with lite context...")
        # Retry with reduced context
        lite_prompt = _build_chat_prompt(text, lite=True)
        try:
            result = subprocess.run(
                ["claude", "-p", lite_prompt, "--allowedTools", allowed_tools, "--max-turns", "1"],
                capture_output=True, text=True, timeout=CHAT_TIMEOUT,
                cwd=PROJECT_PATH or str(KOAN_ROOT),
            )
            response = _clean_chat_response(result.stdout.strip())
            if response:
                send_telegram(response)
                save_telegram_message(TELEGRAM_HISTORY_FILE, "assistant", response)
                print(f"[awake] Chat reply (lite retry): {response[:80]}...")
            else:
                timeout_msg = f"Timeout after {CHAT_TIMEOUT}s — try a shorter question, or send 'mission: ...' for complex tasks."
                format_and_send(timeout_msg)
                save_telegram_message(TELEGRAM_HISTORY_FILE, "assistant", timeout_msg)
        except subprocess.TimeoutExpired:
            timeout_msg = f"Timeout after {CHAT_TIMEOUT}s — try a shorter question, or send 'mission: ...' for complex tasks."
            format_and_send(timeout_msg)
            save_telegram_message(TELEGRAM_HISTORY_FILE, "assistant", timeout_msg)
        except Exception as e:
            print(f"[awake] Lite retry error: {e}")
            error_msg = "Something went wrong — try again?"
            format_and_send(error_msg)
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

def handle_message(text: str):
    text = text.strip()
    if not text:
        return

    if is_command(text):
        handle_command(text)
    elif is_mission(text):
        handle_mission(text)
    else:
        handle_chat(text)


def main():
    check_config()
    print(f"[awake] Token: ...{BOT_TOKEN[-8:]}")
    print(f"[awake] Chat ID: {CHAT_ID}")
    print(f"[awake] Soul: {len(SOUL)} chars loaded")
    print(f"[awake] Summary: {len(SUMMARY)} chars loaded")
    print(f"[awake] Polling every {POLL_INTERVAL}s (chat mode: fast reply)")
    offset = None

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


if __name__ == "__main__":
    main()
