"""Koan language skill — set/reset reply language preference."""

# Language shortcut commands: /french, /english, and their aliases
_LANGUAGE_SHORTCUTS = {
    "french": "french",
    "fr": "french",
    "francais": "french",
    "français": "french",
    "english": "english",
    "en": "english",
    "anglais": "english",
}


def handle(ctx):
    """Handle /language command and language shortcut commands."""
    from app.language_preference import get_language, set_language, reset_language

    # Check if this is a shortcut command (/french, /english, /fr, /en, etc.)
    shortcut_lang = _LANGUAGE_SHORTCUTS.get(ctx.command_name)
    if shortcut_lang:
        set_language(shortcut_lang)
        return f"🌐 Language set to {shortcut_lang}. All my replies will now be in {shortcut_lang}."

    arg = ctx.args.strip()

    if not arg:
        usage = "\n\nUsage:\n/language <language> -- set reply language\n/language reset -- use input language\n/french -- shortcut for French\n/english -- shortcut for English"
        current = get_language()
        if current:
            return f"🌐 Current language: {current}{usage}"
        return f"🌐 No language override set (replying in input language).{usage}"

    if arg.lower() == "reset":
        reset_language()
        return "🌐 Language preference reset. I'll reply in the same language as your messages."

    set_language(arg)
    return f"🌐 Language set to {arg.lower()}. All my replies will now be in {arg.lower()}."
