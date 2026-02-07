"""Koan verbose/silent skill — toggle progress update verbosity."""


def handle(ctx):
    """Toggle verbose mode on or off."""
    verbose_file = ctx.koan_root / ".koan-verbose"

    if ctx.command_name == "silent":
        if verbose_file.exists():
            verbose_file.unlink()
            return "🔕 Verbose mode OFF. Silent until conclusion."
        return "🔕 Already in silent mode."
    else:
        verbose_file.write_text("VERBOSE")
        return "🔔 Verbose mode ON. I'll send you each progress update."
