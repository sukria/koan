"""Koan cancel skill -- cancel pending missions from the queue."""


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
        return "ℹ️ No pending missions."

    from app.missions import list_pending, clean_mission_display

    pending = list_pending(missions_file.read_text())

    if not pending:
        return "ℹ️ No pending missions."

    parts = ["Pending missions:\n"]
    for i, m in enumerate(pending, 1):
        display = clean_mission_display(m)
        parts.append(f"  {i}. {display}")

    parts.append("\nReply /cancel <number> to cancel a mission.")
    return "\n".join(parts)


def _cancel_mission(missions_file, identifier):
    """Cancel a mission by number or keyword."""
    from app.missions import cancel_pending_mission, clean_mission_display
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
        return "⚠️ Error during cancellation."

    display = clean_mission_display(cancelled_text)
    return f"🗑 Mission cancelled: {display}"
