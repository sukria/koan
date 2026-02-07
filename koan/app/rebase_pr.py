"""
Koan -- Pull Request rebase workflow.

Rebases a PR branch onto its target branch, analyzing review comments
and applying requested changes via Claude before pushing.

Pipeline:
1. Fetch PR metadata + comments from GitHub
2. Checkout the PR branch locally
3. Rebase onto the upstream target branch
4. Analyze review comments and apply changes (Claude-powered, if feedback exists)
5. Push the result (force-push to existing branch, or create new draft PR)
6. Comment on the PR with a summary
"""

import json
import re
from pathlib import Path
from typing import List, Optional, Tuple

from app.claude_step import _rebase_onto_target, _run_git, _truncate
from app.github import run_gh


def fetch_pr_context(owner: str, repo: str, pr_number: str) -> dict:
    """Fetch PR details, diff, and all comments via gh CLI.

    Returns a dict with keys: title, body, branch, base, state, author, url,
    diff, review_comments, reviews, issue_comments.
    """
    full_repo = f"{owner}/{repo}"

    # Fetch PR metadata
    pr_json = run_gh(
        "pr", "view", pr_number, "--repo", full_repo, "--json",
        "title,body,headRefName,baseRefName,state,author,url",
    )

    # Fetch PR diff
    diff = run_gh("pr", "diff", pr_number, "--repo", full_repo)

    # Fetch review comments (inline code comments)
    comments_json = run_gh(
        "api", f"repos/{full_repo}/pulls/{pr_number}/comments",
        "--paginate", "--jq",
        r'.[] | "[\(.path):\(.line // .original_line)] @\(.user.login): \(.body)"',
    )

    # Fetch PR-level review comments (top-level reviews)
    reviews_json = run_gh(
        "api", f"repos/{full_repo}/pulls/{pr_number}/reviews",
        "--paginate", "--jq",
        r'.[] | select(.body != "") | "@\(.user.login) (\(.state)): \(.body)"',
    )

    # Fetch issue-level comments (conversation thread)
    issue_comments = run_gh(
        "api", f"repos/{full_repo}/issues/{pr_number}/comments",
        "--paginate", "--jq",
        r'.[] | "@\(.user.login): \(.body)"',
    )

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


def build_comment_summary(context: dict) -> str:
    """Build a human-readable summary of all PR feedback.

    Useful for understanding what reviewers asked for before rebasing.
    """
    parts = []

    if context.get("reviews"):
        parts.append("### Reviews\n" + context["reviews"])
    if context.get("review_comments"):
        parts.append("### Inline Comments\n" + context["review_comments"])
    if context.get("issue_comments"):
        parts.append("### Discussion\n" + context["issue_comments"])

    if not parts:
        return "No comments or reviews found on this PR."

    return "\n\n".join(parts)


