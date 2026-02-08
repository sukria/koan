"""Kōan journal skill — view journal entries."""

import re
from datetime import date, timedelta


def _read_pending_progress(instance_dir, max_lines=5):
    """Read last progress lines from journal/pending.md."""
    pending_path = instance_dir / "journal" / "pending.md"
    if not pending_path.exists():
        return None

    content = pending_path.read_text()
    # Find lines after the --- separator
    sep_idx = content.find("\n---\n")
    if sep_idx == -1:
        return None

    after_sep = content[sep_idx + 5:]  # skip "\n---\n"
    lines = [line for line in after_sep.splitlines() if line.strip()]
    if not lines:
        return None

    last_lines = lines[-max_lines:]
    bullets = "\n".join(f"- {line}" for line in last_lines)
    return f"📡 Live progress:\n{bullets}"


def handle(ctx):
    """Handle /log [project] [date] command."""
    from app.journal import get_latest_journal

    args = ctx.args
    parts = args.split() if args else []
    project = None
    target_date = None

    if len(parts) >= 1:
        if re.match(r'^\d{4}-\d{2}-\d{2}$', parts[0]):
            target_date = parts[0]
        elif parts[0] == "yesterday":
            target_date = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            project = parts[0]

    if len(parts) >= 2 and target_date is None:
        if parts[1] == "yesterday":
            target_date = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        elif re.match(r'^\d{4}-\d{2}-\d{2}$', parts[1]):
            target_date = parts[1]

    journal = get_latest_journal(ctx.instance_dir, project=project, target_date=target_date)

    # Show pending progress at the bottom for today's journal (easier to read)
    if target_date is None:
        pending = _read_pending_progress(ctx.instance_dir)
        if pending:
            return f"{journal}\n\n{pending}"

    return journal
