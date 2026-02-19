"""Koan fix skill -- queue a fix mission for a GitHub issue."""

from app.github_url_parser import parse_issue_url
from app.github_skill_helpers import handle_github_skill


def handle(ctx):
    """Handle /fix command -- queue a mission to fix a GitHub issue.

    Usage:
        /fix https://github.com/owner/repo/issues/42
        /fix https://github.com/owner/repo/issues/42 focus on backend only
    """
    return handle_github_skill(
        ctx,
        command="fix",
        url_type="issue",
        parse_func=parse_issue_url,
        success_prefix="Fix queued",
    )
