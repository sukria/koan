"""Shared helpers for GitHub-related skills.

Common utilities for skills that interact with GitHub PRs and issues:
- URL extraction and validation
- Project resolution
- Mission queuing
- Response formatting
"""

import re
from typing import Optional, Tuple




def extract_github_url(args: str, url_type: str = "pr-or-issue") -> Optional[Tuple[str, Optional[str]]]:
    """Extract and validate a GitHub URL from command arguments.

    Args:
        args: Raw command arguments
        url_type: Expected URL type - "pr", "issue", or "pr-or-issue"

    Returns:
        Tuple of (url, remaining_context) where remaining_context is text after the URL,
        or None if no valid URL found

    Examples:
        >>> extract_github_url("https://github.com/o/r/pull/1 phase 1")
        ("https://github.com/o/r/pull/1", "phase 1")
    """
    if url_type == "pr":
        pattern = r'https?://github\.com/[^\s]+/pull/\d+'
    elif url_type == "issue":
        pattern = r'https?://github\.com/[^\s]+/issues/\d+'
    else:  # pr-or-issue
        pattern = r'https?://github\.com/[^\s]+/(?:pull|issues)/\d+'

    match = re.search(pattern, args)
    if not match:
        return None

    url = match.group(0).split("#")[0]  # Remove fragment
    context = args[match.end():].strip()
    return url, context if context else None


def resolve_project_for_repo(repo: str, owner: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """Resolve local project path and name for a GitHub repository.

    Args:
        repo: Repository name
        owner: Optional repository owner (for better matching)

    Returns:
        Tuple of (project_path, project_name) or (None, None) if not found
    """
    from app.utils import project_name_for_path, resolve_project_path

    project_path = resolve_project_path(repo, owner=owner)
    if not project_path:
        return None, None

    project_name = project_name_for_path(project_path)
    return project_path, project_name


def queue_github_mission(ctx, command: str, url: str, project_name: str, context: Optional[str] = None) -> None:
    """Queue a GitHub-related mission with consistent formatting.

    Args:
        ctx: Skill context
        command: Command name (e.g., "rebase", "review", "implement")
        url: GitHub URL
        project_name: Project name for tagging
        context: Optional additional context to append
    """
    from app.utils import insert_pending_mission

    mission_text = f"/{command} {url}"
    if context:
        mission_text += f" {context}"

    mission_entry = f"- [project:{project_name}] {mission_text}"
    missions_path = ctx.instance_dir / "missions.md"
    insert_pending_mission(missions_path, mission_entry)


def format_project_not_found_error(repo: str) -> str:
    """Format a consistent error message when project cannot be resolved.

    Args:
        repo: Repository name that couldn't be matched

    Returns:
        Formatted error message
    """
    from app.utils import get_known_projects

    known = ", ".join(n for n, _ in get_known_projects()) or "none"
    return (
        f"\u274c Could not find local project matching repo '{repo}'.\n"
        f"Known projects: {known}"
    )


def format_success_message(url_type: str, number: str, owner: str, repo: str, context: Optional[str] = None) -> str:
    """Format a consistent success message for queued missions.

    Args:
        url_type: Type of URL ("PR", "issue", etc.)
        number: PR or issue number
        owner: Repository owner
        repo: Repository name
        context: Optional context suffix

    Returns:
        Formatted success message
    """
    msg = f"{url_type} #{number} ({owner}/{repo})"
    if context:
        msg += f" — {context}"
    return msg
