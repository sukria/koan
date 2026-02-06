"""Koan journal skill — view journal entries."""

import re
from datetime import date, timedelta


def handle(ctx):
    """Handle /log [project] [date] command."""
    from app.utils import get_latest_journal

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

    return get_latest_journal(ctx.instance_dir, project=project, target_date=target_date)
