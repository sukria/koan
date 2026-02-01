#!/usr/bin/env python3
"""
Kōan — Local Dashboard

Flask web app for monitoring and interacting with Kōan.
Features:
- Status overview (signal files, run state)
- Missions management (view, add, reorder)
- Chat interface (writes to outbox, queues missions)
- Journal viewer

Usage:
    python3 dashboard.py [--port 5001]
    make dashboard
"""

import os
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from flask import Flask, jsonify, redirect, render_template, request, url_for
from utils import parse_project, insert_pending_mission

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

KOAN_ROOT = Path(__file__).parent.parent
INSTANCE_DIR = KOAN_ROOT / "instance"
MISSIONS_FILE = INSTANCE_DIR / "missions.md"
OUTBOX_FILE = INSTANCE_DIR / "outbox.md"
SOUL_FILE = INSTANCE_DIR / "soul.md"
SUMMARY_FILE = INSTANCE_DIR / "memory" / "summary.md"
JOURNAL_DIR = INSTANCE_DIR / "journal"

app = Flask(__name__, template_folder=str(KOAN_ROOT / "koan" / "templates"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_file(path: Path) -> str:
    if path.exists():
        return path.read_text()
    return ""


def get_signal_status() -> dict:
    """Read .koan-* signal files."""
    status = {
        "stop_requested": (KOAN_ROOT / ".koan-stop").exists(),
        "quota_paused": (KOAN_ROOT / ".koan-quota-reset").exists(),
        "loop_status": "",
    }
    status_file = KOAN_ROOT / ".koan-status"
    if status_file.exists():
        status["loop_status"] = status_file.read_text().strip()
    report_file = KOAN_ROOT / ".koan-daily-report"
    if report_file.exists():
        status["last_report"] = report_file.read_text().strip()
    return status


def parse_missions() -> dict:
    """Parse missions.md into structured sections."""
    from missions import parse_sections

    content = read_file(MISSIONS_FILE)
    if not content:
        return {"pending": [], "in_progress": [], "done": []}

    return parse_sections(content)


def get_journal_entries(limit: int = 7) -> list:
    """Get recent journal entries."""
    entries = []
    if not JOURNAL_DIR.exists():
        return entries

    # Collect all journal dates (both flat and nested)
    dates = set()
    for item in sorted(JOURNAL_DIR.iterdir(), reverse=True):
        if item.is_dir() and re.match(r"\d{4}-\d{2}-\d{2}", item.name):
            dates.add(item.name)
        elif item.suffix == ".md" and re.match(r"\d{4}-\d{2}-\d{2}", item.stem):
            dates.add(item.stem)

    for d in sorted(dates, reverse=True)[:limit]:
        day_entries = []
        # Check nested structure
        nested = JOURNAL_DIR / d
        if nested.is_dir():
            for f in sorted(nested.glob("*.md")):
                day_entries.append({
                    "project": f.stem,
                    "content": f.read_text(),
                })
        # Check flat structure
        flat = JOURNAL_DIR / f"{d}.md"
        if flat.is_file():
            day_entries.append({
                "project": "general",
                "content": flat.read_text(),
            })
        if day_entries:
            entries.append({"date": d, "entries": day_entries})

    return entries


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Main dashboard page."""
    signals = get_signal_status()
    missions = parse_missions()

    # Determine overall state
    if signals["stop_requested"]:
        state = "stopped"
        state_label = "Arrêté"
    elif signals["quota_paused"]:
        state = "paused"
        state_label = "Quota épuisé"
    elif signals["loop_status"]:
        state = "running"
        state_label = f"Run {signals['loop_status']}"
    else:
        state = "idle"
        state_label = "Inactif"

    return render_template("dashboard.html",
        state=state,
        state_label=state_label,
        signals=signals,
        missions=missions,
        pending_count=len(missions["pending"]),
        in_progress_count=len(missions["in_progress"]),
        done_count=len(missions["done"]),
    )


@app.route("/missions")
def missions_page():
    """Missions management page."""
    missions = parse_missions()
    return render_template("missions.html", missions=missions)


@app.route("/missions/add", methods=["POST"])
def add_mission():
    """Add a new mission to pending."""
    text = request.form.get("mission", "").strip()
    project = request.form.get("project", "").strip()
    if not text:
        return redirect(url_for("missions_page"))

    # Format entry
    if project:
        entry = f"- [project:{project}] {text}"
    else:
        entry = f"- {text}"

    insert_pending_mission(MISSIONS_FILE, entry)
    return redirect(url_for("missions_page"))


@app.route("/chat")
def chat_page():
    """Chat interface."""
    return render_template("chat.html")


@app.route("/chat/send", methods=["POST"])
def chat_send():
    """Send a message — either as mission or direct outbox message."""
    text = request.form.get("message", "").strip()
    mode = request.form.get("mode", "chat")  # chat or mission

    if not text:
        return jsonify({"ok": False, "error": "Empty message"})

    if mode == "mission":
        # Queue as mission (same logic as awake.py)
        project, mission_text = parse_project(text)
        if project:
            entry = f"- [project:{project}] {mission_text}"
        else:
            entry = f"- {mission_text}"

        insert_pending_mission(MISSIONS_FILE, entry)
        return jsonify({"ok": True, "type": "mission", "text": mission_text})

    else:
        # Direct chat — call claude CLI like awake.py does
        soul = read_file(SOUL_FILE)
        summary = read_file(SUMMARY_FILE)[:1500]

        # Load today's journal
        journal_context = ""
        today = f"{date.today():%Y-%m-%d}"
        journal_dir = JOURNAL_DIR / today
        if journal_dir.is_dir():
            parts = []
            for f in sorted(journal_dir.glob("*.md")):
                parts.append(f.read_text())
            journal_content = "\n---\n".join(parts)
        else:
            journal_path = JOURNAL_DIR / f"{today}.md"
            journal_content = journal_path.read_text() if journal_path.exists() else ""
        if journal_content:
            journal_context = journal_content[-2000:] if len(journal_content) > 2000 else journal_content

        prompt = (
            f"You are Kōan. Here is your identity:\n\n{soul}\n\n"
            f"Summary of past sessions:\n{summary}\n\n"
            f"Today's journal (excerpt):\n{journal_context}\n\n"
            f"The human sends you this message via the dashboard:\n\n"
            f"  « {text} »\n\n"
            f"Respond directly. Be concise and natural. "
            f"2-3 sentences max unless the question requires more.\n"
        )

        try:
            project_path = os.environ.get("KOAN_PROJECT_PATH", str(KOAN_ROOT))
            result = subprocess.run(
                ["claude", "-p", prompt, "--allowedTools", "Read,Glob,Grep"],
                capture_output=True, text=True, timeout=120,
                cwd=project_path,
            )
            response = result.stdout.strip()
            if not response:
                response = "Je n'ai pas pu formuler de réponse. Réessaie ?"
            return jsonify({"ok": True, "type": "chat", "response": response})
        except subprocess.TimeoutExpired:
            return jsonify({"ok": True, "type": "chat", "response": "Timeout — je prends trop de temps. Réessaie ?"})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)})


@app.route("/journal")
def journal_page():
    """Journal viewer."""
    entries = get_journal_entries(limit=14)
    return render_template("journal.html", entries=entries)


@app.route("/api/status")
def api_status():
    """JSON status endpoint."""
    signals = get_signal_status()
    missions = parse_missions()
    return jsonify({
        "signals": signals,
        "missions": {
            "pending": len(missions["pending"]),
            "in_progress": len(missions["in_progress"]),
            "done": len(missions["done"]),
        },
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Kōan Dashboard")
    parser.add_argument("--port", type=int, default=5001)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    print(f"[dashboard] Starting on http://{args.host}:{args.port}")
    print(f"[dashboard] Instance: {INSTANCE_DIR}")
    app.run(host=args.host, port=args.port, debug=True)
