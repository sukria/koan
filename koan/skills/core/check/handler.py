"""Koan /check skill -- inspect a PR or issue and take appropriate action."""

import json
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
    """Handle /check command.

    Usage:
        /check <github-url>

    For PRs: checks rebase status, review state, and takes action.
    For issues: triggers /plan if there are new updates.
    Tracks last-checked timestamps to avoid redundant work.
    """
    args = ctx.args.strip()

    if not args:
        return (
            "Usage: /check <github-pr-or-issue-url>\n"
            "Ex: /check https://github.com/sukria/koan/pull/85\n\n"
            "For PRs: checks rebase, review status, and takes action.\n"
            "For issues: triggers /plan if updated since last check."
        )

    # Try PR first (pull/N comes before issues/N in specificity)
    pr_match = _PR_URL_RE.search(args)
    if pr_match:
        return _handle_pr(ctx, pr_match)

    issue_match = _ISSUE_URL_RE.search(args)
    if issue_match:
        return _handle_issue(ctx, issue_match)

    return (
        "\u274c No valid GitHub PR or issue URL found.\n"
        "Expected: https://github.com/owner/repo/pull/123\n"
        "      or: https://github.com/owner/repo/issues/123"
    )


def _canonical_url(owner, repo, kind, number):
    """Build a canonical URL for tracker storage."""
    return f"https://github.com/{owner}/{repo}/{kind}/{number}"


def _fetch_pr_metadata(owner, repo, pr_number):
    """Fetch PR metadata via gh CLI.

    Returns dict with: state, mergeable, reviewDecision, updatedAt,
    headRefName, baseRefName, title, isDraft, author, url.
    """
    from app.github import run_gh

    fields = (
        "state,mergeable,reviewDecision,updatedAt,"
        "headRefName,baseRefName,title,isDraft,author,url"
    )
    raw = run_gh(
        "pr", "view", pr_number,
        "--repo", f"{owner}/{repo}",
        "--json", fields,
    )
    return json.loads(raw)


def _fetch_issue_metadata(owner, repo, issue_number):
    """Fetch issue metadata via gh CLI.

    Returns dict with: state, updatedAt, title, url, comments count.
    """
    from app.github import api

    raw = api(
        f"repos/{owner}/{repo}/issues/{issue_number}",
        jq='{"state": .state, "updatedAt": .updated_at, '
           '"title": .title, "url": .html_url, '
           '"comments": .comments}',
    )
    return json.loads(raw)


def _needs_rebase(pr_data):
    """Determine if the PR branch needs a rebase.

    Uses the ``mergeable`` field from the GitHub API.
    CONFLICTING means the branch has merge conflicts (needs rebase).
    UNKNOWN can be transient — we treat it as "check later".
    """
    mergeable = pr_data.get("mergeable", "UNKNOWN")
    return mergeable == "CONFLICTING"


def _has_no_reviews(pr_data):
    """Return True if the PR has received no review decision yet."""
    decision = pr_data.get("reviewDecision")
    # None or empty string means no review submitted
    return not decision


def _handle_pr(ctx, match):
    """Check a pull request and decide on action."""
    from app.check_tracker import has_changed, mark_checked

    send = ctx.send_message
    owner = match.group("owner")
    repo = match.group("repo")
    pr_number = match.group("number")
    url = _canonical_url(owner, repo, "pull", pr_number)

    if send:
        send(f"\U0001f50d Checking PR #{pr_number} ({owner}/{repo})...")

    try:
        pr_data = _fetch_pr_metadata(owner, repo, pr_number)
    except Exception as e:
        return f"\u274c Failed to fetch PR #{pr_number}: {str(e)[:300]}"

    updated_at = pr_data.get("updatedAt", "")
    title = pr_data.get("title", "")
    state = pr_data.get("state", "UNKNOWN")

    # Skip closed/merged PRs
    if state in ("CLOSED", "MERGED"):
        mark_checked(ctx.instance_dir, url, updated_at)
        return f"PR #{pr_number} is {state.lower()}. No action needed."

    # Check if anything changed since last check
    if not has_changed(ctx.instance_dir, url, updated_at):
        return (
            f"PR #{pr_number} ({title[:60]}) — no updates since last check. "
            "Skipping."
        )

    # Build status report
    actions = []
    missions_path = ctx.instance_dir / "missions.md"
    needs_rebase = _needs_rebase(pr_data)

    # 1. Check if rebase is needed
    if needs_rebase:
        _queue_rebase(ctx, owner, repo, pr_number, missions_path)
        actions.append(f"\u267b\ufe0f Rebase queued — PR has merge conflicts")

    # 2. Check if review is needed (no review decision + not draft + not conflicting)
    is_draft = pr_data.get("isDraft", False)
    if _has_no_reviews(pr_data) and not is_draft and not needs_rebase:
        _queue_pr_review(ctx, owner, repo, pr_number, missions_path)
        actions.append(f"\U0001f4dd PR review queued — no reviews yet")

    # Record the check
    mark_checked(ctx.instance_dir, url, updated_at)

    if not actions:
        head = pr_data.get("headRefName", "?")
        base = pr_data.get("baseRefName", "?")
        mergeable = pr_data.get("mergeable", "UNKNOWN")
        review = pr_data.get("reviewDecision") or "none"
        return (
            f"\u2705 PR #{pr_number} ({title[:60]})\n"
            f"Branch: {head} \u2192 {base}\n"
            f"Mergeable: {mergeable} | Review: {review}\n"
            "No action needed."
        )

    summary = "\n".join(f"  \u2022 {a}" for a in actions)
    return f"\U0001f527 PR #{pr_number} ({title[:60]}):\n{summary}"


