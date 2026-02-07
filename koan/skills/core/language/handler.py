"""Koan language skill — set/reset reply language preference."""


def handle(ctx):
    """Handle /language command."""
    from app.language_preference import get_language, set_language, reset_language

    arg = ctx.args.strip()

    if not arg:
        usage = "\n\nUsage:\n/language <language> -- set reply language\n/language reset -- use input language"
        current = get_language()
        if current:
            return f"🌐 Current language: {current}{usage}"
        return f"🌐 No language override set (replying in input language).{usage}"

    if arg.lower() == "reset":
        reset_language()
        return "🌐 Language preference reset. I'll reply in the same language as your messages."

    set_language(arg)
    return f"🌐 Language set to {arg.lower()}. All my replies will now be in {arg.lower()}."
