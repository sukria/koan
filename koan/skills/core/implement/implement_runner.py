"""
Kōan -- Implement runner.

Reads a GitHub issue containing a plan and invokes Claude to implement it.
The runner extracts the most recent plan iteration from the issue (body or
latest plan comment), ignoring older content, and feeds it to Claude with
an optional user-provided context (e.g. "Phase 1 to 3").

CLI:
    python3 -m skills.core.implement.implement_runner --project-path <path> --issue-url <url>
    python3 -m skills.core.implement.implement_runner --project-path <path> --issue-url <url> --context "Phase 1 to 3"
"""

import logging
import os
import re
from pathlib import Path
from typing import List, Optional, Tuple

from app.claude_step import _run_git
from app.github import detect_parent_repo, fetch_issue_with_comments, run_gh, pr_create
from app.github_url_parser import parse_issue_url
from app.prompts import load_prompt, load_skill_prompt

logger = logging.getLogger(__name__)


def _guess_project_name(project_path: str) -> str:
    """Extract project name from the directory path."""
    return Path(project_path).name


# Regex pattern matching plan structure markers
_PLAN_MARKER_RE = re.compile(
    r"^#{2,}\s+(?:Implementation Phases|Phase \d+|Summary|Changes in this iteration)",
    re.MULTILINE | re.IGNORECASE,
)


