"""Kōan — Text utilities for messaging delivery.

Shared helpers for cleaning CLI output and stripping markdown formatting
before sending to messaging providers (Telegram, Slack, etc.).

These functions ensure consistent text processing across all message paths:
- Chat responses (awake.py)
- Outbox formatting (format_outbox.py)
- AI runner responses (ai_runner.py)
"""

import re

# Markdown symbols to strip (order matters: longer patterns first)
_MARKDOWN_SYMBOLS = ("```", "**", "__", "~~")

# CLI error patterns to filter from responses
_CLI_ERROR_RE = re.compile(r'^Error:.*max turns', re.IGNORECASE)

# Heading markers: # through ######
_HEADING_RE = re.compile(r'^#{1,6}\s+', re.MULTILINE)

# Default max message length for smartphone readability
DEFAULT_MAX_LENGTH = 2000


def strip_markdown(text: str) -> str:
    """Strip markdown formatting artifacts from text.

    Removes code fences, bold, underline, strikethrough, and heading markers.
    Designed for plain-text messaging (Telegram, Slack).

    Args:
        text: Raw text potentially containing markdown.

    Returns:
        Text with markdown artifacts removed.
    """
    for symbol in _MARKDOWN_SYMBOLS:
        text = text.replace(symbol, "")
    text = _HEADING_RE.sub("", text)
    return text


def clean_cli_response(text: str, max_length: int = DEFAULT_MAX_LENGTH) -> str:
    """Clean Claude CLI output for messaging delivery.

    Strips CLI error artifacts, markdown formatting, and truncates for
    smartphone reading. Used by chat responses and AI runner output.

    Args:
        text: Raw CLI stdout text.
        max_length: Maximum character length (default 2000).

    Returns:
        Cleaned, truncated plain text.
    """
    lines = text.splitlines()
    lines = [line for line in lines if not _CLI_ERROR_RE.match(line)]
    cleaned = "\n".join(lines).strip()
    cleaned = strip_markdown(cleaned)
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length - 3] + "..."
    return cleaned.strip()
