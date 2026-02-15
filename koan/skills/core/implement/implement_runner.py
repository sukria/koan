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

import re
from pathlib import Path
from typing import List, Optional, Tuple

from app.github import fetch_issue_with_comments
from app.github_url_parser import parse_issue_url


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
        )
    except Exception as e:
        return False, f"Implementation failed: {str(e)[:300]}"

    if not output:
        return False, "Claude returned empty output."

    notify_fn(
        f"\u2705 Implementation complete for issue #{issue_number}"
        f"{context_label}"
    )
    return True, f"Implementation complete for #{issue_number}{context_label}"


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
) -> str:
    """Build the implementation prompt from the issue and plan.
    
    Args:
        issue_url: GitHub issue URL.
        issue_title: Issue title.
        plan: Extracted plan text.
        context: Additional user context.
        skill_dir: Path to skill directory for prompt loading.
        
    Returns:
        Formatted prompt string.
    """
    if skill_dir is not None:
        from app.prompts import load_skill_prompt
        return load_skill_prompt(
            skill_dir, "implement",
            ISSUE_URL=issue_url,
            ISSUE_TITLE=issue_title,
            PLAN=plan,
            CONTEXT=context,
        )

    from app.prompts import load_prompt
    return load_prompt(
        "implement",
        ISSUE_URL=issue_url,
        ISSUE_TITLE=issue_title,
        PLAN=plan,
        CONTEXT=context,
    )


def _execute_implementation(
    project_path: str,
    issue_url: str,
    issue_title: str,
    plan: str,
    context: str,
    skill_dir: Optional[Path] = None,
) -> str:
    """Execute the implementation via Claude CLI.
    
    Args:
        project_path: Path to the project repository.
        issue_url: GitHub issue URL.
        issue_title: Issue title.
        plan: Extracted plan text.
        context: Additional user context.
        skill_dir: Path to skill directory for prompt loading.
        
    Returns:
        Claude CLI output.
    """
    prompt = _build_prompt(issue_url, issue_title, plan, context, skill_dir)
    
    from app.cli_provider import CLAUDE_TOOLS, run_command
    return run_command(
        prompt, project_path,
        allowed_tools=sorted(CLAUDE_TOOLS),
        max_turns=50, timeout=900,
    )


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
