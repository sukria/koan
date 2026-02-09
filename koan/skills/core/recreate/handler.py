"""Kōan recreate skill -- queue a PR recreation mission."""

import re


def handle(ctx):
    """Handle /recreate command -- queue a mission to recreate a PR from scratch.

    Usage:
        /recreate https://github.com/owner/repo/pull/123

    Unlike /rebase which preserves the branch history, /recreate reads the
    original PR to understand its intent, then reimplements the feature
    from scratch on top of the current upstream target.  Use this when a
    branch has diverged too far for a clean rebase.
    """
    args = ctx.args.strip()

    if not args:
        return (
            "Usage: /recreate <github-pr-url>\n"
            "Ex: /recreate https://github.com/sukria/koan/pull/42\n\n"
            "Reads the original PR to understand intent, then reimplements "
            "the feature from scratch on current upstream. Use when a branch "
            "has diverged too far for a clean rebase."
        )

    # Extract URL from args
    url_match = re.search(r'https?://github\.com/[^\s]+/pull/\d+', args)
    if not url_match:
        return (
            "\u274c No valid GitHub PR URL found.\n"
            "Ex: /recreate https://github.com/owner/repo/pull/123"
        )

    pr_url = url_match.group(0).split("#")[0]

    from app.pr_review import parse_pr_url
    from app.utils import get_known_projects, insert_pending_mission, resolve_project_path

    try:
        owner, repo, pr_number = parse_pr_url(pr_url)
    except ValueError as e:
        return str(e)

    # Determine project path and name
    project_path = resolve_project_path(repo)
    if not project_path:
        known = ", ".join(n for n, _ in get_known_projects()) or "none"
        return (
            f"\u274c Could not find local project matching repo '{repo}'.\n"
            f"Known projects: {known}"
        )

    # Resolve project name for the mission tag
    project_name = None
    for name, path in get_known_projects():
        if path == project_path:
            project_name = name
            break
    if not project_name:
        project_name = repo

    # Queue the mission with clean format
    mission_entry = f"- [project:{project_name}] /recreate {pr_url}"
    missions_path = ctx.instance_dir / "missions.md"
    insert_pending_mission(missions_path, mission_entry)

    return f"Recreate queued for PR #{pr_number} ({owner}/{repo})"
