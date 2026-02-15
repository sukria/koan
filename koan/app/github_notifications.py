"""GitHub notification fetching and parsing.

Core module for the notification-driven commands feature. Handles:
- Fetching unread notifications filtered to @mentions
- Parsing @mention commands from comment bodies
- Converting API URLs to web URLs
- Reaction-based deduplication (👍 = processed)
- Permission checks for authorized users
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

from app.github import api, run_gh

log = logging.getLogger(__name__)

# In-memory set of processed comment IDs (resets on restart)
_processed_comments: Set[str] = set()

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
    except (RuntimeError, Exception):
        return []

    if not raw:
        return []

    try:
        notifications = json.loads(raw)
    except json.JSONDecodeError:
        return []

    if not isinstance(notifications, list):
        return []

    log.debug("GitHub API: %d total unread notifications", len(notifications))

    results = []
    for notif in notifications:
        reason = notif.get("reason", "?")
        repo_name = notif.get("repository", {}).get("full_name", "?")

        if reason != "mention":
            log.debug("GitHub: skipping notification from %s — reason=%s (not mention)", repo_name, reason)
            continue

        # Filter by known repos if provided
        if known_repos:
            if repo_name not in known_repos:
                log.debug("GitHub: skipping mention from %s — not in known repos", repo_name)
                continue

        results.append(notif)

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

    # Convert full URL to API endpoint
    endpoint = comment_url.replace("https://api.github.com/", "")
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


def check_already_processed(comment_id: str, bot_username: str,
                             owner: str, repo: str) -> bool:
    """Check if a comment has already been processed (has bot's 👍 reaction).

    Also checks in-memory set for current session deduplication.

    Args:
        comment_id: The comment ID.
        bot_username: The bot's GitHub username.
        owner: Repository owner.
        repo: Repository name.

    Returns:
        True if already processed.
    """
    # Check in-memory first
    if comment_id in _processed_comments:
        return True

    # Check GitHub reactions
    try:
        raw = api(f"repos/{owner}/{repo}/issues/comments/{comment_id}/reactions")
        reactions = json.loads(raw) if raw else []
        if isinstance(reactions, list):
            for reaction in reactions:
                if (reaction.get("user", {}).get("login") == bot_username
                        and reaction.get("content") == "+1"):
                    _processed_comments.add(comment_id)
                    return True
    except (RuntimeError, json.JSONDecodeError):
        pass

    return False


def add_reaction(owner: str, repo: str, comment_id: str, emoji: str = "+1") -> bool:
    """Add a reaction to a comment.

    Args:
        owner: Repository owner.
        repo: Repository name.
        comment_id: The comment ID.
        emoji: Reaction content (default: "+1" for 👍).

    Returns:
        True if successful.
    """
    try:
        api(
            f"repos/{owner}/{repo}/issues/comments/{comment_id}/reactions",
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
