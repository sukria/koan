"""
Koan -- Fix runner.

Reads a GitHub issue, builds a fix prompt, and invokes Claude to fix it.
Unlike implement_runner (which requires a pre-existing plan), fix_runner
takes a raw issue and lets Claude handle the full pipeline: understand,
plan, test, fix, and submit a PR.

CLI:
    python3 -m skills.core.fix.fix_runner --project-path <path> --issue-url <url>
    python3 -m skills.core.fix.fix_runner --project-path <path> --issue-url <url> --context "backend only"
"""

import logging
import os
from pathlib import Path
from typing import List, Optional, Tuple

from app.git_utils import run_git_strict
from app.github import detect_parent_repo, fetch_issue_with_comments, run_gh, pr_create
from app.github_url_parser import parse_issue_url
from app.prompts import load_skill_prompt

logger = logging.getLogger(__name__)


def _guess_project_name(project_path: str) -> str:
    """Extract project name from the directory path."""
    return Path(project_path).name


def _resolve_base_branch(project_name: str) -> str:
    """Resolve the base branch for a project from config, defaulting to 'main'."""
    try:
        from app.projects_config import load_projects_config, get_project_auto_merge
        koan_root = os.environ.get("KOAN_ROOT", "")
        if koan_root:
            config = load_projects_config(koan_root)
            if config:
                am = get_project_auto_merge(config, project_name)
                return am.get("base_branch", "main")
    except Exception:
        pass
    return "main"


def run_fix(
    project_path: str,
    issue_url: str,
    context: Optional[str] = None,
    notify_fn=None,
    skill_dir: Optional[Path] = None,
) -> Tuple[bool, str]:
    """Execute the fix pipeline.

    Fetches the GitHub issue, builds a fix prompt, and invokes Claude to
    understand, plan, test, and fix the issue.

    Args:
        project_path: Local path to the project repository.
        issue_url: GitHub issue URL.
        context: Optional additional context (e.g. "backend only").
        notify_fn: Notification function (defaults to send_telegram).
        skill_dir: Path to the fix skill directory for prompt loading.

    Returns:
        (success, summary) tuple.
    """
    if notify_fn is None:
        from app.notify import send_telegram
        notify_fn = send_telegram

    # Parse issue URL
    try:
        owner, repo, issue_number = parse_issue_url(issue_url)
    except ValueError as e:
        return False, str(e)

    context_label = f" ({context})" if context else ""
    notify_fn(
        f"\U0001f527 Fixing issue #{issue_number} "
        f"({owner}/{repo}){context_label}..."
    )

    # Fetch issue content
    try:
        title, body, comments = fetch_issue_with_comments(
            owner, repo, issue_number
        )
    except Exception as e:
        return False, f"Failed to fetch issue: {str(e)[:300]}"

    if not body and not comments:
        return False, f"Issue #{issue_number} has no content."

    # Build full issue body (include relevant comments)
    full_body = _build_issue_body(body, comments)

    # Invoke Claude with the fix prompt
    try:
        output = _execute_fix(
            project_path=project_path,
            issue_url=issue_url,
            issue_title=title,
            issue_body=full_body,
            context=context or "Fix the issue completely.",
            skill_dir=skill_dir,
            issue_number=str(issue_number),
        )
    except Exception as e:
        return False, f"Fix failed: {str(e)[:300]}"

    if not output:
        return False, "Claude returned empty output."

    # Post-fix: submit draft PR
    pr_url = None
    try:
        pr_url = _submit_draft_pr(
            project_path=project_path,
            project_name=_guess_project_name(project_path),
            owner=owner,
            repo=repo,
            issue_number=str(issue_number),
            issue_title=title,
            issue_url=issue_url,
            skill_dir=skill_dir,
        )
    except Exception as e:
        logger.warning("PR submission failed: %s", e)

    # Build notification and summary
    branch = _get_current_branch(project_path)
    if pr_url:
        notify_fn(
            f"\u2705 Fix complete for issue #{issue_number}"
            f"{context_label}\nDraft PR: {pr_url}"
        )
        summary = (
            f"Fix complete for #{issue_number}{context_label}"
            f"\nDraft PR: {pr_url}"
        )
    elif branch not in ("main", "master"):
        notify_fn(
            f"\u2705 Fix complete for issue #{issue_number}"
            f"{context_label}\nBranch: {branch} (PR creation failed)"
        )
        summary = (
            f"Fix complete for #{issue_number}{context_label}"
            f"\nBranch: {branch}"
        )
    else:
        notify_fn(
            f"\u26a0\ufe0f Fix complete for issue #{issue_number}"
            f"{context_label} \u2014 changes landed on {branch}, no PR created"
        )
        summary = (
            f"Fix complete for #{issue_number}{context_label}"
            f" (on {branch}, no PR)"
        )

    return True, summary


