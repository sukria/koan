"""Koan chat skill — force chat mode bypassing mission detection."""


def handle(ctx):
    """Force chat mode. Returns None to signal the caller should use handle_chat."""
    if not ctx.args:
        return "Usage: /chat <message>\nForces chat mode for messages that look like missions."
    # Return None to signal that the caller should route to handle_chat
    # This is a special case — the chat skill is a routing directive, not a handler
    return None
