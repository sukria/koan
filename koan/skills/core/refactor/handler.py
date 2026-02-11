"""Kōan refactor skill -- queue a refactoring mission."""

from app.github_url_parser import parse_github_url
from app.github_skill_helpers import (
    extract_github_url,
    format_project_not_found_error,
    format_success_message,
    queue_github_mission,
    resolve_project_for_repo,
)


def handle(ctx):
    """Handle /refactor command -- queue a refactoring mission.

    Usage:
        /refactor https://github.com/owner/repo/pull/42
        /refactor https://github.com/owner/repo/issues/42
        /refactor src/utils.py
    """
    args = ctx.args.strip()

    if not args:
        return (
            "Usage: /refactor <github-url-or-path>\n"
            "Ex: /refactor https://github.com/sukria/koan/pull/42\n"
            "Ex: /refactor src/utils.py\n\n"
            "Queues a refactoring mission."
        )

    # Try to extract a GitHub URL first
    result = extract_github_url(args, url_type="pr-or-issue")
    if result:
        url, _ = result

        try:
            owner, repo, url_type, number = parse_github_url(url)
        except ValueError as e:
            return f"\u274c {e}"

        project_path, project_name = resolve_project_for_repo(repo, owner=owner)
        if not project_path:
            return format_project_not_found_error(repo)

        queue_github_mission(ctx, "refactor", url, project_name)

        type_label = "PR" if url_type == "pull" else "issue"
        return f"Refactor queued for {format_success_message(type_label, number, owner, repo)}"

    # No URL found - treat as a file path
    from app.utils import insert_pending_mission

    file_path = args.strip()
    mission_entry = f"- /refactor {file_path}"
    missions_path = ctx.instance_dir / "missions.md"
    insert_pending_mission(missions_path, mission_entry)

    return f"Refactor queued for {file_path}"
