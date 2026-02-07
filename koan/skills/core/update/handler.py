"""Handler for /update and /restart commands.

/update: Pull latest code from upstream/main, then restart both processes.
/restart: Restart both processes without updating code.
"""

from app.skills import SkillContext


def handle(ctx: SkillContext) -> str:
    """Dispatch /update or /restart based on command name."""
    if ctx.command_name == "restart":
        return _handle_restart(ctx)
    return _handle_update(ctx)


def _handle_restart(ctx: SkillContext) -> str:
    """Restart both bridge and run loop processes."""
    from app.restart_manager import request_restart
    from app.pause_manager import remove_pause

    # Clear any pause state -- restart should start fresh
    remove_pause(str(ctx.koan_root))

    request_restart(ctx.koan_root)
    return "🔄 Restart requested. Both processes will restart momentarily."


def _handle_update(ctx: SkillContext) -> str:
    """Pull latest code from upstream and restart."""
    from app.update_manager import pull_upstream
    from app.restart_manager import request_restart
    from app.pause_manager import remove_pause

    # Pull latest code
    result = pull_upstream(ctx.koan_root)

    if not result.success:
        return f"❌ Update failed: {result.error}"

    if not result.changed:
        # No new code -- ask if they still want to restart
        return "✅ Already up to date. No restart needed."

    # New code pulled -- clear pause and restart
    remove_pause(str(ctx.koan_root))
    request_restart(ctx.koan_root)

    msg = f"🔄 {result.summary()}\nRestarting both processes..."
    if result.stashed:
        msg += "\n⚠️ Dirty work was auto-stashed."
    return msg
