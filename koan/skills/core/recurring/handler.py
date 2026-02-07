"""Koan recurring skill -- manage recurring missions (hourly, daily, weekly)."""


def handle(ctx):
    """Handle /daily, /hourly, /weekly, /recurring, /cancel-recurring commands.

    /daily <text>           — add a daily recurring mission
    /hourly <text>          — add an hourly recurring mission
    /weekly <text>          — add a weekly recurring mission
    /recurring              — list all recurring missions
    /cancel-recurring [n]   — cancel a recurring mission by number or keyword
    """
    command = ctx.command_name

    if command in ("daily", "hourly", "weekly"):
        return _handle_add(ctx, command)
    elif command == "recurring":
        return _handle_list(ctx)
    elif command == "cancel-recurring":
        return _handle_cancel(ctx)

    return None


def _handle_add(ctx, frequency):
    """Add a recurring mission with the given frequency."""
    body = ctx.args.strip()
    if not body:
        return f"Usage: /{frequency} <description>\nEx: /{frequency} check open pull requests"

    from app.utils import parse_project
    from app.recurring import add_recurring

    project, text = parse_project(body)
    recurring_path = ctx.instance_dir / "recurring.json"

    try:
        add_recurring(recurring_path, frequency, text, project)
        ack = f"Recurring mission added ({frequency})"
        if project:
            ack += f" [project:{project}]"
        ack += f":\n\n{text}"
        return ack
    except ValueError as e:
        return str(e)


def _handle_list(ctx):
    """List all recurring missions."""
    from app.recurring import list_recurring, format_recurring_list

    recurring_path = ctx.instance_dir / "recurring.json"
    missions = list_recurring(recurring_path)
    return format_recurring_list(missions)


def _handle_cancel(ctx):
    """Cancel a recurring mission by number or keyword."""
    from app.recurring import list_recurring, format_recurring_list, remove_recurring

    recurring_path = ctx.instance_dir / "recurring.json"
    identifier = ctx.args.strip()

    if not identifier:
        missions = list_recurring(recurring_path)
        if missions:
            msg = format_recurring_list(missions)
            msg += "\n\nUsage: /cancel-recurring <number or keyword>"
            return msg
        return "No recurring missions to cancel."

    try:
        removed = remove_recurring(recurring_path, identifier)
        return f"Recurring mission removed: {removed}"
    except ValueError as e:
        return str(e)
