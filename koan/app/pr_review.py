#!/usr/bin/env python3
"""
Koan -- Pull Request review and update workflow.

Handles /pr command: reads a GitHub PR, understands review comments,
runs Claude Code to implement requested changes, then pushes and comments.
"""

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple

from app.notify import send_telegram
from app.utils import load_dotenv, get_model_config, build_claude_flags


def parse_pr_url(url: str) -> Tuple[str, str, str]:
    """Extract owner, repo, and PR number from a GitHub PR URL.

    Accepts formats:
        https://github.com/owner/repo/pull/123
        https://github.com/owner/repo/pull/123#...

    Returns:
        (owner, repo, pr_number) as strings.

    Raises:
        ValueError: If the URL doesn't match expected format.
    """
    match = re.match(
        r"https?://github\.com/([^/]+)/([^/]+)/pull/(\d+)",
        url.strip(),
    )
    if not match:
        raise ValueError(f"Invalid PR URL: {url}")
    return match.group(1), match.group(2), match.group(3)


def fetch_pr_context(owner: str, repo: str, pr_number: str) -> dict:
    """Fetch PR details, diff, and review comments via gh CLI.

    Returns a dict with keys: title, body, branch, base, diff, reviews, comments.
    """
    full_repo = f"{owner}/{repo}"

    # Fetch PR metadata
    pr_json = _gh(
        ["gh", "pr", "view", pr_number, "--repo", full_repo, "--json",
         "title,body,headRefName,baseRefName,state,author,url"]
    )

    # Fetch PR diff
    diff = _gh(
        ["gh", "pr", "diff", pr_number, "--repo", full_repo]
    )

    # Fetch review comments (inline code comments)
    comments_json = _gh(
        ["gh", "api", f"repos/{full_repo}/pulls/{pr_number}/comments",
         "--paginate", "--jq",
         r'.[] | "[\(.path):\(.line // .original_line)] @\(.user.login): \(.body)"']
    )

    # Fetch PR-level review comments (top-level reviews)
    reviews_json = _gh(
        ["gh", "api", f"repos/{full_repo}/pulls/{pr_number}/reviews",
         "--paginate", "--jq",
         r'.[] | select(.body != "") | "@\(.user.login) (\(.state)): \(.body)"']
    )

    # Fetch issue-level comments (conversation thread)
    issue_comments = _gh(
        ["gh", "api", f"repos/{full_repo}/issues/{pr_number}/comments",
         "--paginate", "--jq",
         r'.[] | "@\(.user.login): \(.body)"']
    )

    import json
    try:
        metadata = json.loads(pr_json)
    except (json.JSONDecodeError, TypeError):
        metadata = {}

    return {
        "title": metadata.get("title", ""),
        "body": metadata.get("body", ""),
        "branch": metadata.get("headRefName", ""),
        "base": metadata.get("baseRefName", "main"),
        "state": metadata.get("state", ""),
        "author": metadata.get("author", {}).get("login", ""),
        "url": metadata.get("url", ""),
        "diff": _truncate(diff, 8000),
        "review_comments": _truncate(comments_json, 4000),
        "reviews": _truncate(reviews_json, 3000),
        "issue_comments": _truncate(issue_comments, 3000),
    }


def build_pr_prompt(context: dict) -> str:
    """Build a prompt for Claude to address PR review feedback.

    Args:
        context: Dict from fetch_pr_context().

    Returns:
        Full prompt string for Claude Code CLI.
    """
    from app.prompts import load_prompt
    return load_prompt(
        "pr-review",
        TITLE=context["title"],
        BODY=context["body"],
        BRANCH=context["branch"],
        BASE=context["base"],
        DIFF=context["diff"],
        REVIEW_COMMENTS=context["review_comments"],
        REVIEWS=context["reviews"],
        ISSUE_COMMENTS=context["issue_comments"],
    )


