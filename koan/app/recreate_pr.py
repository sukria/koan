"""
Koan -- Pull Request recreation workflow.

Recreates a PR from scratch when the original branch has diverged too far
from the target for a clean rebase.

Pipeline:
1. Fetch PR metadata + diff + comments from GitHub (understand the intent)
2. Create a fresh branch from upstream target
3. Invoke Claude to reimplement the feature (inspired by the original PR)
4. Run tests to verify
5. Push the result (force-push to original branch, or create new draft PR)
6. Comment on the original PR with cross-link
"""

import re
from pathlib import Path
from typing import List, Optional, Tuple

from app.claude_step import _run_git, run_claude_step
from app.github import run_gh
from app.rebase_pr import (
    _get_current_branch,
    _is_permission_error,
    _safe_checkout,
    build_comment_summary,
    fetch_pr_context,
)


def run_recreate(
    owner: str,
    repo: str,
    pr_number: str,
    project_path: str,
    notify_fn=None,
    skill_dir: Optional[Path] = None,
) -> Tuple[bool, str]:
    """Execute the recreation pipeline for a pull request.

    Unlike run_rebase which preserves the branch history, this creates
    a fresh branch from the upstream target and reimplements the feature
    from scratch, using the original PR as inspiration.

    Args:
        owner: GitHub owner (e.g., "sukria")
        repo: GitHub repo name (e.g., "koan")
        pr_number: PR number as string
        project_path: Local path to the project
        notify_fn: Optional callback for progress notifications.
        skill_dir: Path to the recreate skill directory for prompt resolution.

    Returns:
        (success, summary) tuple.
    """
    if notify_fn is None:
        from app.notify import send_telegram
        notify_fn = send_telegram

    full_repo = f"{owner}/{repo}"
    actions_log: List[str] = []

    # -- Step 1: Fetch PR context ------------------------------------------------
    notify_fn(f"Reading PR #{pr_number} to understand original intent...")
    try:
        context = fetch_pr_context(owner, repo, pr_number)
    except Exception as e:
        return False, f"Failed to fetch PR context: {e}"

    if not context["branch"]:
        return False, "Could not determine PR branch name."

    branch = context["branch"]
    base = context["base"]
    actions_log.append(f"Read PR #{pr_number}: \"{context['title']}\"")

    # Log comment summary for awareness
    comment_summary = build_comment_summary(context)
    if comment_summary and "No comments" not in comment_summary:
        actions_log.append("Read PR comments and review feedback")

    # -- Step 2: Create fresh branch from upstream target -----------------------
    notify_fn(f"Creating fresh branch from upstream `{base}`...")

    original_branch = _get_current_branch(project_path)

    # Fetch latest upstream target
    upstream_remote = _fetch_upstream_target(base, project_path)
    if not upstream_remote:
        return False, f"Could not fetch `{base}` from origin or upstream."

    # Create a fresh working branch from the upstream target
    work_branch = branch  # We'll try to reuse the original branch name
    try:
        # Delete local branch if it exists (we're recreating from scratch)
        try:
            _run_git(["git", "branch", "-D", work_branch], cwd=project_path)
        except Exception:
            pass  # Branch doesn't exist locally, that's fine

        _run_git(
            ["git", "checkout", "-b", work_branch, f"{upstream_remote}/{base}"],
            cwd=project_path,
        )
        actions_log.append(f"Created fresh branch `{work_branch}` from `{upstream_remote}/{base}`")
    except Exception as e:
        _safe_checkout(original_branch, project_path)
        return False, f"Failed to create fresh branch: {e}"

    # -- Step 3: Reimplement the feature via Claude ----------------------------
    notify_fn(f"Reimplementing feature from PR #{pr_number}...")

    _reimpl_feature(
        context, pr_number, project_path, actions_log,
        skill_dir=skill_dir,
    )

    # Verify something was actually implemented
    has_changes = _has_commits_on_branch(work_branch, base, upstream_remote, project_path)
    if not has_changes:
        _safe_checkout(original_branch, project_path)
        return False, "Recreation produced no changes. The feature may need manual implementation."

    # -- Step 4: Run tests ----------------------------------------------------
    notify_fn("Running tests...")
    test_result = _run_tests(project_path)
    if test_result:
        actions_log.append(test_result)

    # -- Step 5: Push the result -----------------------------------------------
    notify_fn(f"Pushing `{work_branch}`...")
    push_result = _push_recreated(
        work_branch, base, full_repo, pr_number, context, project_path
    )
    actions_log.extend(push_result["actions"])

    if not push_result["success"]:
        _safe_checkout(original_branch, project_path)
        return False, (
            f"Push failed: {push_result.get('error', 'unknown')}\n\n"
            "Actions completed:\n" +
            "\n".join(f"- {a}" for a in actions_log)
        )

    # -- Step 6: Comment on the original PR ------------------------------------
    comment_body = _build_recreate_comment(
        pr_number, work_branch, base, actions_log, context,
        new_pr_url=push_result.get("new_pr_url"),
    )

    try:
        run_gh(
            "pr", "comment", pr_number,
            "--repo", full_repo,
            "--body", comment_body,
        )
        actions_log.append("Commented on original PR")
    except Exception as e:
        # Non-fatal
        actions_log.append(f"Comment failed (non-fatal): {str(e)[:100]}")

    # Restore original branch
    _safe_checkout(original_branch, project_path)

    summary = f"PR #{pr_number} recreated.\n" + "\n".join(
        f"- {a}" for a in actions_log
    )
    return True, summary


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fetch_upstream_target(base: str, project_path: str) -> Optional[str]:
    """Fetch the target branch from origin or upstream.

    Returns the remote name used, or None on failure.
    """
    for remote in ("origin", "upstream"):
        try:
            _run_git(["git", "fetch", remote, base], cwd=project_path)
            return remote
        except Exception:
            continue
    return None


