"""Koan /check skill -- queue a check mission for a PR or issue."""

import re
import shlex


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
    project_name = _resolve_project_name(repo)

    # Build CLI command
    koan_root = ctx.koan_root
    instance_dir = ctx.instance_dir
    cmd = (
        f"cd {koan_root}/koan && "
        f"{koan_root}/.venv/bin/python3 -m app.check_runner "
        f"{shlex.quote(url)} "
        f"--instance-dir {shlex.quote(str(instance_dir))} "
        f"--koan-root {shlex.quote(str(koan_root))}"
    )

    # Queue the mission
    from app.utils import insert_pending_mission

    mission_entry = (
        f"- [project:{project_name}] Check {label} "
        f"\u2014 run: `{cmd}`"
    )
    missions_path = instance_dir / "missions.md"
    insert_pending_mission(missions_path, mission_entry)

    return f"\U0001f50d Check queued for {label}"


def _resolve_project_name(repo):
    """Resolve a repo name to a known project name."""
    from app.utils import get_known_projects

    for name, path in get_known_projects():
        if name.lower() == repo.lower():
            return name
    return repo
