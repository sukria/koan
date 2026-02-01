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
import time
from datetime import date
from pathlib import Path
from typing import Optional, Tuple

import requests

from notify import send_telegram


def load_dotenv():
    """Load .env file, stripping quotes from values."""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ[key.strip()] = value.strip().strip("\"'")


load_dotenv()

BOT_TOKEN = os.environ.get("KOAN_TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("KOAN_TELEGRAM_CHAT_ID", "")
POLL_INTERVAL = int(os.environ.get("KOAN_BRIDGE_INTERVAL", "3"))

KOAN_ROOT = Path(__file__).parent.parent
INSTANCE_DIR = KOAN_ROOT / "instance"
MISSIONS_FILE = INSTANCE_DIR / "missions.md"
OUTBOX_FILE = INSTANCE_DIR / "outbox.md"
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
        return resp.json().get("result", [])
    except Exception as e:
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
    """Extract [project:name] from message. Returns (project_name, cleaned_text)."""
    match = re.search(r'\[project:([a-zA-Z0-9_-]+)\]', text)
    if match:
        project = match.group(1)
        cleaned = re.sub(r'\[project:[a-zA-Z0-9_-]+\]\s*', '', text).strip()
        return project, cleaned
    return None, text


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def handle_command(text: str):
    """Handle /commands locally — no Claude needed."""
    cmd = text.strip().lower()

    if cmd == "/stop":
        (KOAN_ROOT / ".koan-stop").write_text("STOP")
        send_telegram("Stop requested. Current mission will complete, then Kōan will stop.")
        return

    if cmd == "/status":
        status = _build_status()
        send_telegram(status)
        return

    if cmd == "/resume":
        handle_resume()
        return

    # Unknown command — pass to Claude as chat
    handle_chat(text)


def _build_status() -> str:
    """Build status message grouped by project."""
    parts = ["📊 Kōan Status"]

    # Parse missions by project
    if MISSIONS_FILE.exists():
        from collections import defaultdict
        content = MISSIONS_FILE.read_text()
        missions_by_project = defaultdict(lambda: {"pending": [], "in_progress": []})

        lines = content.splitlines()
        current_section = None
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("## "):
                current_section = stripped[3:].lower().replace(" ", "_")
            elif stripped.startswith("- "):
                # Extract project tag if present
                match = re.search(r'\[project:([a-zA-Z0-9_-]+)\]', stripped)
                project = match.group(1) if match else "default"

                if current_section == "pending":
                    missions_by_project[project]["pending"].append(stripped)
                elif current_section == "in_progress":
                    missions_by_project[project]["in_progress"].append(stripped)

        # Display by project
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
                            display = re.sub(r'\[project:[a-zA-Z0-9_-]+\]\s*', '', m)
                            parts.append(f"    {display}")
                    if pending:
                        parts.append(f"  Pending: {len(pending)}")

    # Run loop status
    stop_file = KOAN_ROOT / ".koan-stop"
    if stop_file.exists():
        parts.append("\n⛔ Stop requested")

    status_file = KOAN_ROOT / ".koan-status"
    if status_file.exists():
        parts.append(f"\nLoop: {status_file.read_text().strip()}")

    return "\n".join(parts)


def handle_resume():
    """Check if quota has reset and offer to resume the run loop."""
    quota_file = KOAN_ROOT / ".koan-quota-reset"

    if not quota_file.exists():
        send_telegram("ℹ️ No quota pause detected. Kōan is either running or was stopped normally.\n\nUse /status to check current state.")
        return

    try:
        lines = quota_file.read_text().strip().split("\n")
        reset_info = lines[0] if lines else "unknown time"
        paused_at = int(lines[1]) if len(lines) > 1 else 0

        # Calculate time since pause (rough estimate)
        import time as time_module
        hours_since_pause = (time_module.time() - paused_at) / 3600

        # Parse reset time from message like "resets 7pm (Europe/Paris)"
        # This is a simple heuristic - we assume if several hours have passed, quota likely reset
        likely_reset = hours_since_pause >= 2

        if likely_reset:
            quota_file.unlink()  # Remove the quota marker
            send_telegram(f"✅ Quota likely reset ({reset_info}, paused {hours_since_pause:.1f}h ago)\n\nTo resume, run: make run\n\nThe run loop will start fresh. Check claude.ai/settings to verify your quota before starting.")
        else:
            send_telegram(f"⏳ Quota probably not reset yet ({reset_info})\n\nPaused {hours_since_pause:.1f}h ago. Check back later or visit claude.ai/settings to verify your quota status.")
    except Exception as e:
        print(f"[awake] Error checking quota reset: {e}")
        send_telegram(f"⚠️ Error checking quota status. Try /status or check manually.")


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

    # Append to missions.md under "## Pending"
    if MISSIONS_FILE.exists():
        content = MISSIONS_FILE.read_text()
    else:
        content = "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n"

    # Insert under "## Pending"
    marker = "## Pending"
    if marker in content:
        idx = content.index(marker) + len(marker)
        # Find the end of the "Pending" line (skip newlines)
        while idx < len(content) and content[idx] == "\n":
            idx += 1
        new_entry = f"\n{mission_entry}\n"
        content = content[:idx] + new_entry + content[idx:]
    else:
        content += f"\n## Pending\n\n{mission_entry}\n"

    MISSIONS_FILE.write_text(content)

    # Acknowledge with project info
    ack_msg = f"✅ Mission received"
    if project:
        ack_msg += f" (project: {project})"
    ack_msg += f":\n\n{mission_text[:500]}"
    send_telegram(ack_msg)
    print(f"[awake] Mission queued: [{project or 'default'}] {mission_text[:60]}")


def handle_chat(text: str):
    """Lightweight Claude call for conversational messages — fast response."""
    # Load today's journal for recent context
    # Try nested structure first (journal/YYYY-MM-DD/*.md), fall back to flat
    journal_context = ""
    today = f"{date.today():%Y-%m-%d}"
    journal_dir = INSTANCE_DIR / "journal" / today
    if journal_dir.is_dir():
        parts = []
        for f in sorted(journal_dir.glob("*.md")):
            parts.append(f.read_text())
        journal_content = "\n---\n".join(parts)
    else:
        journal_path = INSTANCE_DIR / "journal" / f"{today}.md"
        journal_content = journal_path.read_text() if journal_path.exists() else ""
    if journal_content:
        if len(journal_content) > 2000:
            journal_context = "...\n" + journal_content[-2000:]
        else:
            journal_context = journal_content

    prompt = (
        f"You are Kōan. Here is your identity:\n\n{SOUL}\n\n"
        f"Summary of past sessions:\n{SUMMARY[:1500]}\n\n"
        f"Today's journal (excerpt):\n{journal_context}\n\n"
        f"The human sends you this message on Telegram:\n\n"
        f"  « {text} »\n\n"
        f"Respond directly. Be concise and natural. "
        f"This is a Telegram conversation — not a report. "
        f"2-3 sentences max unless the question requires more.\n"
    )

    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--allowedTools", "Read,Glob,Grep"],
            capture_output=True, text=True, timeout=120,
            cwd=PROJECT_PATH or str(KOAN_ROOT),
        )
        response = result.stdout.strip()
        if response:
            send_telegram(response)
            print(f"[awake] Chat reply: {response[:80]}...")
        elif result.returncode != 0:
            print(f"[awake] Claude error: {result.stderr[:200]}")
            send_telegram("Hmm, I couldn't formulate a response. Try again?")
        else:
            print("[awake] Empty response from Claude.")
    except subprocess.TimeoutExpired:
        print("[awake] Claude timed out (2min).")
        send_telegram("Taking too long to respond — try again, or send 'mission: ...' if it's a task.")
    except Exception as e:
        print(f"[awake] Claude error: {e}")


def flush_outbox():
    """Relay messages from the run loop outbox. Uses file locking for concurrency."""
    if not OUTBOX_FILE.exists():
        return
    try:
        with open(OUTBOX_FILE, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            content = f.read().strip()
            if content:
                send_telegram(content)
                f.seek(0)
                f.truncate()
                # Show preview of sent message (first 150 chars)
                preview = content[:150].replace("\n", " ")
                if len(content) > 150:
                    preview += "..."
                print(f"[awake] Outbox flushed: {preview}")
            fcntl.flock(f, fcntl.LOCK_UN)
    except BlockingIOError:
        # Another process holds the lock — skip this cycle
        pass
    except Exception as e:
        print(f"[awake] Outbox error: {e}")


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
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