def _build_recreate_prompt(context: dict, skill_dir: Optional[Path] = None) -> str:
    """Build a prompt for Claude to reimplement the feature from scratch."""
    if skill_dir is not None:
        from app.prompts import load_skill_prompt
        return load_skill_prompt(
            skill_dir, "recreate",
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
        "recreate",
        TITLE=context["title"],
        BODY=context.get("body", ""),
        BRANCH=context["branch"],
        BASE=context["base"],
        DIFF=context.get("diff", ""),
        REVIEW_COMMENTS=context.get("review_comments", ""),
        REVIEWS=context.get("reviews", ""),
        ISSUE_COMMENTS=context.get("issue_comments", ""),
    )


def _reimpl_feature(
    context: dict,
    pr_number: str,
    project_path: str,
    actions_log: List[str],
    skill_dir: Optional[Path] = None,
) -> None:
    """Reimplement the feature via Claude, inspired by the original PR."""
    prompt = _build_recreate_prompt(context, skill_dir=skill_dir)
    run_claude_step(
        prompt=prompt,
        project_path=project_path,
        commit_msg=f"feat: recreate PR #{pr_number} — {context.get('title', 'reimplementation')}",
        success_label="Reimplemented feature from scratch",
        failure_label="Feature reimplementation step failed",
        actions_log=actions_log,
        max_turns=30,
        timeout=900,
    )


def _has_commits_on_branch(
    branch: str, base: str, remote: str, project_path: str
) -> bool:
    """Check if the branch has commits beyond the upstream target."""
    try:
        log = _run_git(
            ["git", "log", f"{remote}/{base}..{branch}", "--oneline"],
            cwd=project_path,
        )
        return bool(log.strip())
    except Exception:
        return False


def _run_tests(project_path: str) -> Optional[str]:
    """Run the project test suite, return summary or None."""
    import subprocess
    try:
        result = subprocess.run(
            ["make", "test"],
            capture_output=True, text=True,
            timeout=300, cwd=project_path,
        )
        if result.returncode == 0:
            # Extract test count from output
            output = result.stdout + result.stderr
            passed_match = re.search(r'(\d+)\s+passed', output)
            if passed_match:
                return f"Tests pass ({passed_match.group(1)} passed)"
            return "Tests pass"
        else:
            # Extract failure info
            output = result.stdout + result.stderr
            failed_match = re.search(r'(\d+)\s+failed', output)
            if failed_match:
                return f"Tests: {failed_match.group(1)} failures (non-blocking)"
            return "Tests: some failures (non-blocking)"
    except subprocess.TimeoutExpired:
        return "Tests: timeout (non-blocking)"
    except FileNotFoundError:
        return None  # No Makefile or make not available


