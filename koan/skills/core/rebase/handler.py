"""Koan rebase skill -- queue a PR rebase mission."""

import re


def handle(ctx):
    """Handle /rebase command -- queue a rebase mission for a PR.

    Usage:
        /rebase https://github.com/owner/repo/pull/123

    Queues a mission that rebases the PR branch onto its target,
    reads all comments for context, and pushes the result.
    """
    args = ctx.args.strip()

    if not args:
        return (
            "Usage: /rebase <github-pr-url>\n"
            "Ex: /rebase https://github.com/sukria/koan/pull/42\n\n"
            "Queues a mission that rebases the PR branch onto its target, "
            "reads comments for context, and force-pushes the result."
        )

    # Extract URL from args
    url_match = re.search(r'https?://github\.com/[^\s]+/pull/\d+', args)
    if not url_match:
        return (
            "\u274c No valid GitHub PR URL found.\n"
            "Ex: /rebase https://github.com/owner/repo/pull/123"
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

    # Build CLI command for the mission
    koan_root = ctx.koan_root
    cmd = (
        f"cd {koan_root}/koan && "
        f"{koan_root}/.venv/bin/python3 -m app.rebase_pr "
        f"{pr_url} --project-path {project_path}"
    )

    # Queue the mission
    mission_entry = (
        f"- [project:{project_name}] Rebase PR #{pr_number} "
        f"({owner}/{repo}) \u2014 run: `{cmd}`"
    )
    missions_path = ctx.instance_dir / "missions.md"
    insert_pending_mission(missions_path, mission_entry)

    return f"Rebase queued for PR #{pr_number} ({owner}/{repo})"
