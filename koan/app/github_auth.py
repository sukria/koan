#!/usr/bin/env python3
"""
Kōan -- GitHub CLI authentication helper.

Manages GitHub CLI identity switching for gh commands. When GITHUB_USER
is configured, retrieves a session token via `gh auth token --user <user>`
and exports it as GH_TOKEN for all gh CLI calls in the session.

Usage from Python:
    from app.github_auth import get_gh_env, setup_github_auth

    # Get env dict to pass to subprocess.run()
    env = get_gh_env()

    # Or setup once at session start (validates + alerts on failure)
    setup_github_auth()

Usage from shell:
    python3 -m app.github_auth
    # Prints GH_TOKEN=<token> on success, exits 1 on failure.
"""

import os
import subprocess
import sys
from typing import Optional, Dict


def get_github_user(project_name: str = "") -> str:
    """Return the GitHub user for a project, falling back to GITHUB_USER env.

    Resolution order:
    1. Per-project github_user from projects.yaml (if project_name given)
    2. GITHUB_USER environment variable
    3. Empty string (no user configured)
    """
    if project_name:
        try:
            from app.projects_config import load_projects_config, get_project_github_user
            koan_root = os.environ.get("KOAN_ROOT", "")
            if koan_root:
                config = load_projects_config(koan_root)
                if config:
                    project_user = get_project_github_user(config, project_name)
                    if project_user:
                        return project_user
        except Exception:
            pass
    return os.environ.get("GITHUB_USER", "").strip()


def get_gh_token(username: str) -> Optional[str]:
    """Retrieve a GitHub token for the given user via gh auth token.

    Args:
        username: The GitHub username to get a token for.

    Returns:
        The token string, or None if retrieval failed.
    """
    if not username:
        return None
    try:
        result = subprocess.run(
            ["gh", "auth", "token", "--user", username],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        return None
    except Exception:
        return None


def get_gh_env(project_name: str = "") -> Dict[str, str]:
    """Return environment variables for gh CLI commands.

    If a GitHub user is configured (per-project or global), retrieves
    the token and returns a dict with GH_TOKEN set.

    This is designed to be merged into subprocess.run() env:
        env = {**os.environ, **get_gh_env()}
    """
    username = get_github_user(project_name)
    if not username:
        return {}

    # Check if GH_TOKEN is already set in environment
    existing_token = os.environ.get("GH_TOKEN", "")
    if existing_token:
        return {"GH_TOKEN": existing_token}

    token = get_gh_token(username)
    if token:
        return {"GH_TOKEN": token}
    return {}


def setup_github_auth(project_name: str = "") -> bool:
    """Setup GitHub authentication for the session.

    Reads github_user from projects.yaml (per-project) or GITHUB_USER
    env var (global), retrieves token, sets GH_TOKEN in os.environ.
    Sends a Telegram alert if the token retrieval fails.

    Args:
        project_name: Optional project name for per-project user lookup.

    Returns:
        True if auth was set up successfully (or no user configured).
        False if a user is set but token retrieval failed.
    """
    username = get_github_user(project_name)
    if not username:
        if project_name:
            # Per-project switch with no configured user — clear any
            # previously set GH_TOKEN from a prior project switch, so
            # gh CLI falls back to its default auth.
            os.environ.pop("GH_TOKEN", None)
        return True  # No user configured, nothing to do

    token = get_gh_token(username)
    if token:
        os.environ["GH_TOKEN"] = token
        print(f"[github_auth] Authenticated as {username}")
        return True

    # Token retrieval failed — alert the user
    error_msg = (
        f"⚠️ GitHub authentication failed for user `{username}`.\n"
        f"Cannot retrieve token via `gh auth token --user {username}`.\n"
        f"Ensure the user is authenticated with: `gh auth login --user {username}`\n"
        f"gh CLI commands may fail during this session."
    )
    print(f"[github_auth] {error_msg}", file=sys.stderr)

    try:
        from app.notify import send_telegram
        send_telegram(error_msg)
    except Exception as e:
        print(f"[github_auth] Failed to send alert: {e}", file=sys.stderr)

    return False


if __name__ == "__main__":
    # CLI entry point.
    # Prints GH_TOKEN=<token> on success (for eval in bash).
    # Exits 0 if no GITHUB_USER configured or token retrieved.
    # Exits 1 if GITHUB_USER set but token retrieval failed.
    username = get_github_user()
    if not username:
        sys.exit(0)

    if setup_github_auth():
        # setup_github_auth sets os.environ["GH_TOKEN"] on success
        print(f"GH_TOKEN={os.environ['GH_TOKEN']}")
        sys.exit(0)

    sys.exit(1)
