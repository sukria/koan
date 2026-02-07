"""Koan priority skill -- reorder pending missions in the queue."""


def handle(ctx):
    """Handle /priority command.

    /priority          — show queue with usage hint
    /priority 3        — move mission #3 to top of queue
    /priority 5 2      — move mission #5 to position 2
    """
    args = ctx.args.strip()
    missions_file = ctx.instance_dir / "missions.md"

    if not args:
        return _show_queue_with_hint(missions_file)

    return _reorder(missions_file, args)


def _show_queue_with_hint(missions_file):
    """Show queue with usage hint when /priority is called bare."""
    if not missions_file.exists():
        return "ℹ️ Queue is empty.\n\nUsage: /priority <n>"

    from app.missions import list_pending, clean_mission_display

    pending = list_pending(missions_file.read_text())
    if not pending:
        return "ℹ️ Queue is empty.\n\nUsage: /priority <n>"

    parts = ["PENDING"]
    for i, m in enumerate(pending, 1):
        display = clean_mission_display(m)
        parts.append(f"  {i}. {display}")

    parts.append("\nUsage: /priority <n> — bumps mission #n to the top")
    parts.append("       /priority <n> <m> — moves mission #n to position m")
    return "\n".join(parts)


def _reorder(missions_file, args):
    """Reorder a pending mission."""
    from app.missions import reorder_mission, clean_mission_display
    from app.utils import modify_missions_file

    parts = args.split()
    try:
        position = int(parts[0])
    except ValueError:
        return f"⚠️ Invalid number: {parts[0]}\nUsage: /priority <n>"

    target = 1
    if len(parts) > 1:
        try:
            target = int(parts[1])
        except ValueError:
            return f"⚠️ Invalid target: {parts[1]}\nUsage: /priority <n> [target]"

    moved_display = None

    def _transform(content):
        nonlocal moved_display
        updated, moved_display = reorder_mission(content, position, target)
        return updated

    try:
        modify_missions_file(missions_file, _transform)
    except ValueError as e:
        return f"⚠️ {e}"

    if moved_display is None:
        return "⚠️ Error during reorder."

    if target == 1:
        return f"⬆️ Bumped to top: {moved_display}"
    else:
        return f"🔀 Moved to position {target}: {moved_display}"
