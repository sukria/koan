"""Kōan add_project skill — clone a GitHub repo and register it.

Usage: /add_project <github-url> [name]

Clones the repository into workspace/<name>, detects push access,
and creates a personal fork if needed so PRs can be submitted.
"""

import os
import re
from pathlib import Path

from app.git_utils import run_git_strict


def handle(ctx):
    """Handle /add_project command."""
    args = ctx.args.strip()
    if not args:
        return (
            "Usage: /add_project <github-url> [name]\n\n"
            "Examples:\n"
            "  /add_project https://github.com/owner/repo\n"
            "  /add_project owner/repo myname"
        )

    url, project_name = _parse_args(args)
    if not url:
        return "Could not parse a GitHub URL or owner/repo from the arguments."

    owner, repo = _extract_owner_repo(url)
    if not owner or not repo:
        return f"Could not extract owner/repo from: {url}"

    if not project_name:
        project_name = repo

    # Validate project name
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$", project_name):
        return f"Invalid project name: {project_name}"

    koan_root = str(ctx.koan_root)
    workspace_dir = Path(koan_root) / "workspace"
    project_dir = workspace_dir / project_name

    # Check for existing project
    if project_dir.exists():
        return f"Project '{project_name}' already exists at {project_dir}"

    # Ensure workspace directory exists
    workspace_dir.mkdir(exist_ok=True)

    # Notify: starting clone
    ctx.send_message(f"Cloning {owner}/{repo} into workspace/{project_name}...")

    # Clone the repository
    clone_url = f"https://github.com/{owner}/{repo}.git"
    try:
        _git_clone(clone_url, str(project_dir))
    except RuntimeError as e:
        return f"Clone failed: {e}"

    # Check push access and fork if needed
    forked = False
    try:
        has_push = _check_push_access(owner, repo)
    except Exception:
        has_push = False

    if not has_push:
        ctx.send_message(
            f"No push access to {owner}/{repo}. Creating a personal fork..."
        )
        try:
            fork_url = _create_fork_and_configure(
                owner, repo, str(project_dir)
            )
            forked = True
        except RuntimeError as e:
            # Fork failed — still usable, just can't push
            ctx.send_message(f"Fork creation failed: {e}")

    # Refresh project cache
    try:
        from app.projects_merged import refresh_projects
        refresh_projects(koan_root)
    except Exception:
        pass

    # Build result message
    lines = [f"Project '{project_name}' added to workspace."]
    lines.append(f"  Source: {owner}/{repo}")
    if forked:
        lines.append(f"  Fork: {fork_url}")
        lines.append("  Remotes: origin=fork, upstream=original")
    lines.append(f"  Path: {project_dir}")
    return "\n".join(lines)


def _parse_args(args):
    """Parse command arguments into (url, optional_name).

    Accepts:
    - https://github.com/owner/repo
    - https://github.com/owner/repo.git
    - git@github.com:owner/repo.git
    - owner/repo
    - Any of the above followed by an optional project name
    """
    parts = args.split()

    url_part = parts[0]
    name_part = parts[1] if len(parts) > 1 else None

    # Normalize the URL
    url = _normalize_github_url(url_part)

    return url, name_part


def _normalize_github_url(raw):
    """Normalize various GitHub URL formats to https://github.com/owner/repo.

    Returns the normalized URL or None if not recognizable.
    """
    raw = raw.strip().rstrip("/")

    # HTTPS URL: https://github.com/owner/repo[.git]
    m = re.match(
        r"https?://github\.com/([a-zA-Z0-9._-]+)/([a-zA-Z0-9._-]+?)(?:\.git)?$",
        raw,
    )
    if m:
        return f"https://github.com/{m.group(1)}/{m.group(2)}"

    # SSH URL: git@github.com:owner/repo[.git]
    m = re.match(
        r"git@github\.com:([a-zA-Z0-9._-]+)/([a-zA-Z0-9._-]+?)(?:\.git)?$",
        raw,
    )
    if m:
        return f"https://github.com/{m.group(1)}/{m.group(2)}"

    # Short form: owner/repo
    m = re.match(r"^([a-zA-Z0-9._-]+)/([a-zA-Z0-9._-]+)$", raw)
    if m:
        return f"https://github.com/{m.group(1)}/{m.group(2)}"

    return None


def _extract_owner_repo(url):
    """Extract (owner, repo) from a normalized GitHub URL."""
    m = re.match(
        r"https?://github\.com/([a-zA-Z0-9._-]+)/([a-zA-Z0-9._-]+?)(?:\.git)?$",
        url,
    )
    if m:
        return m.group(1), m.group(2)
    return None, None


def _git_clone(url, target_dir):
    """Clone a git repository.

    Raises RuntimeError on failure.
    """
    run_git_strict("clone", url, target_dir, timeout=120)


def _check_push_access(owner, repo):
    """Check if the current gh user has push access to owner/repo.

    Returns True if push/admin/maintain, False otherwise.
    """
    from app.github import run_gh

    output = run_gh(
        "repo", "view", f"{owner}/{repo}",
        "--json", "viewerPermission",
        "--jq", ".viewerPermission",
        timeout=15,
    )
    permission = output.strip().upper()
    return permission in ("ADMIN", "MAINTAIN", "WRITE")


def _create_fork_and_configure(owner, repo, project_dir):
    """Create a personal fork and reconfigure remotes.

    - Fork via gh repo fork
    - Set origin to fork URL
    - Set upstream to original URL

    Returns the fork URL string.
    Raises RuntimeError on failure.
    """
    from app.github import run_gh

    # Create fork (gh repo fork does not clone — it creates on GitHub)
    try:
        run_gh(
            "repo", "fork", f"{owner}/{repo}",
            "--clone=false",
            timeout=60,
        )
    except RuntimeError as e:
        # gh returns error if fork already exists — that's fine
        if "already exists" not in str(e).lower():
            raise

    # Determine the fork URL (current gh user's fork)
    gh_user = _get_gh_username()
    if not gh_user:
        raise RuntimeError("Cannot determine GitHub username for fork URL")

    fork_url = f"https://github.com/{gh_user}/{repo}.git"
    original_url = f"https://github.com/{owner}/{repo}.git"

    # Reconfigure remotes: origin=fork, upstream=original
    run_git_strict("remote", "rename", "origin", "upstream", cwd=project_dir)
    run_git_strict("remote", "add", "origin", fork_url, cwd=project_dir)

    return f"{gh_user}/{repo}"


def _get_gh_username():
    """Get the current GitHub username."""
    from app.github import run_gh

    try:
        return run_gh("api", "user", "--jq", ".login", timeout=15)
    except Exception:
        return None


