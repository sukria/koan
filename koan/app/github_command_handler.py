"""GitHub command handler — bridges notifications to missions.

Orchestrates the full flow from a GitHub @mention notification to a
queued mission in missions.md:
1. Parse comment → extract command
2. Validate command → check skill has github_enabled
3. Check permissions → verify user is authorized
4. Add reaction → mark as processed (👍)
5. Build mission → format with project tag
6. Insert mission → write to missions.md
"""

import logging
import re
from typing import List, Optional, Set, Tuple

from app.github_config import get_github_authorized_users, get_github_nickname
from app.github_notifications import (
    add_reaction,
    api_url_to_web_url,
    check_already_processed,
    check_user_permission,
    extract_comment_metadata,
    get_comment_from_notification,
    is_notification_stale,
    is_self_mention,
    mark_notification_read,
    parse_mention_command,
)
from app.skills import SkillRegistry

log = logging.getLogger(__name__)

# Track error replies to avoid duplicate error messages per comment
_error_replies: Set[str] = set()


def validate_command(command_name: str, registry: SkillRegistry) -> Optional[object]:
    """Check if a command maps to a skill with github_enabled.

    Args:
        command_name: The command to validate (e.g., "rebase").
        registry: The skills registry.

    Returns:
        The Skill object if valid, or None.
    """
    skill = registry.find_by_command(command_name)
    if skill is None:
        return None
    if not skill.github_enabled:
        return None
    return skill


def get_github_enabled_commands(registry: SkillRegistry) -> List[str]:
    """Get list of command names that are github_enabled.

    Returns sorted, deduplicated list of primary command names.
    """
    commands = set()
    for skill in registry.list_all():
        if skill.github_enabled:
            for cmd in skill.commands:
                commands.add(cmd.name)
    return sorted(commands)


def get_github_enabled_commands_with_descriptions(
    registry: SkillRegistry,
) -> List[Tuple[str, str]]:
    """Get github-enabled commands with their descriptions.

    Returns sorted list of (command_name, description) tuples.
    Only includes primary command names (not aliases).
    """
    commands: dict = {}
    for skill in registry.list_all():
        if skill.github_enabled:
            for cmd in skill.commands:
                if cmd.name not in commands:
                    commands[cmd.name] = cmd.description or skill.description
    return sorted(commands.items())


def format_help_message(
    invalid_command: str,
    registry: SkillRegistry,
    bot_username: str,
) -> str:
    """Build a help message listing available GitHub commands.

    Args:
        invalid_command: The command that was not recognized.
        registry: Skills registry.
        bot_username: The bot's GitHub username (for usage examples).

    Returns:
        A formatted markdown help message for GitHub comments.
    """
    commands = get_github_enabled_commands_with_descriptions(registry)

    lines = [f"Unknown command `{invalid_command}`. Here are the commands I support:\n"]
    for name, description in commands:
        lines.append(f"- `@{bot_username} {name}` — {description}")

    lines.append(f"\nUsage: `@{bot_username} <command>` in any PR or issue comment.")
    return "\n".join(lines)


def _extract_url_from_context(context: str) -> Optional[Tuple[str, str]]:
    """Extract URL from context text if present.
    
    Args:
        context: Context text that may contain a URL
        
    Returns:
        Tuple of (url, remaining_context) or None if no URL found
    """
    url_match = re.search(r'https?://github\.com/\S+', context)
    if not url_match:
        return None
    
    url = url_match.group(0)
    # Remove URL from context
    remaining = context[:url_match.start()].strip() + " " + context[url_match.end():].strip()
    remaining = remaining.strip()
    return url, remaining


