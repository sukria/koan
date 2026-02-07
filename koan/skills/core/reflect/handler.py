"""Koan reflect skill — write reflections to the shared journal."""

import fcntl
from datetime import datetime


def handle(ctx):
    """Handle /reflect <text> — append to shared journal."""
    message = ctx.args.strip()
    if not message:
        return "Usage: /reflect <your reflection>"

    shared_journal = ctx.instance_dir / "shared-journal.md"

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"\n## Human -- {timestamp}\n\n{message}\n"

    shared_journal.parent.mkdir(parents=True, exist_ok=True)
    with open(shared_journal, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(entry)

    return "📝 Noted in the shared journal. I'll reflect on it."
