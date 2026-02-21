"""GitHub notification fetching and parsing.

Core module for the notification-driven commands feature. Handles:
- Fetching unread notifications filtered to @mentions
- Parsing @mention commands from comment bodies
- Converting API URLs to web URLs
- Reaction-based deduplication (any bot reaction = processed)
- Permission checks for authorized users
"""

import json
import logging
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

from app.bounded_set import BoundedSet
from app.github import api, run_gh

log = logging.getLogger(__name__)

# In-memory set of processed comment IDs (resets on restart).
# Bounded: FIFO eviction when limit is reached (oldest entries removed first).
_MAX_PROCESSED_COMMENTS = 10000
_processed_comments: BoundedSet = BoundedSet(maxlen=_MAX_PROCESSED_COMMENTS)

# Regex for extracting @mention commands, skipping code blocks
_CODE_BLOCK_RE = re.compile(r'```.*?```|`[^`]+`', re.DOTALL)


def fetch_unread_notifications(known_repos: Optional[Set[str]] = None) -> List[dict]:
    """Fetch unread GitHub notifications filtered to mentions.

    Args:
        known_repos: Optional set of "owner/repo" strings to filter against.
            If None, all mention notifications are returned.

    Returns:
        List of notification dicts from the GitHub API.
    """
    try:
        raw = api("notifications", extra_args=["--paginate"])
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as e:
        log.debug("GitHub API: failed to fetch notifications: %s", e)
        return []

    if not raw:
        log.debug("GitHub API: empty response from notifications endpoint")
        return []

    try:
        notifications = json.loads(raw)
    except json.JSONDecodeError:
        log.debug("GitHub API: invalid JSON in notifications response")
        return []

    if not isinstance(notifications, list):
        log.debug("GitHub API: unexpected response type: %s", type(notifications).__name__)
        return []

    log.debug("GitHub API: %d total unread notifications", len(notifications))

    skipped_reasons: Dict[str, int] = {}
    skipped_repos: List[str] = []
    results = []
    for notif in notifications:
        reason = notif.get("reason", "?")
        repo_name = notif.get("repository", {}).get("full_name", "?")

        if reason != "mention":
            skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1
            continue

        # Filter by known repos if provided — normalize for comparison
        if known_repos:
            repo_lower = repo_name.lower()
            if repo_lower not in known_repos:
                skipped_repos.append(repo_name)
                continue

        results.append(notif)

    if skipped_reasons:
        log.debug(
            "GitHub: skipped %d non-mention notifications: %s",
            sum(skipped_reasons.values()),
            ", ".join(f"{r}={c}" for r, c in sorted(skipped_reasons.items())),
        )
    if skipped_repos:
        log.debug(
            "GitHub: skipped %d mentions from unknown repos: %s",
            len(skipped_repos), ", ".join(skipped_repos),
        )

    log.debug("GitHub: %d mention notification(s) after filtering", len(results))
    return results


def parse_mention_command(comment_body: str, nickname: str) -> Optional[Tuple[str, str]]:
    """Extract command and args from a @mention in a comment body.

    Ignores mentions inside code blocks (``` or `).
    Only processes the first @mention found.

    Args:
        comment_body: The full comment text.
        nickname: The bot's GitHub username (without @).

    Returns:
        Tuple of (command, context) or None if no valid mention found.
        Command is lowercase. Context is the remaining text after command.
    """
    if not comment_body or not nickname:
        return None

    # Remove code blocks to avoid matching mentions in code
    clean_body = _CODE_BLOCK_RE.sub('', comment_body)

    # Match @nickname followed by a command word
    pattern = rf'@{re.escape(nickname)}\s+(\w+)(.*?)(?:\n|$)'
    match = re.search(pattern, clean_body, re.IGNORECASE)
    if not match:
        return None

    command = match.group(1).strip().lower()
    context = match.group(2).strip()

    if not command:
        return None

    return command, context


def api_url_to_web_url(api_url: str) -> str:
    """Convert a GitHub API URL to a web URL.

    Examples:
        https://api.github.com/repos/owner/repo/pulls/123
        → https://github.com/owner/repo/pull/123

        https://api.github.com/repos/owner/repo/issues/42
        → https://github.com/owner/repo/issues/42
    """
    url = api_url.replace("https://api.github.com/repos/", "https://github.com/")
    # API uses "pulls" (plural), web uses "pull" (singular)
    url = re.sub(r'/pulls/(\d+)', r'/pull/\1', url)
    return url


def get_comment_from_notification(notification: dict) -> Optional[dict]:
    """Fetch the latest comment that triggered the notification.

    Args:
        notification: A notification dict from the GitHub API.

    Returns:
        The comment dict, or None if it can't be fetched.
    """
    # The notification's latest_comment_url points to the triggering comment
    comment_url = notification.get("subject", {}).get("latest_comment_url", "")
    if not comment_url:
        return None

    # Convert full URL to API endpoint (strict prefix check to prevent SSRF)
    api_prefix = "https://api.github.com/"
    if not comment_url.startswith(api_prefix):
        return None
    endpoint = comment_url[len(api_prefix):]
    if not endpoint:
        return None

    try:
        raw = api(endpoint)
        return json.loads(raw) if raw else None
    except (RuntimeError, json.JSONDecodeError):
        return None


def mark_notification_read(thread_id: str) -> bool:
    """Mark a notification thread as read.

    Args:
        thread_id: The notification thread ID.

    Returns:
        True if successful, False otherwise.
    """
    try:
        api(f"notifications/threads/{thread_id}", method="PATCH")
        return True
    except RuntimeError:
        return False