def _push_recreated(
    branch: str,
    base: str,
    full_repo: str,
    pr_number: str,
    context: dict,
    project_path: str,
) -> dict:
    """Push recreated branch, falling back to new draft PR if permission denied.

    Returns:
        dict with keys: success (bool), actions (list), error (str),
        new_pr_url (optional str).
    """
    actions = []

    # Option 1: Try force-pushing to the existing branch
    try:
        _run_git(
            ["git", "push", "origin", branch, "--force-with-lease"],
            cwd=project_path,
        )
        actions.append(f"Force-pushed `{branch}` (recreated from scratch)")
        return {"success": True, "actions": actions, "error": ""}
    except Exception as push_error:
        error_msg = str(push_error)

    # Option 2: Permission denied -- create a new draft PR
    if not _is_permission_error(error_msg):
        return {
            "success": False,
            "actions": actions,
            "error": error_msg,
        }

    # Create new branch and draft PR
    new_branch = f"koan/recreate-{branch.replace('/', '-')}"
    try:
        _run_git(
            ["git", "checkout", "-b", new_branch],
            cwd=project_path,
        )
        _run_git(
            ["git", "push", "-u", "origin", new_branch],
            cwd=project_path,
        )
        actions.append(
            f"Created new branch `{new_branch}` (no push permission on `{branch}`)"
        )

        # Create draft PR
        title = context.get("title", f"Recreate of #{pr_number}")
        new_pr_body = (
            f"Supersedes #{pr_number}.\n\n"
            f"This PR contains a fresh reimplementation of the original feature, "
            f"built on top of current `{base}`.\n\n"
            f"The original branch had diverged too far for a clean rebase, so the "
            f"feature was recreated from scratch based on the original PR's intent.\n\n"
            f"Original PR: {context.get('url', f'#{pr_number}')}\n\n"
            f"---\n_Automated by Koan_"
        )
        new_pr_url = run_gh(
            "pr", "create",
            "--repo", full_repo,
            "--head", new_branch,
            "--base", base,
            "--title", f"[Recreate] {title}",
            "--body", new_pr_body,
            "--draft",
        )
        actions.append(f"Created draft PR: {new_pr_url.strip()}")

        # Cross-link on the original PR
        new_pr_match = re.search(r'/pull/(\d+)', new_pr_url)
        new_pr_ref = new_pr_match.group(0) if new_pr_match else new_pr_url.strip()

        try:
            run_gh(
                "pr", "comment", pr_number,
                "--repo", full_repo,
                "--body",
                f"This PR has been recreated from scratch and superseded by {new_pr_ref}.\n\n"
                f"The original branch had diverged too far for a clean rebase. "
                f"The new PR contains a fresh reimplementation on current `{base}`.\n\n"
                f"---\n_Automated by Koan_",
            )
            actions.append("Cross-linked original PR")
        except Exception:
            pass

        return {
            "success": True,
            "actions": actions,
            "error": "",
            "new_pr_url": new_pr_url.strip(),
        }

    except Exception as e:
        return {
            "success": False,
            "actions": actions,
            "error": f"Failed to create fallback PR: {e}",
        }


def _build_recreate_comment(
    pr_number: str,
    branch: str,
    base: str,
    actions_log: List[str],
    context: dict,
    new_pr_url: Optional[str] = None,
) -> str:
    """Build a markdown comment summarizing the recreation."""
    title = context.get("title", f"PR #{pr_number}")

    actions_md = "\n".join(
        f"- {a}" for a in actions_log
    ) if actions_log else "- No changes needed"

    comment = (
        f"## Recreated: {title}\n\n"
        f"The original branch `{branch}` had diverged too far from `{base}` "
        f"for a clean rebase. The feature has been **reimplemented from scratch** "
        f"on top of current `{base}`.\n\n"
    )

    if new_pr_url:
        comment += f"New PR: {new_pr_url}\n\n"
    else:
        comment += f"Branch `{branch}` has been force-pushed with the recreation.\n\n"

    comment += (
        f"### Actions\n\n"
        f"{actions_md}\n\n"
        f"---\n"
        f"_Automated by Koan_"
    )
    return comment


# ---------------------------------------------------------------------------
# CLI entry point -- python3 -m app.recreate_pr <url> --project-path <path>
# ---------------------------------------------------------------------------

def main(argv=None):
    """CLI entry point for recreate_pr.

    Returns exit code (0 = success, 1 = failure).
    """
    import argparse
    import sys

    from app.pr_review import parse_pr_url as _parse_url

    parser = argparse.ArgumentParser(
        description="Recreate a GitHub PR from scratch on current upstream."
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

    skill_dir = Path(__file__).resolve().parent.parent / "skills" / "core" / "recreate"

    success, summary = run_recreate(
        owner, repo, pr_number, cli_args.project_path,
        skill_dir=skill_dir,
    )
    print(summary)
    return 0 if success else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
