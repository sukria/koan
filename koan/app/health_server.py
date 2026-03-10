"""Flask server for Cloud Run — health checks, GitHub webhooks, and API triggers.

Registers:
- /health (blueprint from app.health)
- /webhook/github (POST, from watcher webhook_handler)
- /api/trigger-report (POST, generates and sends daily report)

Serves on PORT (default 8080).
"""

import logging
import os
import threading
import time
from datetime import date, datetime, timezone

from flask import Flask, jsonify, request, render_template

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)

from app.health import health_bp
from app.utils import KOAN_ROOT, INSTANCE_DIR, load_config
from app.watcher.webhook_handler import register_webhooks

logger = logging.getLogger("governor.health_server")

template_dir = os.path.join(KOAN_ROOT, "koan", "templates")
app = Flask(__name__, template_folder=template_dir)
app.register_blueprint(health_bp)

register_webhooks(app, INSTANCE_DIR)


# ── Periodic GitLab scanner ─────────────────────────────────────────

def _gitlab_scan_loop():
    """Background thread: run GitLab scan at configured interval."""
    time.sleep(30)  # wait for startup to settle
    config = load_config().get("watcher", {})
    interval = config.get("gitlab", {}).get("scan_interval_minutes", 15) * 60

    while True:
        try:
            from app.watcher.scanner import run_gitlab_scan
            from app.watcher.helpers import get_watcher_config
            watcher_config = get_watcher_config()
            result = run_gitlab_scan(watcher_config, INSTANCE_DIR)
            logger.info(
                "GitLab scan: %d projects, %d commits, %d MRs in %.1fs",
                result.get("projects_scanned", 0),
                result.get("new_commits", 0),
                result.get("new_mrs", 0),
                result.get("duration_seconds", 0),
            )
        except Exception as e:
            logger.error("GitLab scan failed: %s", e, exc_info=True)
        time.sleep(interval)


_gitlab_thread = threading.Thread(target=_gitlab_scan_loop, daemon=True)
_gitlab_thread.start()


@app.route("/api/trigger-report", methods=["POST"])
def trigger_report():
    """POST /api/trigger-report — generate and send the daily report.

    Query params:
        date: YYYY-MM-DD (optional, defaults to today UTC)

    Returns JSON: {"status": "sent"|"no_activity"|"error", "date": "YYYY-MM-DD"}
    """
    from app.governor_daily_report import send_daily_report, _collect_day_data

    date_str = request.args.get("date")
    if date_str:
        try:
            target_date = date.fromisoformat(date_str)
        except ValueError:
            return jsonify({"status": "error", "error": f"Invalid date: {date_str}"}), 400
    else:
        target_date = datetime.now(timezone.utc).date()

    try:
        data = _collect_day_data(target_date)
        has_activity = data.get("events_count", 0) > 0
        send_daily_report(target_date=target_date, notify=True)
        status = "sent" if has_activity else "no_activity"
        return jsonify({"status": status, "date": target_date.isoformat()})
    except Exception as e:
        logger.exception("Failed to generate daily report")
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/api/advisor-scan", methods=["POST"])
def advisor_scan():
    """POST /api/advisor-scan — launch advisor scan in background thread.

    Query params:
        full: if present, run full scan (default: incremental)

    Returns JSON: {"status": "started", "mode": "full"|"incremental"}
    Check progress via GET /api/advisor-scan/status
    """
    import threading
    from app.advisor.indexer import run_full_scan, run_incremental_scan
    from app.advisor.helpers import get_advisor_config

    config = get_advisor_config()
    full = request.args.get("full") is not None

    def _run_scan():
        try:
            if full:
                run_full_scan(config)
            else:
                run_incremental_scan(config)
        except Exception as e:
            logger.exception("Advisor scan failed: %s", e)

    thread = threading.Thread(target=_run_scan, daemon=True)
    thread.start()
    return jsonify({"status": "started", "mode": "full" if full else "incremental"})