def _queue_rebase(ctx, owner, repo, pr_number, missions_path):
    """Queue a rebase mission for the PR."""
    from app.utils import insert_pending_mission, resolve_project_path

    project_path = resolve_project_path(repo)
    project_name = _resolve_project_name(repo)
    koan_root = ctx.koan_root

    cmd = (
        f"cd {koan_root}/koan && "
        f"{koan_root}/.venv/bin/python3 -m app.rebase_pr "
        f"https://github.com/{owner}/{repo}/pull/{pr_number}"
    )
    if project_path:
        cmd += f" --project-path {project_path}"

    entry = (
        f"- [project:{project_name}] Rebase PR #{pr_number} "
        f"({owner}/{repo}) \u2014 run: `{cmd}`"
    )
    insert_pending_mission(missions_path, entry)


def _queue_pr_review(ctx, owner, repo, pr_number, missions_path):
    """Queue a PR review mission."""
    from app.utils import insert_pending_mission

    project_name = _resolve_project_name(repo)
    pr_url = f"https://github.com/{owner}/{repo}/pull/{pr_number}"

    entry = (
        f"- [project:{project_name}] Review PR #{pr_number} "
        f"({owner}/{repo}) \u2014 /pr {pr_url}"
    )
    insert_pending_mission(missions_path, entry)


def _resolve_project_name(repo):
    """Resolve a repo name to a known project name."""
    from app.utils import get_known_projects

    for name, path in get_known_projects():
        if name.lower() == repo.lower():
            return name
    return repo


def _handle_issue(ctx, match):
    """Check an issue and trigger /plan if updated."""
    from app.check_tracker import has_changed, mark_checked

    send = ctx.send_message
    owner = match.group("owner")
    repo = match.group("repo")
    issue_number = match.group("number")
    url = _canonical_url(owner, repo, "issues", issue_number)

    if send:
        send(f"\U0001f50d Checking issue #{issue_number} ({owner}/{repo})...")

    try:
        issue_data = _fetch_issue_metadata(owner, repo, issue_number)
    except Exception as e:
        return f"\u274c Failed to fetch issue #{issue_number}: {str(e)[:300]}"

    updated_at = issue_data.get("updatedAt", "")
    title = issue_data.get("title", "")
    state = issue_data.get("state", "unknown")

    # Skip closed issues
    if state == "closed":
        mark_checked(ctx.instance_dir, url, updated_at)
        return f"Issue #{issue_number} is closed. No action needed."

    # Check if anything changed since last check
    if not has_changed(ctx.instance_dir, url, updated_at):
        return (
            f"Issue #{issue_number} ({title[:60]}) — no updates since last "
            "check. Skipping."
        )

    # Queue /plan on the issue
    _queue_plan(ctx, owner, repo, issue_number, title)

    mark_checked(ctx.instance_dir, url, updated_at)

    return (
        f"\U0001f9e0 Issue #{issue_number} ({title[:60]}) has updates.\n"
        f"  \u2022 /plan queued for iteration."
    )


def _queue_plan(ctx, owner, repo, issue_number, title):
    """Queue a /plan mission for the issue."""
    from app.utils import insert_pending_mission

    project_name = _resolve_project_name(repo)
    issue_url = f"https://github.com/{owner}/{repo}/issues/{issue_number}"
    missions_path = ctx.instance_dir / "missions.md"

    short_title = title[:80] if title else f"issue #{issue_number}"
    entry = (
        f"- [project:{project_name}] Plan iteration on {short_title} "
        f"\u2014 /plan {issue_url}"
    )
    insert_pending_mission(missions_path, entry)