def _build_issue_body(body: str, comments: List[dict]) -> str:
    """Build full issue content including relevant comments.

    Includes the issue body and any comments that add context
    (e.g. reproduction steps, additional details). Skips bot comments
    and very short comments.
    """
    parts = [body.strip()] if body else []

    for comment in comments:
        comment_body = comment.get("body", "").strip()
        author = comment.get("author", "")

        # Skip bot comments and very short comments
        if "[bot]" in author or len(comment_body) < 20:
            continue

        parts.append(f"\n---\n**Comment by {author}**:\n{comment_body}")

    return "\n".join(parts)


def _execute_fix(
    project_path: str,
    issue_url: str,
    issue_title: str,
    issue_body: str,
    context: str,
    skill_dir: Optional[Path] = None,
    issue_number: str = "",
) -> str:
    """Execute the fix via Claude CLI.

    Args:
        project_path: Path to the project repository.
        issue_url: GitHub issue URL.
        issue_title: Issue title.
        issue_body: Full issue content.
        context: Additional user context.
        skill_dir: Path to skill directory for prompt loading.
        issue_number: Issue number for branch naming.

    Returns:
        Claude CLI output.
    """
    from app.config import get_branch_prefix
    branch_prefix = get_branch_prefix()

    prompt = _build_prompt(
        issue_url, issue_title, issue_body, context, skill_dir,
        branch_prefix=branch_prefix,
        issue_number=issue_number,
    )

    from app.cli_provider import CLAUDE_TOOLS, run_command
    return run_command(
        prompt, project_path,
        allowed_tools=sorted(CLAUDE_TOOLS),
        max_turns=50, timeout=900,
    )


def _build_prompt(
    issue_url: str,
    issue_title: str,
    issue_body: str,
    context: str,
    skill_dir: Optional[Path] = None,
    branch_prefix: str = "koan/",
    issue_number: str = "",
) -> str:
    """Build the fix prompt from the issue content."""
    template_vars = dict(
        ISSUE_URL=issue_url,
        ISSUE_TITLE=issue_title,
        ISSUE_BODY=issue_body,
        CONTEXT=context,
        BRANCH_PREFIX=branch_prefix,
        ISSUE_NUMBER=issue_number,
    )

    if skill_dir is not None:
        return load_skill_prompt(skill_dir, "fix", **template_vars)

    from app.prompts import load_prompt
    return load_prompt("fix", **template_vars)


# ---------------------------------------------------------------------------
# Post-fix: draft PR submission (reuses implement_runner patterns)
# ---------------------------------------------------------------------------

def _get_current_branch(project_path: str) -> str:
    """Return the current git branch name, or 'main' on error."""
    try:
        return run_git_strict(
            "rev-parse", "--abbrev-ref", "HEAD",
            cwd=project_path,
        ).strip()
    except Exception:
        return "main"


def _get_commit_subjects(project_path: str, base_branch: str = "main") -> List[str]:
    """Return commit subject lines from base_branch..HEAD."""
    try:
        output = run_git_strict(
            "log", f"{base_branch}..HEAD", "--format=%s",
            cwd=project_path,
        )
        return [s for s in output.strip().splitlines() if s.strip()]
    except Exception:
        return []


def _get_fork_owner(project_path: str) -> str:
    """Return the GitHub owner login of the current repo."""
    try:
        return run_gh(
            "repo", "view", "--json", "owner", "--jq", ".owner.login",
            cwd=project_path, timeout=15,
        ).strip()
    except Exception:
        return ""


