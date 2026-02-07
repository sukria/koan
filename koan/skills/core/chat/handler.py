"""Koan chat skill — force chat mode bypassing mission detection."""


def handle(ctx):
    """Force chat mode — routes the message directly to handle_chat.

    This is a routing directive: it bypasses the mission detection heuristic
    so messages like "fix the login bug" get treated as conversation, not missions.
    """
    if not ctx.args:
        return "Usage: /chat <message>\nForces chat mode for messages that look like missions."

    if ctx.handle_chat is not None:
        ctx.handle_chat(ctx.args)
        # Return empty string to signal "handled, don't send anything else"
        return ""

    return "⚠️ Chat handler not available."