def run_implement(
    project_path: str,
    issue_url: str,
    context: Optional[str] = None,
    notify_fn=None,
    skill_dir: Optional[Path] = None,
) -> Tuple[bool, str]:
    """Execute the implement pipeline.

    Fetches the GitHub issue, extracts the most recent plan, and invokes
    Claude to implement it.

    Args:
        project_path: Local path to the project repository.
        issue_url: GitHub issue URL containing the plan.
        context: Optional additional context (e.g. "Phase 1 to 3").
        notify_fn: Notification function (defaults to send_telegram).
        skill_dir: Path to the implement skill directory for prompt loading.

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
        f"\U0001f528 Implementing issue #{issue_number} "
        f"({owner}/{repo}){context_label}..."
    )

    # Fetch issue content
    try:
        title, body, comments = fetch_issue_with_comments(
            owner, repo, issue_number
        )
    except Exception as e:
        return False, f"Failed to fetch issue: {str(e)[:300]}"

    # Extract the most recent plan
    plan = _extract_latest_plan(body, comments)
    if not plan:
        return False, (
            f"No plan found in issue #{issue_number}. "
            "The issue should contain implementation phases."
        )

    # Invoke Claude with the plan
    try:
        output = _execute_implementation(
            project_path=project_path,
            issue_url=issue_url,
            issue_title=title,
            plan=plan,
            context=context or "Implement the full plan.",
            skill_dir=skill_dir,
            issue_number=str(issue_number),
        )
    except Exception as e:
        return False, f"Implementation failed: {str(e)[:300]}"

    if not output:
        return False, "Claude returned empty output."

    # Post-implementation: submit draft PR
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
            f"\u2705 Implementation complete for issue #{issue_number}"
            f"{context_label}\nDraft PR: {pr_url}"
        )
        summary = (
            f"Implementation complete for #{issue_number}{context_label}"
            f"\nDraft PR: {pr_url}"
        )
    elif branch not in ("main", "master"):
        notify_fn(
            f"\u2705 Implementation complete for issue #{issue_number}"
            f"{context_label}\nBranch: {branch} (PR creation failed)"
        )
        summary = (
            f"Implementation complete for #{issue_number}{context_label}"
            f"\nBranch: {branch}"
        )
    else:
        notify_fn(
            f"\u26a0\ufe0f Implementation complete for issue #{issue_number}"
            f"{context_label} \u2014 changes landed on {branch}, no PR created"
        )
        summary = (
            f"Implementation complete for #{issue_number}{context_label}"
            f" (on {branch}, no PR)"
        )

    return True, summary


def _is_plan_content(text: str) -> bool:
    """Check if text contains plan structure markers.
    
    Args:
        text: Text to check for plan markers.
        
    Returns:
        True if text contains markdown headings indicating a plan structure.
    """
    if not text:
        return False
    return bool(_PLAN_MARKER_RE.search(text))


def _extract_latest_plan(body: str, comments: List[dict]) -> str:
    """Extract the most recent plan from issue body and comments.

    Strategy: scan comments from newest to oldest. The first comment
    that contains plan markers is the latest plan iteration. If no
    comment has a plan, fall back to the issue body.

    Args:
        body: Issue body text.
        comments: List of comment dicts with keys: author, date, body.

    Returns:
        The plan text, or empty string if no plan found.
    """
    # Check comments from newest to oldest
    for comment in reversed(comments):
        comment_body = comment.get("body", "")
        if _is_plan_content(comment_body):
            return comment_body

    # Fall back to issue body if it has plan markers
    if _is_plan_content(body):
        return body

    # If no plan markers found, assume the entire body is the plan
    # (allows non-standard plan formats)
    return body.strip()


def _build_prompt(
    issue_url: str,
    issue_title: str,
    plan: str,
    context: str,
    skill_dir: Optional[Path] = None,
    branch_prefix: str = "koan/",
    issue_number: str = "",
) -> str:
    """Build the implementation prompt from the issue and plan.

    Args:
        issue_url: GitHub issue URL.
        issue_title: Issue title.
        plan: Extracted plan text.
        context: Additional user context.
        skill_dir: Path to skill directory for prompt loading.
        branch_prefix: Git branch prefix for the project.
        issue_number: Issue number for branch naming.

    Returns:
        Formatted prompt string.
    """
    template_vars = dict(
        ISSUE_URL=issue_url,
        ISSUE_TITLE=issue_title,
        PLAN=plan,
        CONTEXT=context,
        BRANCH_PREFIX=branch_prefix,
        ISSUE_NUMBER=issue_number,
    )

    if skill_dir is not None:
        return load_skill_prompt(skill_dir, "implement", **template_vars)

    return load_prompt("implement", **template_vars)


def _generate_pr_summary(
    project_path: str,
    issue_title: str,
    issue_url: str,
    commit_subjects: List[str],
    skill_dir: Optional[Path] = None,
) -> str:
    """Generate a PR summary using the lightweight model.

    Falls back to a bullet list of commit subjects if the model call
    fails or times out.

    Args:
        project_path: Path to the project repository.
        issue_title: Issue title for context.
        issue_url: Issue URL for cross-reference.
        commit_subjects: List of commit subject lines.
        skill_dir: Path to skill directory for prompt loading.

    Returns:
        PR summary text.
    """
    commits_text = "\n".join(f"- {s}" for s in commit_subjects) or "(no commits)"
    fallback = f"Implements {issue_url}\n\n{commits_text}"

    try:
        if skill_dir is not None:
            prompt = load_skill_prompt(
                skill_dir, "pr_summary",
                ISSUE_URL=issue_url,
                ISSUE_TITLE=issue_title,
                COMMIT_SUBJECTS=commits_text,
            )
        else:
            prompt = load_prompt(
                "pr_summary",
                ISSUE_URL=issue_url,
                ISSUE_TITLE=issue_title,
                COMMIT_SUBJECTS=commits_text,
            )

        from app.cli_provider import run_command
        output = run_command(
            prompt, project_path,
            allowed_tools=[],
            model_key="lightweight",
            max_turns=1,
            timeout=300,
        )
        return output.strip() if output and output.strip() else fallback
    except Exception as e:
        logger.debug("PR summary generation failed: %s", e)
        return fallback


def _execute_implementation(
    project_path: str,
    issue_url: str,
    issue_title: str,
    plan: str,
    context: str,
    skill_dir: Optional[Path] = None,
    issue_number: str = "",
) -> str:
    """Execute the implementation via Claude CLI.

    Args:
        project_path: Path to the project repository.
        issue_url: GitHub issue URL.
        issue_title: Issue title.
        plan: Extracted plan text.
        context: Additional user context.
        skill_dir: Path to skill directory for prompt loading.
        issue_number: Issue number for branch naming.

    Returns:
        Claude CLI output.
    """
    from app.config import get_branch_prefix
    branch_prefix = get_branch_prefix()

    prompt = _build_prompt(
        issue_url, issue_title, plan, context, skill_dir,
        branch_prefix=branch_prefix,
        issue_number=issue_number,
    )

    from app.cli_provider import CLAUDE_TOOLS, run_command
    return run_command(
        prompt, project_path,
        allowed_tools=sorted(CLAUDE_TOOLS),
        max_turns=50, timeout=900,
    )


# ---------------------------------------------------------------------------
# Post-implementation: draft PR submission
# ---------------------------------------------------------------------------


def _get_current_branch(project_path: str) -> str:
    """Return the current git branch name, or 'main' on error."""
    try:
        return _run_git(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=project_path,
        ).strip()
    except Exception:
        return "main"


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


def _get_commit_subjects(project_path: str, base_branch: str = "main") -> List[str]:
    """Return commit subject lines from base_branch..HEAD."""
    try:
        output = _run_git(
            ["git", "log", f"{base_branch}..HEAD", "--format=%s"],
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

    Returns dict with 'repo' (owner/repo) and 'is_fork' (bool).
    """
    from app.projects_config import load_projects_config, get_project_submit_to_repository

    koan_root = os.environ.get("KOAN_ROOT", "")
    if koan_root:
        config = load_projects_config(koan_root)
        if config:
            submit_cfg = get_project_submit_to_repository(config, project_name)
            if submit_cfg.get("repo"):
                return {"repo": submit_cfg["repo"], "is_fork": True}

    # Auto-detect fork parent
    parent = detect_parent_repo(project_path)
    if parent:
        return {"repo": parent, "is_fork": True}

    # Fall back to issue's owner/repo
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
    """Push branch and create a draft PR after successful implementation.

    Returns the PR URL on success, or None on failure.
    """
    # Check current branch
    branch = _get_current_branch(project_path)
    if branch in ("main", "master"):
        logger.info("On %s — skipping PR creation", branch)
        return None

    # Check for existing PR on this branch
    try:
        existing = run_gh(
            "pr", "list", "--head", branch, "--json", "url", "--jq", ".[0].url",
            cwd=project_path, timeout=15,
        ).strip()
        if existing:
            logger.info("PR already exists: %s", existing)
            return existing
    except Exception:
        pass  # No existing PR, continue

    # Get commit subjects
    base_branch = _resolve_base_branch(project_name)
    commits = _get_commit_subjects(project_path, base_branch=base_branch)
    if not commits:
        logger.info("No commits on branch — skipping PR creation")
        return None

    # Push branch
    try:
        _run_git(
            ["git", "push", "-u", "origin", branch],
            cwd=project_path, timeout=120,
        )
    except Exception as e:
        logger.warning("Failed to push branch: %s", e)
        return None

    # Generate PR summary
    summary = _generate_pr_summary(
        project_path, issue_title, issue_url, commits, skill_dir,
    )

    # Build PR body
    pr_body = f"## Summary\n\n{summary}\n\nCloses {issue_url}\n\n---\n*Generated by Kōan /implement*"

    # Resolve where to submit
    target = _resolve_submit_target(project_path, project_name, owner, repo)
    pr_title = f"Implement: {issue_title}"[:70]

    # Build pr_create kwargs
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

    # Create draft PR
    try:
        pr_url = pr_create(**pr_kwargs)
    except Exception as e:
        logger.warning("Failed to create PR: %s", e)
        return None

    # Comment on the issue with the PR link
    try:
        run_gh(
            "issue", "comment", str(issue_number),
            "--repo", f"{owner}/{repo}",
            "--body", f"Draft PR submitted: {pr_url}",
            cwd=project_path, timeout=15,
        )
    except Exception as e:
        logger.debug("Failed to comment on issue: %s", e)

    return pr_url


# ---------------------------------------------------------------------------
# CLI entry point -- python3 -m app.implement_runner
# ---------------------------------------------------------------------------

def main(argv=None):
    """CLI entry point for implement_runner.

    Returns exit code (0 = success, 1 = failure).
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Implement a plan from a GitHub issue."
    )
    parser.add_argument(
        "--project-path", required=True,
        help="Local path to the project repository",
    )
    parser.add_argument(
        "--issue-url", required=True,
        help="GitHub issue URL containing the plan",
    )
    parser.add_argument(
        "--context",
        help="Additional context (e.g. 'Phase 1 to 3')",
        default=None,
    )
    cli_args = parser.parse_args(argv)

    skill_dir = Path(__file__).resolve().parent

    success, summary = run_implement(
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
