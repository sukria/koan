"""Kōan review skill -- queue a code review mission."""

from app.github_url_parser import parse_github_url
from app.github_skill_helpers import (
    extract_github_url,
    format_project_not_found_error,
    format_success_message,
    queue_github_mission,
    resolve_project_for_repo,
)


def handle(ctx):
    """Handle /review command -- queue a code review mission.

    Usage:
        /review https://github.com/owner/repo/pull/42
        /review https://github.com/owner/repo/issues/42
    """
    args = ctx.args.strip()

    if not args:
        return (
            "Usage: /review <github-url>\n"
            "Ex: /review https://github.com/sukria/koan/pull/42\n"
            "Ex: /review https://github.com/sukria/koan/issues/42\n\n"
            "Queues a code review mission."
        )

    result = extract_github_url(args, url_type="pr-or-issue")
    if not result:
        return (
            "\u274c No valid GitHub PR or issue URL found.\n"
            "Ex: /review https://github.com/owner/repo/pull/123"
        )

    url, _ = result

    try:
        owner, repo, url_type, number = parse_github_url(url)
    except ValueError as e:
        return f"\u274c {e}"

    project_path, project_name = resolve_project_for_repo(repo, owner=owner)
    if not project_path:
        return format_project_not_found_error(repo)

    queue_github_mission(ctx, "review", url, project_name)

    type_label = "PR" if url_type == "pull" else "issue"
    return f"Review queued for {format_success_message(type_label, number, owner, repo)}"
