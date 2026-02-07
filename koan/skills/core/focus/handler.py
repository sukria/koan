"""Kōan focus/unfocus skill — toggle mission-only focus mode."""

from app.focus_manager import (
    check_focus,
    create_focus,
    parse_duration,
    remove_focus,
    DEFAULT_FOCUS_DURATION,
)


def handle(ctx):
    """Toggle focus mode on or off."""
    koan_root = str(ctx.koan_root)

    if ctx.command_name == "unfocus":
        state = check_focus(koan_root)
        if state:
            remove_focus(koan_root)
            return "🎯 Focus mode OFF. Back to normal: missions, reflection, exploration."
        return "🎯 Not in focus mode."

    # /focus [duration]
    args = ctx.args.strip() if ctx.args else ""

    # Parse optional duration
    duration = DEFAULT_FOCUS_DURATION
    if args:
        parsed = parse_duration(args)
        if parsed is not None:
            duration = parsed
        else:
            return f"❌ Invalid duration: '{args}'. Examples: 5h, 3h30m, 90m"

    state = create_focus(koan_root, duration=duration, reason="missions")
    remaining = state.remaining_display()
    return (
        f"🎯 Focus mode ON for {remaining}. "
        "Missions only — no reflection, no free exploration. "
        "Use /unfocus to deactivate early."
    )
