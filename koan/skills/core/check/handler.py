"""Koan /check skill -- queue a check mission for a PR or issue."""

import re


# PR URL: https://github.com/owner/repo/pull/123
_PR_URL_RE = re.compile(
    r"https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)"
)
# Issue URL: https://github.com/owner/repo/issues/123
_ISSUE_URL_RE = re.compile(
    r"https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/issues/(?P<number>\d+)"
)


def handle(ctx):
    """Handle /check command -- queue a mission to check a PR or issue.

    Usage:
        /check <github-url>

    Queues a mission that inspects the PR/issue via GitHub API and
    takes action (rebase, review, plan) as needed.
    """
    args = ctx.args.strip()

    if not args:
        return (
            "Usage: /check <github-pr-or-issue-url>\n"
            "Ex: /check https://github.com/sukria/koan/pull/85\n\n"
            "Queues a mission that checks rebase/review status for PRs, "
            "or triggers /plan for updated issues."
        )

    # Validate URL format before queuing
    pr_match = _PR_URL_RE.search(args)
    issue_match = _ISSUE_URL_RE.search(args)

    if not pr_match and not issue_match:
        return (
            "\u274c No valid GitHub PR or issue URL found.\n"
            "Expected: https://github.com/owner/repo/pull/123\n"
            "      or: https://github.com/owner/repo/issues/123"
        )

    # Extract the clean URL (strip fragments/query)
    if pr_match:
        owner = pr_match.group("owner")
        repo = pr_match.group("repo")
        number = pr_match.group("number")
        url = f"https://github.com/{owner}/{repo}/pull/{number}"
        label = f"PR #{number} ({owner}/{repo})"
    else:
        owner = issue_match.group("owner")
        repo = issue_match.group("repo")
        number = issue_match.group("number")
        url = f"https://github.com/{owner}/{repo}/issues/{number}"
        label = f"issue #{number} ({owner}/{repo})"

    # Resolve project name for the mission tag
    project_name = _resolve_project_name(repo, owner)

    # Queue the mission with clean format
    from app.utils import insert_pending_mission

    mission_entry = f"- [project:{project_name}] /check {url}"
    missions_path = ctx.instance_dir / "missions.md"
    insert_pending_mission(missions_path, mission_entry)

    return f"\U0001f50d Check queued for {label}"


def _resolve_project_name(repo, owner=None):
    """Resolve a repo name to a known project name."""
    from app.utils import project_name_for_path, resolve_project_path

    project_path = resolve_project_path(repo, owner=owner)
    if project_path:
        return project_name_for_path(project_path)
    return repo