def _reactions_endpoint(
    comment_api_url: str = "",
    owner: str = "",
    repo: str = "",
    comment_id: str = "",
) -> str:
    """Build the reactions API endpoint for a comment.

    Uses comment_api_url when available (handles all comment types:
    issue comments, PR review comments, commit comments).
    Falls back to the issues/comments endpoint for backward compatibility.

    Args:
        comment_api_url: The comment's canonical API URL (from comment["url"]).
        owner: Repository owner (fallback).
        repo: Repository name (fallback).
        comment_id: Comment ID (fallback).

    Returns:
        The reactions API endpoint path.
    """
    if comment_api_url:
        api_prefix = "https://api.github.com/"
        if comment_api_url.startswith(api_prefix):
            return comment_api_url[len(api_prefix):] + "/reactions"
    return f"repos/{owner}/{repo}/issues/comments/{comment_id}/reactions"


def check_already_processed(comment_id: str, bot_username: str,
                             owner: str, repo: str,
                             comment_api_url: str = "") -> bool:
    """Check if a comment has already been processed (has bot reaction).

    Checks for any reaction from the bot — both 👍 (command acknowledgment)
    and 👀 (AI reply acknowledgment). This prevents duplicate processing
    when mark_notification_read fails.

    Also checks in-memory set for current session deduplication.

    Args:
        comment_id: The comment ID.
        bot_username: The bot's GitHub username.
        owner: Repository owner.
        repo: Repository name.
        comment_api_url: The comment's canonical API URL. When provided,
            derives the correct reactions endpoint (handles PR review
            comments, commit comments, etc.). Falls back to
            issues/comments endpoint.

    Returns:
        True if already processed.
    """
    # Check in-memory first
    if comment_id in _processed_comments:
        return True

    # Check GitHub reactions — any reaction from the bot means processed
    endpoint = _reactions_endpoint(comment_api_url, owner, repo, comment_id)
    try:
        raw = api(endpoint)
        reactions = json.loads(raw) if raw else []
        if isinstance(reactions, list):
            for reaction in reactions:
                if reaction.get("user", {}).get("login") == bot_username:
                    _processed_comments.add(comment_id)
                    return True
    except (RuntimeError, json.JSONDecodeError):
        pass

    return False


def add_reaction(owner: str, repo: str, comment_id: str,
                 emoji: str = "+1", comment_api_url: str = "") -> bool:
    """Add a reaction to a comment.

    Args:
        owner: Repository owner.
        repo: Repository name.
        comment_id: The comment ID.
        emoji: Reaction content (default: "+1" for 👍).
        comment_api_url: The comment's canonical API URL. When provided,
            derives the correct reactions endpoint (handles PR review
            comments, commit comments, etc.).

    Returns:
        True if successful.
    """
    endpoint = _reactions_endpoint(comment_api_url, owner, repo, comment_id)
    try:
        api(
            endpoint,
            method="POST",
            extra_args=["-f", f"content={emoji}"],
        )
        _processed_comments.add(comment_id)
        return True
    except RuntimeError:
        return False


def check_user_permission(owner: str, repo: str, username: str,
                           allowed_users: List[str]) -> bool:
    """Check if a user is authorized to trigger bot commands.

    Args:
        owner: Repository owner.
        repo: Repository name.
        username: The GitHub username to check.
        allowed_users: List of allowed usernames, or ["*"] for all.

    Returns:
        True if authorized.
    """
    # Check allowlist (unless wildcard)
    if "*" not in allowed_users and username not in allowed_users:
        return False

    # Always verify at least write access via GitHub API
    try:
        raw = api(f"repos/{owner}/{repo}/collaborators/{username}/permission")
        data = json.loads(raw) if raw else {}
        permission = data.get("permission", "none")
        return permission in ("admin", "write")
    except (RuntimeError, json.JSONDecodeError):
        return False


def is_notification_stale(notification: dict, max_age_hours: int = 24) -> bool:
    """Check if a notification is too old to process.

    Args:
        notification: A notification dict.
        max_age_hours: Maximum age in hours (default: 24).

    Returns:
        True if the notification is stale.
    """
    updated_at = notification.get("updated_at", "")
    if not updated_at:
        return True

    try:
        notif_time = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        age_hours = (datetime.now(timezone.utc) - notif_time).total_seconds() / 3600
        return age_hours > max_age_hours
    except (ValueError, TypeError):
        return True


def is_self_mention(comment: dict, bot_username: str) -> bool:
    """Check if the comment was posted by the bot itself.

    Args:
        comment: A comment dict from the GitHub API.
        bot_username: The bot's GitHub username.

    Returns:
        True if the comment author is the bot.
    """
    author = comment.get("user", {}).get("login", "")
    return author == bot_username


def extract_comment_metadata(comment_url: str) -> Optional[Tuple[str, str, str]]:
    """Extract owner, repo, and comment ID from a comment URL.

    Handles both web URLs and API URLs:
        https://github.com/owner/repo/issues/123#issuecomment-456
        https://api.github.com/repos/owner/repo/issues/comments/456

    Returns:
        Tuple of (owner, repo, comment_id) or None.
    """
    # Try API URL format first
    match = re.match(
        r'https?://api\.github\.com/repos/([^/]+)/([^/]+)/issues/comments/(\d+)',
        comment_url,
    )
    if match:
        return match.group(1), match.group(2), match.group(3)

    # Try web URL format
    match = re.match(
        r'https?://github\.com/([^/]+)/([^/]+)/(?:issues|pull)/\d+#issuecomment-(\d+)',
        comment_url,
    )
    if match:
        return match.group(1), match.group(2), match.group(3)

    return None