@app.route("/api/advisor-scan/status", methods=["GET"])
def advisor_scan_status():
    """GET /api/advisor-scan/status — return scan progress."""
    from app.advisor.indexer import get_scan_progress
    progress = get_scan_progress()
    if not progress:
        return jsonify({"status": "idle"})
    return jsonify(progress)


@app.route("/api/watcher-scan", methods=["POST"])
def watcher_scan():
    """POST /api/watcher-scan — trigger GitLab scan immediately."""
    import threading as _t
    from app.watcher.scanner import run_gitlab_scan
    from app.watcher.helpers import get_watcher_config

    def _run():
        try:
            config = get_watcher_config()
            result = run_gitlab_scan(config, INSTANCE_DIR)
            logger.info("Manual GitLab scan: %s", result)
        except Exception as e:
            logger.error("Manual GitLab scan failed: %s", e, exc_info=True)

    _t.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/governor/help")
def governor_help_page():
    """Governor command help — full reference for all CLI commands."""
    from app.governor_cli import HELP_COMMANDS, VERSION

    order = [
        "watcher", "advisor", "scan",
        "status", "report",
        "budget", "keys",
        "vault", "env",
        "autonomy", "rollout", "offboard",
        "simulate", "tunnel",
    ]
    commands = []
    for name in order:
        info = HELP_COMMANDS.get(name)
        if not info:
            continue
        search_parts = [name, info["desc"], info["usage"]]
        for a_name, a_desc in info["actions"].items():
            search_parts.extend([a_name, a_desc])
        for ex in info.get("examples", []):
            search_parts.append(ex)
        commands.append({
            "name": name,
            "desc": info["desc"],
            "usage": info["usage"],
            "actions": list(info["actions"].items()),
            "flags": info.get("flags", {}),
            "examples": info.get("examples", []),
            "search_text": " ".join(search_parts).lower(),
        })

    return render_template("help_commands.html", commands=commands, version=VERSION)


@app.route("/governor")
def governor_page():
    """Governor dashboard — CEO overview with feed, actions, health."""
    health = {}
    try:
        from app.health import get_health_report
        health = get_health_report()
    except Exception:
        health = {"status": "unknown"}

    events = []
    try:
        from app.watcher.journal import read_events
        events = read_events(INSTANCE_DIR, days=7, limit=20)
    except Exception:
        pass

    return render_template("governor.html", health=health, events=events)


@app.route("/api/governor/events")
def api_governor_events():
    """JSON feed of recent governor events."""
    limit = request.args.get("limit", 20, type=int)
    try:
        from app.watcher.journal import read_events
        events = read_events(INSTANCE_DIR, days=7, limit=limit)
        return jsonify({"ok": True, "events": events, "count": len(events)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "events": []})


@app.route("/api/governor/action", methods=["POST"])
def api_governor_action():
    """Execute a governor action and return the result."""
    data = request.get_json(silent=True) or {}
    action = data.get("action", "")

    action_map = {
        "report_daily": ("report", "daily --notify"),
        "status": ("status", ""),
        "advisor_scan": ("advisor", "scan"),
    }

    if action not in action_map:
        return jsonify({"ok": False, "error": f"Action inconnue: {action}"})

    command, args = action_map[action]
    try:
        from app.governor_cli import dispatch_skill
        import argparse
        flags = argparse.Namespace(
            output_json=False, notify=True, dry_run=False, verbose=False
        )
        parts = args.split(None, 1) if args else ["", ""]
        skill_action = parts[0] if parts else ""
        extra = parts[1] if len(parts) > 1 else ""
        exit_code, result = dispatch_skill(command, skill_action, extra, flags)
        return jsonify({"ok": exit_code == 0, "action": action, "result": result})
    except Exception as e:
        return jsonify({"ok": False, "action": action, "error": str(e)})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, threaded=True)
