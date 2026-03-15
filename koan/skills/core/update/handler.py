"""Handler for /update command (alias: /upgrade).

Pulls latest code from upstream/main, then restarts both processes.
"""

from app.skills import SkillContext


def handle(ctx: SkillContext) -> str:
    """Pull latest code from upstream and restart both processes."""
    from app.update_manager import pull_upstream
    from app.restart_manager import request_restart
    from app.pause_manager import remove_pause

    # Pull latest code
    result = pull_upstream(ctx.koan_root)

    if not result.success:
        return f"❌ Update failed: {result.error}"

    if not result.changed:
        return "✅ Already up to date. No restart needed. Use /restart if needed."

    # New code pulled -- clear pause and restart
    remove_pause(str(ctx.koan_root))
    request_restart(str(ctx.koan_root))

    msg = f"🔄 {result.summary()}\nRestarting both processes..."
    if result.stashed:
        msg += "\n⚠️ Dirty work was auto-stashed."
    return msg