def build_mission_from_command(
    skill,
    command_name: str,
    context: str,
    notification: dict,
    project_name: str,
) -> str:
    """Construct a mission string from a GitHub notification command.

    Args:
        skill: The Skill object.
        command_name: The command name (e.g., "rebase").
        context: Additional context text from the @mention.
        notification: The notification dict.
        project_name: The resolved project name.

    Returns:
        A mission entry string like "- [project:X] /command url context"
    """
    # Extract URL from notification subject
    subject_url = notification.get("subject", {}).get("url", "")
    web_url = api_url_to_web_url(subject_url) if subject_url else ""

    # Check if context contains a URL — if so, use that instead
    url_in_context = _extract_url_from_context(context)
    if url_in_context:
        web_url, context = url_in_context

    # Build mission text
    parts = [f"/{command_name}"]
    if web_url:
        parts.append(web_url)
    if context and skill.github_context_aware:
        parts.append(context)

    mission_text = " ".join(parts)
    return f"- [project:{project_name}] {mission_text}"


def resolve_project_from_notification(notification: dict) -> Optional[Tuple[str, str, str]]:
    """Resolve project name from notification repository.

    Args:
        notification: A notification dict.

    Returns:
        Tuple of (project_name, owner, repo) or None if unknown.
    """
    repo_data = notification.get("repository", {})
    full_name = repo_data.get("full_name", "")
    if not full_name or "/" not in full_name:
        return None

    owner, repo = full_name.split("/", 1)

    from app.utils import project_name_for_path, resolve_project_path

    project_path = resolve_project_path(repo, owner=owner)
    if not project_path:
        return None

    project_name = project_name_for_path(project_path)
    return project_name, owner, repo


def _should_skip_notification(notification: dict, bot_username: str, max_age_hours: int) -> bool:
    """Check if notification should be skipped (stale or self-mention).

    Args:
        notification: Notification dict
        bot_username: Bot's GitHub username
        max_age_hours: Maximum age threshold

    Returns:
        True if should skip
    """
    thread_id = notification.get("id", "?")
    # Check staleness
    if is_notification_stale(notification, max_age_hours):
        log.debug("GitHub: skipping notification %s — stale", thread_id)
        mark_notification_read(str(notification.get("id", "")))
        return True

    # Get comment
    comment = get_comment_from_notification(notification)
    if not comment:
        log.debug("GitHub: skipping notification %s — no comment found", thread_id)
        return True

    # Skip self-mentions
    if is_self_mention(comment, bot_username):
        log.debug("GitHub: skipping notification %s — self-mention", thread_id)
        mark_notification_read(str(notification.get("id", "")))
        return True

    return False


def _validate_and_parse_command(
    notification: dict,
    comment: dict,
    config: dict,
    registry: SkillRegistry,
    bot_username: str,
    owner: str,
    repo: str,
) -> Tuple[Optional[object], Optional[str], str]:
    """Validate command and parse from comment.
    
    Args:
        notification: Notification dict
        comment: Comment dict
        config: Config dict
        registry: Skills registry
        bot_username: Bot's GitHub username
        owner: Repository owner
        repo: Repository name
        
    Returns:
        Tuple of (skill, command_name, context). 
        skill is None if command is invalid or already processed.
        command_name is None if already processed/no valid mention.
    """
    comment_id = str(comment.get("id", ""))

    # Check if already processed
    if check_already_processed(comment_id, bot_username, owner, repo):
        log.debug("GitHub: comment %s already processed", comment_id)
        mark_notification_read(str(notification.get("id", "")))
        return None, None, ""

    # Parse command from comment
    nickname = get_github_nickname(config)
    command_result = parse_mention_command(comment.get("body", ""), nickname)
    if not command_result:
        log.debug("GitHub: no valid @mention command in comment %s", comment_id)
        mark_notification_read(str(notification.get("id", "")))
        return None, None, ""

    command_name, context = command_result
    log.debug("GitHub: parsed command=%s context=%s from comment %s", command_name, context, comment_id)

    # Validate command
    skill = validate_command(command_name, registry)
    if not skill:
        log.debug("GitHub: command '%s' is not github-enabled", command_name)
        return None, command_name, context  # Invalid command, but we have the name for error message

    return skill, command_name, context


