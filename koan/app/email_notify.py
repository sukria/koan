#!/usr/bin/env python3
"""
Koan -- Email notification to owner

Sends email to a single configured recipient (the human).
Uses stdlib smtplib — no extra dependencies.

Security:
- Single recipient only (EMAIL_KOAN_OWNER env var)
- Rate limited (max_per_day, default 5)
- Duplicate detection (content hash, 24h window)
- Audit logging to journal

Usage from Python:
    from app.email_notify import send_owner_email
    send_owner_email("Daily digest", "Here's what happened today...")

Usage from shell:
    python3 -m app.email_notify "Subject" "Body text"
"""

import hashlib
import json
import os
import smtplib
import sys
import time
from email.mime.text import MIMEText
from pathlib import Path
from typing import Tuple

from app.utils import load_config, load_dotenv


# Rate limit file location (inside instance/)
def _get_cooldown_path() -> Path:
    koan_root = Path(os.environ.get("KOAN_ROOT", "."))
    return koan_root / "instance" / ".email-cooldown.json"


def _get_email_config() -> dict:
    """Get email config from config.yaml with defaults."""
    config = load_config()
    defaults = {
        "enabled": False,
        "max_per_day": 5,
        "require_approval": False,
    }
    email_cfg = config.get("email", {})
    return {k: email_cfg.get(k, v) for k, v in defaults.items()}


def _get_smtp_config() -> dict:
    """Get SMTP credentials from environment variables."""
    load_dotenv()
    return {
        "host": os.environ.get("KOAN_SMTP_HOST", ""),
        "port": int(os.environ.get("KOAN_SMTP_PORT", "587")),
        "user": os.environ.get("KOAN_SMTP_USER", ""),
        "password": os.environ.get("KOAN_SMTP_PASSWORD", ""),
        "recipient": os.environ.get("EMAIL_KOAN_OWNER", ""),
    }


def _load_cooldown() -> list:
    """Load cooldown records. Each record: {timestamp, content_hash}."""
    path = _get_cooldown_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, OSError):
        return []


def _save_cooldown(records: list):
    """Save cooldown records."""
    path = _get_cooldown_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, indent=2))


def _prune_old_records(records: list) -> list:
    """Remove records older than 24 hours."""
    cutoff = time.time() - 86400
    return [r for r in records if r.get("timestamp", 0) > cutoff]


def _content_hash(subject: str, body: str) -> str:
    """Hash subject + first 100 chars of body for duplicate detection."""
    content = f"{subject}:{body[:100]}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def can_send_email() -> Tuple[bool, str]:
    """Check if email can be sent right now.

    Returns:
        (allowed, reason) — reason explains rejection if not allowed.
    """
    config = _get_email_config()

    if not config["enabled"]:
        return False, "Email not enabled in config.yaml"

    smtp = _get_smtp_config()
    if not smtp["host"] or not smtp["user"] or not smtp["password"]:
        return False, "SMTP credentials not configured (KOAN_SMTP_HOST, KOAN_SMTP_USER, KOAN_SMTP_PASSWORD)"

    if not smtp["recipient"]:
        return False, "No recipient configured (EMAIL_KOAN_OWNER)"

    records = _prune_old_records(_load_cooldown())
    max_per_day = config["max_per_day"]
    if len(records) >= max_per_day:
        return False, f"Rate limit reached ({max_per_day} emails per 24h)"

    return True, "OK"


def is_duplicate(subject: str, body: str) -> bool:
    """Check if this email was already sent in the last 24 hours."""
    records = _prune_old_records(_load_cooldown())
    h = _content_hash(subject, body)
    return any(r.get("content_hash") == h for r in records)


def get_email_stats() -> dict:
    """Return email sending statistics.

    Returns:
        {sent_today: int, remaining: int, max_per_day: int, last_sent: float or None}
    """
    config = _get_email_config()
    records = _prune_old_records(_load_cooldown())
    max_per_day = config["max_per_day"]
    last_sent = max((r.get("timestamp", 0) for r in records), default=None) if records else None
    return {
        "sent_today": len(records),
        "remaining": max(0, max_per_day - len(records)),
        "max_per_day": max_per_day,
        "last_sent": last_sent,
        "enabled": config["enabled"],
    }


def send_owner_email(subject: str, body: str, skip_duplicate_check: bool = False) -> bool:
    """Send email to the owner. Returns True on success.

    Respects rate limits and duplicate detection.

    Args:
        subject: Email subject line
        body: Plain text email body
        skip_duplicate_check: If True, send even if duplicate detected

    Returns:
        True if email was sent successfully
    """
    allowed, reason = can_send_email()
    if not allowed:
        print(f"[email] Cannot send: {reason}", file=sys.stderr)
        return False

    if not skip_duplicate_check and is_duplicate(subject, body):
        print(f"[email] Skipping duplicate email: {subject}", file=sys.stderr)
        return False

    smtp = _get_smtp_config()

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"[Koan] {subject}"
    msg["From"] = smtp["user"]
    msg["To"] = smtp["recipient"]

    try:
        with smtplib.SMTP(smtp["host"], smtp["port"], timeout=30) as server:
            server.starttls()
            server.login(smtp["user"], smtp["password"])
            server.send_message(msg)

        # Record success in cooldown
        records = _prune_old_records(_load_cooldown())
        records.append({
            "timestamp": time.time(),
            "content_hash": _content_hash(subject, body),
            "subject": subject,
        })
        _save_cooldown(records)

        print(f"[email] Sent: {subject}", file=sys.stderr)
        return True

    except smtplib.SMTPAuthenticationError as e:
        print(f"[email] Authentication failed: {e}", file=sys.stderr)
        return False
    except smtplib.SMTPException as e:
        print(f"[email] SMTP error: {e}", file=sys.stderr)
        return False
    except OSError as e:
        print(f"[email] Connection error: {e}", file=sys.stderr)
        return False


def send_session_digest(project_name: str, summary: str) -> bool:
    """Send a session digest email when budget is exhausted or session ends.

    Args:
        project_name: Current project name
        summary: Session summary text (from journal)

    Returns:
        True if email was sent
    """
    from datetime import date
    subject = f"Session digest — {project_name} ({date.today():%Y-%m-%d})"
    return send_owner_email(subject, summary)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <subject> <body>", file=sys.stderr)
        sys.exit(1)

    subject = sys.argv[1]
    body = sys.argv[2]
    success = send_owner_email(subject, body)
    sys.exit(0 if success else 1)
