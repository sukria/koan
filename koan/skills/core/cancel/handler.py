"""Koan cancel skill -- cancel pending missions from the queue."""

import re


def handle(ctx):
    """Handle /cancel command.

    /cancel        — show numbered list of pending missions
    /cancel 3      — cancel mission #3
    /cancel auth   — cancel first mission matching keyword "auth"
    """
    args = ctx.args.strip()
    missions_file = ctx.instance_dir / "missions.md"

    if not args:
        return _list_pending(missions_file)

    return _cancel_mission(missions_file, args)


def _list_pending(missions_file):
    """Show numbered list of pending missions for selection."""
    if not missions_file.exists():
        return "No pending missions."

    from app.missions import list_pending

    pending = list_pending(missions_file.read_text())

    if not pending:
        return "No pending missions."

    parts = ["Pending missions:"]
    for i, m in enumerate(pending, 1):
        display = _clean_display(m)
        parts.append(f"  {i}. {display}")

    parts.append("\nUsage: /cancel 3 or /cancel fix auth")
    return "\n".join(parts)


def _cancel_mission(missions_file, identifier):
    """Cancel a mission by number or keyword."""
    from app.missions import cancel_pending_mission
    from app.utils import modify_missions_file

    cancelled_text = None

    def _transform(content):
        nonlocal cancelled_text
        updated, cancelled_text = cancel_pending_mission(content, identifier)
        return updated

    try:
        modify_missions_file(missions_file, _transform)
    except ValueError as e:
        return str(e)

    if cancelled_text is None:
        return "Error during cancellation."

    display = _clean_display(cancelled_text)
    return f"Mission cancelled: {display}"


def _clean_display(text):
    """Clean a mission line for display."""
    # Strip leading "- "
    if text.startswith("- "):
        text = text[2:]

    # Strip project tag but keep project name as prefix
    tag_match = re.search(r'\[projec?t:([a-zA-Z0-9_-]+)\]\s*', text)
    if tag_match:
        project = tag_match.group(1)
        text = re.sub(r'\[projec?t:[a-zA-Z0-9_-]+\]\s*', '', text)
        text = f"[{project}] {text}"

    # Truncate for readability
    if len(text) > 120:
        text = text[:117] + "..."

    return text