def process_single_notification(
    notification: dict,
    registry: SkillRegistry,
    config: dict,
    projects_config: Optional[dict],
    bot_username: str,
    max_age_hours: int = 24,
) -> Tuple[bool, Optional[str]]:
    """Process a single GitHub notification.

    Full workflow: parse → validate → check permissions → react → create mission.

    Args:
        notification: A notification dict from GitHub API.
        registry: Skills registry.
        config: Global config (from config.yaml).
        projects_config: Projects config (from projects.yaml), or None.
        bot_username: The bot's GitHub username.
        max_age_hours: Max notification age in hours.

    Returns:
        Tuple of (success, error_message). error_message is None on success.
    """
    # Early exit checks
    if _should_skip_notification(notification, bot_username, max_age_hours):
        return False, None

    # Get the triggering comment (already fetched in _should_skip_notification, but kept for clarity)
    comment = get_comment_from_notification(notification)
    if not comment:
        return False, None

    comment_author = comment.get("user", {}).get("login", "")

    # Resolve project
    project_info = resolve_project_from_notification(notification)
    if not project_info:
        repo_name = notification.get("repository", {}).get("full_name", "?")
        log.debug("GitHub: repo %s not found in projects.yaml", repo_name)
        return False, "Unknown repository — not configured in projects.yaml"

    project_name, owner, repo = project_info
    log.debug("GitHub: resolved project=%s from %s/%s", project_name, owner, repo)

    # Validate and parse command
    skill, command_name, context = _validate_and_parse_command(
        notification, comment, config, registry, bot_username, owner, repo
    )
    
    # If command_name is None, already processed or no valid mention
    if command_name is None:
        return False, None
    
    # If skill is None but we have a command_name, it's an invalid command
    if skill is None:
        mark_notification_read(str(notification.get("id", "")))
        help_msg = format_help_message(command_name, registry, bot_username)
        return False, help_msg

    # Check permissions
    allowed_users = get_github_authorized_users(config, project_name, projects_config)
    if not check_user_permission(owner, repo, comment_author, allowed_users):
        log.debug("GitHub: permission denied for user %s on %s/%s", comment_author, owner, repo)
        return False, "Permission denied. Only users with write access can trigger bot commands."

    # Build and insert mission BEFORE reacting (so crash doesn't lose command)
    mission_entry = build_mission_from_command(
        skill, command_name, context, notification, project_name,
    )

    from app.utils import insert_pending_mission
    from pathlib import Path
    import os

    koan_root = os.environ.get("KOAN_ROOT", "")
    missions_path = Path(koan_root) / "instance" / "missions.md"
    insert_pending_mission(missions_path, mission_entry)

    # React AFTER mission is persisted (marks as processed)
    comment_id = str(comment.get("id", ""))
    add_reaction(owner, repo, comment_id)

    # Mark notification as read
    mark_notification_read(str(notification.get("id", "")))

    log.info("GitHub: created mission from @%s: %s", comment_author, command_name)
    return True, None


def post_error_reply(
    owner: str,
    repo: str,
    issue_number: str,
    comment_id: str,
    error_message: str,
) -> bool:
    """Post an error reply to a GitHub comment.

    Includes deduplication — won't post the same error twice for the same comment.

    Args:
        owner: Repository owner.
        repo: Repository name.
        issue_number: Issue or PR number.
        comment_id: The triggering comment ID.
        error_message: The error message to post.

    Returns:
        True if posted successfully.
    """
    # Deduplication key
    error_key = f"{comment_id}:{error_message}"
    if error_key in _error_replies:
        return False

    from app.github import api

    body = f"❌ {error_message}"
    try:
        api(
            f"repos/{owner}/{repo}/issues/{issue_number}/comments",
            method="POST",
            extra_args=["-f", f"body={body}"],
        )
        _error_replies.add(error_key)

        # Also add reaction to mark as processed
        add_reaction(owner, repo, comment_id)
        return True
    except RuntimeError:
        return False


def extract_issue_number_from_notification(notification: dict) -> Optional[str]:
    """Extract issue/PR number from a notification.

    Works for both issues and pull requests.
    """
    subject_url = notification.get("subject", {}).get("url", "")
    if not subject_url:
        return None

    # API URL: .../issues/42 or .../pulls/42
    match = re.search(r'/(?:issues|pulls)/(\d+)', subject_url)
    return match.group(1) if match else None