def run_rebase(
    owner: str,
    repo: str,
    pr_number: str,
    project_path: str,
    notify_fn=None,
    skill_dir: Optional[Path] = None,
) -> Tuple[bool, str]:
    """Execute the rebase pipeline for a pull request.

    Steps:
        1. Fetch PR context from GitHub (metadata + all comments)
        2. Checkout the PR branch locally
        3. Rebase onto the upstream target branch
        4. Analyze review comments and apply changes (if feedback exists)
        5. Push the result (try existing branch first, then create new PR)
        6. Comment on the PR with a summary

    Args:
        owner: GitHub owner (e.g., "sukria")
        repo: GitHub repo name (e.g., "koan")
        pr_number: PR number as string
        project_path: Local path to the project
        notify_fn: Optional callback for progress notifications.
        skill_dir: Path to the rebase skill directory for prompt resolution.

    Returns:
        (success, summary) tuple.
    """
    if notify_fn is None:
        from app.notify import send_telegram
        notify_fn = send_telegram

    full_repo = f"{owner}/{repo}"
    actions_log: List[str] = []

    # ── Step 1: Fetch PR context ──────────────────────────────────────
    notify_fn(f"Reading PR #{pr_number}...")
    try:
        context = fetch_pr_context(owner, repo, pr_number)
    except Exception as e:
        return False, f"Failed to fetch PR context: {e}"

    if not context["branch"]:
        return False, "Could not determine PR branch name."

    branch = context["branch"]
    base = context["base"]

    # Log comment summary for awareness
    comment_summary = build_comment_summary(context)
    if comment_summary and "No comments" not in comment_summary:
        actions_log.append("Read PR comments and review feedback")

    # ── Step 2: Checkout the PR branch ────────────────────────────────
    notify_fn(f"Checking out `{branch}`...")

    # Save current branch to restore later
    original_branch = _get_current_branch(project_path)

    try:
        _checkout_pr_branch(branch, project_path)
    except Exception as e:
        return False, f"Failed to checkout branch `{branch}`: {e}"

    # ── Step 3: Rebase onto target branch ─────────────────────────────
    notify_fn(f"Rebasing `{branch}` onto `{base}`...")
    rebase_remote = _rebase_onto_target(base, project_path)
    if rebase_remote:
        actions_log.append(f"Rebased `{branch}` onto `{rebase_remote}/{base}`")
    else:
        _safe_checkout(original_branch, project_path)
        return False, f"Rebase conflict on `{base}` (tried origin and upstream). Manual resolution required."

    # ── Step 4: Analyze review comments and apply changes ──────────────
    has_review_feedback = bool(
        context.get("review_comments", "").strip()
        or context.get("reviews", "").strip()
        or context.get("issue_comments", "").strip()
    )

    if has_review_feedback:
        notify_fn(f"Analyzing review comments on `{branch}`...")
        _apply_review_feedback(
            context, pr_number, project_path, actions_log,
            skill_dir=skill_dir,
        )

    # ── Step 5: Push the result ───────────────────────────────────────
    notify_fn(f"Pushing `{branch}`...")
    push_result = _push_with_fallback(
        branch, base, full_repo, pr_number, context, project_path
    )
    actions_log.extend(push_result["actions"])

    if not push_result["success"]:
        _safe_checkout(original_branch, project_path)
        return False, (
            f"Push failed: {push_result.get('error', 'unknown')}\n\n"
            f"Actions completed:\n" +
            "\n".join(f"- {a}" for a in actions_log)
        )

    # ── Step 6: Comment on the PR ─────────────────────────────────────
    comment_body = _build_rebase_comment(
        pr_number, branch, base, actions_log, context
    )

    try:
        run_gh(
            "pr", "comment", pr_number,
            "--repo", full_repo,
            "--body", comment_body,
        )
        actions_log.append("Commented on PR")
    except Exception as e:
        # Non-fatal — the rebase itself succeeded
        actions_log.append(f"Comment failed (non-fatal): {str(e)[:100]}")

    # Restore original branch
    _safe_checkout(original_branch, project_path)

    summary = f"PR #{pr_number} rebased.\n" + "\n".join(
        f"- {a}" for a in actions_log
    )
    return True, summary


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_rebase_prompt(context: dict, skill_dir: Optional[Path] = None) -> str:
    """Build a prompt for Claude to analyze and apply review feedback."""
    if skill_dir is not None:
        from app.prompts import load_skill_prompt
        return load_skill_prompt(
            skill_dir, "rebase",
            TITLE=context["title"],
            BODY=context.get("body", ""),
            BRANCH=context["branch"],
            BASE=context["base"],
            DIFF=context.get("diff", ""),
            REVIEW_COMMENTS=context.get("review_comments", ""),
            REVIEWS=context.get("reviews", ""),
            ISSUE_COMMENTS=context.get("issue_comments", ""),
        )
    from app.prompts import load_prompt
    return load_prompt(
        "rebase",
        TITLE=context["title"],
        BODY=context.get("body", ""),
        BRANCH=context["branch"],
        BASE=context["base"],
        DIFF=context.get("diff", ""),
        REVIEW_COMMENTS=context.get("review_comments", ""),
        REVIEWS=context.get("reviews", ""),
        ISSUE_COMMENTS=context.get("issue_comments", ""),
    )


def _apply_review_feedback(
    context: dict,
    pr_number: str,
    project_path: str,
    actions_log: List[str],
    skill_dir: Optional[Path] = None,
) -> None:
    """Analyze review comments via Claude and apply requested changes."""
    from app.claude_step import run_claude_step

    prompt = _build_rebase_prompt(context, skill_dir=skill_dir)
    run_claude_step(
        prompt=prompt,
        project_path=project_path,
        commit_msg=f"rebase: apply review feedback on #{pr_number}",
        success_label="Applied review feedback",
        failure_label="Review feedback step failed",
        actions_log=actions_log,
        max_turns=20,
    )


def _get_current_branch(project_path: str) -> str:
    """Get the current branch name."""
    try:
        return _run_git(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=project_path,
        )
    except Exception:
        return "main"


def _checkout_pr_branch(branch: str, project_path: str) -> None:
    """Checkout the PR branch, fetching from origin first."""
    _run_git(["git", "fetch", "origin", branch], cwd=project_path)

    # Try to checkout — may already exist locally
    try:
        _run_git(["git", "checkout", branch], cwd=project_path)
    except Exception:
        # Branch doesn't exist locally — create tracking branch
        _run_git(
            ["git", "checkout", "-b", branch, f"origin/{branch}"],
            cwd=project_path,
        )

    # Reset to origin's version to ensure clean state
    _run_git(
        ["git", "reset", "--hard", f"origin/{branch}"],
        cwd=project_path,
    )


