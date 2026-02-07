"""Handler for /shutdown command — signals both processes to exit."""

from app.shutdown_manager import request_shutdown


def handle(ctx):
    """Request a full shutdown of both agent loop and bridge."""
    request_shutdown(str(ctx.koan_root))
    return "🔌 Shutdown requested. Both agent loop and bridge will stop."