def _resolve_submit_target(
    project_path: str,
    project_name: str,
    owner: str,
    repo: str,
) -> dict:
    """Determine where to submit the PR.

    Resolution order:
    1. submit_to_repository in projects.yaml config
    2. Auto-detect fork parent via gh
    3. Fall back to issue's owner/repo
    """
    from app.projects_config import load_projects_config, get_project_submit_to_repository

    koan_root = os.environ.get("KOAN_ROOT", "")
    if koan_root:
        config = load_projects_config(koan_root)
        if config:
            submit_cfg = get_project_submit_to_repository(config, project_name)
            if submit_cfg.get("repo"):
                return {"repo": submit_cfg["repo"], "is_fork": True}

    parent = detect_parent_repo(project_path)
    if parent:
        return {"repo": parent, "is_fork": True}

    return {"repo": f"{owner}/{repo}", "is_fork": False}


def _submit_draft_pr(
    project_path: str,
    project_name: str,
    owner: str,
    repo: str,
    issue_number: str,
    issue_title: str,
    issue_url: str,
    skill_dir: Optional[Path] = None,
) -> Optional[str]:
    """Push branch and create a draft PR after successful fix."""
    branch = _get_current_branch(project_path)
    if branch in ("main", "master"):
        logger.info("On %s — skipping PR creation", branch)
        return None

    # Check for existing PR
    try:
        existing = run_gh(
            "pr", "list", "--head", branch, "--json", "url", "--jq", ".[0].url",
            cwd=project_path, timeout=15,
        ).strip()
        if existing:
            logger.info("PR already exists: %s", existing)
            return existing
    except Exception:
        pass

    base_branch = _resolve_base_branch(project_name)
    commits = _get_commit_subjects(project_path, base_branch=base_branch)
    if not commits:
        logger.info("No commits on branch — skipping PR creation")
        return None

    # Push branch
    try:
        run_git_strict(
            "push", "-u", "origin", branch,
            cwd=project_path, timeout=120,
        )
    except Exception as e:
        logger.warning("Failed to push branch: %s", e)
        return None

    # Build PR body
    commits_text = "\n".join(f"- {s}" for s in commits)
    pr_body = (
        f"## Summary\n\n"
        f"Fixes {issue_url}\n\n"
        f"## Changes\n\n{commits_text}\n\n"
        f"---\n*Generated by Koan /fix*"
    )

    # Resolve where to submit
    target = _resolve_submit_target(project_path, project_name, owner, repo)
    pr_title = f"fix: {issue_title}"[:70]

    pr_kwargs = {
        "title": pr_title,
        "body": pr_body,
        "draft": True,
        "cwd": project_path,
    }

    if target["is_fork"]:
        pr_kwargs["repo"] = target["repo"]
        fork_owner = _get_fork_owner(project_path)
        if fork_owner:
            pr_kwargs["head"] = f"{fork_owner}:{branch}"

    try:
        pr_url = pr_create(**pr_kwargs)
    except Exception as e:
        logger.warning("Failed to create PR: %s", e)
        return None

    # Comment on the issue
    try:
        run_gh(
            "issue", "comment", str(issue_number),
            "--repo", f"{owner}/{repo}",
            "--body", f"Draft fix submitted: {pr_url}",
            cwd=project_path, timeout=15,
        )
    except Exception as e:
        logger.debug("Failed to comment on issue: %s", e)

    return pr_url


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv=None):
    """CLI entry point for fix_runner."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Fix a GitHub issue end-to-end."
    )
    parser.add_argument(
        "--project-path", required=True,
        help="Local path to the project repository",
    )
    parser.add_argument(
        "--issue-url", required=True,
        help="GitHub issue URL to fix",
    )
    parser.add_argument(
        "--context",
        help="Additional context (e.g. 'backend only')",
        default=None,
    )
    cli_args = parser.parse_args(argv)

    skill_dir = Path(__file__).resolve().parent

    success, summary = run_fix(
        project_path=cli_args.project_path,
        issue_url=cli_args.issue_url,
        context=cli_args.context,
        skill_dir=skill_dir,
    )
    print(summary)
    return 0 if success else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