def _push_with_fallback(
    branch: str,
    base: str,
    full_repo: str,
    pr_number: str,
    context: dict,
    project_path: str,
) -> dict:
    """Push rebased branch, falling back to new draft PR if permission denied.

    Returns:
        dict with keys: success (bool), actions (list), error (str).
    """
    actions = []

    # Option 1: Try force-pushing to the existing branch
    try:
        _run_git(
            ["git", "push", "origin", branch, "--force-with-lease"],
            cwd=project_path,
        )
        actions.append(f"Force-pushed `{branch}`")
        return {"success": True, "actions": actions, "error": ""}
    except Exception as push_error:
        error_msg = str(push_error)

    # Option 2: Permission denied — create a new draft PR
    if not _is_permission_error(error_msg):
        return {
            "success": False,
            "actions": actions,
            "error": error_msg,
        }

    # Create new branch and draft PR
    new_branch = f"koan/rebase-{branch.replace('/', '-')}"
    try:
        _run_git(
            ["git", "checkout", "-b", new_branch],
            cwd=project_path,
        )
        _run_git(
            ["git", "push", "-u", "origin", new_branch],
            cwd=project_path,
        )
        actions.append(f"Created new branch `{new_branch}` (no push permission on `{branch}`)")

        # Create draft PR
        title = context.get("title", f"Rebase of #{pr_number}")
        new_pr_body = (
            f"Supersedes #{pr_number}.\n\n"
            f"This PR contains the rebased version of `{branch}` onto `{base}`.\n"
            f"Original PR: {context.get('url', f'#{pr_number}')}\n\n"
            f"---\n_Automated by Koan_"
        )
        new_pr_url = run_gh(
            "pr", "create",
            "--repo", full_repo,
            "--head", new_branch,
            "--base", base,
            "--title", f"[Rebase] {title}",
            "--body", new_pr_body,
            "--draft",
        )
        actions.append(f"Created draft PR: {new_pr_url.strip()}")

        # Comment on the original PR to cross-link
        new_pr_match = re.search(r'/pull/(\d+)', new_pr_url)
        new_pr_ref = new_pr_match.group(0) if new_pr_match else new_pr_url.strip()

        try:
            run_gh(
                "pr", "comment", pr_number,
                "--repo", full_repo,
                "--body",
                f"This PR has been rebased and superseded by {new_pr_ref}.\n\n"
                f"The new PR contains the same changes rebased onto `{base}`.\n\n"
                f"---\n_Automated by Koan_",
            )
            actions.append("Cross-linked original PR")
        except Exception:
            pass

        return {"success": True, "actions": actions, "error": ""}

    except Exception as e:
        return {
            "success": False,
            "actions": actions,
            "error": f"Failed to create fallback PR: {e}",
        }


def _is_permission_error(error_msg: str) -> bool:
    """Check if an error message indicates a permission/access problem."""
    indicators = [
        "permission", "denied", "forbidden", "403",
        "protected branch", "not allowed",
        "unable to access", "authentication failed",
    ]
    lower = error_msg.lower()
    return any(ind in lower for ind in indicators)


def _build_rebase_comment(
    pr_number: str,
    branch: str,
    base: str,
    actions_log: List[str],
    context: dict,
) -> str:
    """Build a markdown comment summarizing the rebase."""
    title = context.get("title", f"PR #{pr_number}")

    actions_md = "\n".join(
        f"- {a}" for a in actions_log
    ) if actions_log else "- No changes needed"

    return (
        f"## Rebase: {title}\n\n"
        f"Branch `{branch}` has been rebased onto `{base}` and force-pushed.\n\n"
        f"### Actions\n\n"
        f"{actions_md}\n\n"
        f"---\n"
        f"_Automated by Koan_"
    )


def _safe_checkout(branch: str, project_path: str) -> None:
    """Checkout a branch without raising on failure."""
    try:
        _run_git(["git", "checkout", branch], cwd=project_path)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# CLI entry point — python3 -m app.rebase_pr <url> --project-path <path>
# ---------------------------------------------------------------------------

def main(argv=None):
    """CLI entry point for rebase_pr.

    Returns exit code (0 = success, 1 = failure).
    """
    import argparse
    import sys

    from app.pr_review import parse_pr_url as _parse_url

    parser = argparse.ArgumentParser(
        description="Rebase a GitHub PR onto its target branch."
    )
    parser.add_argument("url", help="GitHub PR URL")
    parser.add_argument(
        "--project-path", required=True,
        help="Local path to the project repository",
    )
    cli_args = parser.parse_args(argv)

    try:
        owner, repo, pr_number = _parse_url(cli_args.url)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    skill_dir = Path(__file__).resolve().parent.parent / "skills" / "core" / "rebase"

    success, summary = run_rebase(
        owner, repo, pr_number, cli_args.project_path,
        skill_dir=skill_dir,
    )
    print(summary)
    return 0 if success else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
