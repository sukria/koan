"""Kōan review skill -- queue a code review mission."""

from app.github_url_parser import parse_github_url
from app.github_skill_helpers import handle_github_skill


def handle(ctx):
    """Handle /review command -- queue a code review mission.

    Usage:
        /review https://github.com/owner/repo/pull/42
        /review https://github.com/owner/repo/issues/42
    """
    return handle_github_skill(
        ctx,
        command="review",
        url_type="pr-or-issue",
        parse_func=parse_github_url,
        success_prefix="Review queued",
    )