def run_pr_review(
    owner: str,
    repo: str,
    pr_number: str,
    project_path: str,
) -> Tuple[bool, str]:
    """Execute the full PR review workflow.

    1. Fetch PR context
    2. Checkout the PR branch
    3. Run Claude Code to implement changes
    4. Run tests
    5. Push (force if needed)
    6. Comment on PR

    Args:
        owner: GitHub owner
        repo: GitHub repo name
        pr_number: PR number as string
        project_path: Local path to the project

    Returns:
        (success, summary) tuple.
    """
    full_repo = f"{owner}/{repo}"

    # Step 1: Fetch PR context
    send_telegram(f"Reading PR #{pr_number}...")
    try:
        context = fetch_pr_context(owner, repo, pr_number)
    except Exception as e:
        return False, f"Failed to fetch PR context: {e}"

    if not context["branch"]:
        return False, "Could not determine PR branch name."

    branch = context["branch"]

    # Step 2: Checkout the branch
    try:
        _run_git(["git", "fetch", "origin", branch], cwd=project_path)
        _run_git(["git", "checkout", branch], cwd=project_path)
        _run_git(["git", "pull", "origin", branch, "--rebase"], cwd=project_path)
    except Exception as e:
        return False, f"Failed to checkout branch {branch}: {e}"

    # Step 3: Run Claude Code to implement changes
    send_telegram(f"Analyzing review comments on `{branch}`...")
    prompt = build_pr_prompt(context)

    from app.cli_provider import build_full_command
    models = get_model_config()

    cmd = build_full_command(
        prompt=prompt,
        allowed_tools=["Bash", "Read", "Write", "Glob", "Grep", "Edit"],
        model=models["mission"],
        fallback=models["fallback"],
        max_turns=30,
    )

    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            timeout=600,  # 10 min max
            cwd=project_path,
        )
        claude_output = result.stdout.strip()
        if result.returncode != 0:
            stderr_snippet = result.stderr[-500:] if result.stderr else "no stderr"
            return False, f"Claude exited with code {result.returncode}: {stderr_snippet}"
    except subprocess.TimeoutExpired:
        return False, "Claude timed out (10 min limit)."

    # Step 4: Check if there are changes to commit
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True, text=True, cwd=project_path,
    )
    has_changes = bool(status.stdout.strip())

    if has_changes:
        # Stage and commit
        _run_git(["git", "add", "-A"], cwd=project_path)
        _run_git(
            ["git", "commit", "-m", f"pr-review: address feedback on #{pr_number}"],
            cwd=project_path,
        )

    # Step 5: Rebase on base branch and push
    try:
        _run_git(["git", "fetch", "origin", context["base"]], cwd=project_path)
        _run_git(
            ["git", "rebase", f"origin/{context['base']}"],
            cwd=project_path,
        )
    except Exception as e:
        # Rebase conflict — abort and report
        subprocess.run(
            ["git", "rebase", "--abort"],
            capture_output=True, cwd=project_path,
        )
        return False, f"Rebase conflict on {context['base']}: {e}"

    # Push (force to handle rebase)
    try:
        _run_git(
            ["git", "push", "origin", branch, "--force-with-lease"],
            cwd=project_path,
        )
    except Exception as e:
        return False, f"Push failed: {e}"

    # Step 6: Comment on PR
    summary = _build_summary(claude_output, has_changes)
    comment_body = (
        f"## PR Review Update\n\n"
        f"{summary}\n\n"
        f"Branch `{branch}` has been rebased on `{context['base']}` and force-pushed.\n\n"
        f"---\n"
        f"_Automated by Koan_"
    )

    try:
        _gh([
            "gh", "pr", "comment", pr_number,
            "--repo", full_repo,
            "--body", comment_body,
        ])
    except Exception as e:
        # Non-fatal — changes are pushed, comment is nice-to-have
        send_telegram(f"Changes pushed but failed to comment on PR: {e}")

    return True, summary


def _build_summary(claude_output: str, has_changes: bool) -> str:
    """Extract a concise summary from Claude's output."""
    if not has_changes:
        return "No code changes were needed — the review comments may have been addressed already or were non-actionable."

    # Take the last meaningful paragraph from Claude output as summary
    lines = claude_output.strip().splitlines()
    # Filter noise
    lines = [l for l in lines if l.strip() and not l.startswith("Error:")]

    if not lines:
        return "Changes were made to address review feedback."

    # Take last 500 chars as likely summary
    tail = "\n".join(lines)
    if len(tail) > 500:
        tail = tail[-500:]

    return f"Changes made:\n{tail}"


def _gh(cmd: list, timeout: int = 30) -> str:
    """Run a gh CLI command and return stdout."""
    result = subprocess.run(
        cmd,
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh command failed: {' '.join(cmd[:4])}... — {result.stderr[:200]}")
    return result.stdout.strip()


def _run_git(cmd: list, cwd: str = None, timeout: int = 60) -> str:
    """Run a git command, raise on failure."""
    result = subprocess.run(
        cmd,
        capture_output=True, text=True,
        timeout=timeout, cwd=cwd,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git failed: {' '.join(cmd)} — {result.stderr[:200]}")
    return result.stdout.strip()


def _truncate(text: str, max_chars: int) -> str:
    """Truncate text with indicator."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...(truncated)"
