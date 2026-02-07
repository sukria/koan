"""Koan live progress skill — show current mission progress."""


def _read_live_progress(instance_dir):
    """Read full live progress from journal/pending.md.

    Returns the mission header and all progress lines,
    or None if no mission is running.
    """
    pending_path = instance_dir / "journal" / "pending.md"
    if not pending_path.exists():
        return None

    content = pending_path.read_text().strip()
    if not content:
        return None

    return content


def handle(ctx):
    """Handle /live command — show live progress of current mission."""
    progress = _read_live_progress(ctx.instance_dir)
    if not progress:
        return "No mission running."
    return progress
