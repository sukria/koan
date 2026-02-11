"""GitHub notification configuration helpers.

Reads GitHub-specific settings from config.yaml (global) and projects.yaml
(per-project override) for the notification-driven commands feature.

Config schema in config.yaml:
    github:
      nickname: "koan-bot"
      commands_enabled: true
      authorized_users: ["*"]
      max_age_hours: 24

Per-project override in projects.yaml:
    projects:
      myproject:
        github:
          authorized_users: ["alice", "bob"]
"""

from typing import List, Optional


def get_github_nickname(config: dict) -> str:
    """Get the bot's GitHub @mention nickname from config.yaml.

    Returns empty string if not configured.
    """
    github = config.get("github") or {}
    return str(github.get("nickname", "")).strip()


def get_github_commands_enabled(config: dict) -> bool:
    """Check if GitHub notification commands are enabled in config.yaml."""
    github = config.get("github") or {}
    return bool(github.get("commands_enabled", False))


def get_github_authorized_users(config: dict, project_name: Optional[str] = None,
                                 projects_config: Optional[dict] = None) -> List[str]:
    """Get the list of authorized GitHub users.

    If project_name and projects_config are provided, checks for per-project
    override first. Falls back to global config.yaml setting.

    Returns ["*"] for wildcard (all users), or a list of GitHub usernames.
    Returns empty list if not configured.
    """
    # Check per-project override first
    if project_name and projects_config:
        from app.projects_config import get_project_github_authorized_users
        project_users = get_project_github_authorized_users(projects_config, project_name)
        if project_users:
            return project_users

    # Fall back to global config.yaml
    github = config.get("github") or {}
    users = github.get("authorized_users", [])
    return users if isinstance(users, list) else []


def get_github_max_age_hours(config: dict) -> int:
    """Get max age in hours for processing notifications.

    Notifications older than this are ignored (stale protection).
    Default: 24 hours.
    """
    github = config.get("github") or {}
    try:
        return int(github.get("max_age_hours", 24))
    except (ValueError, TypeError):
        return 24


def validate_github_config(config: dict) -> Optional[str]:
    """Validate GitHub configuration at startup.

    Returns an error message if config is invalid, or None if valid.
    """
    if not get_github_commands_enabled(config):
        return None  # Feature disabled, no validation needed

    nickname = get_github_nickname(config)
    if not nickname:
        return "GitHub commands enabled but 'github.nickname' is not set in config.yaml"

    return None
