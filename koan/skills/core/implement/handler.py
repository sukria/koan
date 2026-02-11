"""Kōan implement skill -- queue an implementation mission for a GitHub issue."""

from app.github_url_parser import parse_issue_url
from app.github_skill_helpers import (
    extract_github_url,
    format_project_not_found_error,
    format_success_message,
    queue_github_mission,
    resolve_project_for_repo,
)


def handle(ctx):
    """Handle /implement command -- queue a mission to implement a GitHub issue.

    Usage:
        /implement https://github.com/owner/repo/issues/42
        /implement https://github.com/owner/repo/issues/42 phase 1 only
    """
    args = ctx.args.strip()

    if not args:
        return (
            "Usage: /implement <github-issue-url> [context]\n"
            "Ex: /implement https://github.com/sukria/koan/issues/42\n"
            "Ex: /implement https://github.com/sukria/koan/issues/42 phase 1 only\n\n"
            "Queues a mission to implement the described issue."
        )

    result = extract_github_url(args, url_type="issue")
    if not result:
        return (
            "\u274c No valid GitHub issue URL found.\n"
            "Ex: /implement https://github.com/owner/repo/issues/123"
        )

    issue_url, context = result

    try:
        owner, repo, issue_number = parse_issue_url(issue_url)
    except ValueError as e:
        return f"\u274c {e}"

    project_path, project_name = resolve_project_for_repo(repo, owner=owner)
    if not project_path:
        return format_project_not_found_error(repo)

    queue_github_mission(ctx, "implement", issue_url, project_name, context)

    return f"Implementation queued for {format_success_message('issue', issue_number, owner, repo, context)}"
