"""Koan plan skill -- deep-think an idea, create or update a GitHub issue."""

import json
import os
import re
import subprocess
from pathlib import Path

from app.github import run_gh, issue_create, api


# GitHub issue URL pattern
_ISSUE_URL_RE = re.compile(
    r"https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/issues/(?P<number>\d+)"
)


def handle(ctx):
    """Handle /plan command.

    Modes:
        /plan                              -- usage help
        /plan <idea>                       -- plan for default project
        /plan <project> <idea>             -- plan for a specific project
        /plan <github-issue-url>           -- iterate on existing issue
    """
    args = ctx.args.strip()

    if not args:
        return (
            "Usage:\n"
            "  /plan <idea> -- plan for default project\n"
            "  /plan <project> <idea> -- plan for a specific project\n"
            "  /plan <github-issue-url> -- iterate on an existing issue\n\n"
            "Creates a structured plan with step-by-step implementation, "
            "corner cases, and open questions. Posts to GitHub as an issue."
        )

    # Mode 1: existing GitHub issue URL
    issue_match = _ISSUE_URL_RE.search(args)
    if issue_match:
        return _handle_existing_issue(ctx, issue_match)

    # Mode 2: detect project name prefix
    project, idea = _parse_project_arg(args)

    if not idea:
        return "Please provide an idea to plan. Ex: /plan Add dark mode to the dashboard"

    return _handle_new_plan(ctx, project, idea)


def _parse_project_arg(args):
    """Parse optional project prefix from args.

    Supports:
        /plan koan Fix the bug        -> ("koan", "Fix the bug")
        /plan [project:koan] Fix bug  -> ("koan", "Fix bug")
        /plan Fix the bug             -> (None, "Fix the bug")
    """
    from app.utils import parse_project, get_known_projects

    # Try [project:X] tag first
    project, cleaned = parse_project(args)
    if project:
        return project, cleaned

    # Try first word as project name
    parts = args.split(None, 1)
    if len(parts) < 2:
        return None, args

    candidate = parts[0].lower()
    known = get_known_projects()
    for name, _ in known:
        if name.lower() == candidate:
            return name, parts[1]

    return None, args


def _resolve_project_path(project_name, fallback=False):
    """Resolve project name to its local path.

    Args:
        project_name: Project name to look up (None = use default).
        fallback: If True, fall back to first project or env var when
                  no exact match is found (used for existing issue mode).
                  If False, return None on no match (used for new plan mode).
    """
    from app.utils import get_known_projects

    projects = get_known_projects()

    if project_name:
        for name, path in projects:
            if name.lower() == project_name.lower():
                return path
        # Try directory basename match
        for name, path in projects:
            if Path(path).name.lower() == project_name.lower():
                return path
        if not fallback:
            return None

    # Default to first project
    if projects:
        return projects[0][1]

    return os.environ.get("KOAN_PROJECT_PATH", "")


def _get_repo_info(project_path):
    """Get GitHub owner/repo from a local git repo."""
    try:
        output = run_gh("repo", "view", "--json", "owner,name",
                        cwd=project_path, timeout=15)
        data = json.loads(output)
        owner = data.get("owner", {}).get("login", "")
        repo = data.get("name", "")
        if owner and repo:
            return owner, repo
    except Exception:
        pass
    return None, None


def _fetch_issue_context(owner, repo, issue_number):
    """Fetch issue title, body and comments via gh CLI.

    Returns:
        Tuple of (title, body, comments_text).
        comments_text preserves authorship and timestamps for plan iteration.
    """
    # Get issue title and body
    issue_json = api(
        f"repos/{owner}/{repo}/issues/{issue_number}",
        jq='{"title": .title, "body": .body}',
    )
    try:
        data = json.loads(issue_json)
        title = data.get("title", "")
        body = data.get("body", "")
    except (json.JSONDecodeError, TypeError):
        title = ""
        body = issue_json

    # Get all comments with author and date context
    comments_json = api(
        f"repos/{owner}/{repo}/issues/{issue_number}/comments",
        jq='[.[] | {author: .user.login, date: .created_at, body: .body}]',
    )

    comments_text = _format_comments(comments_json)
    return title, body, comments_text


def _format_comments(comments_json):
    """Format comments JSON into readable text with authorship."""
    try:
        comments = json.loads(comments_json)
        if not isinstance(comments, list) or not comments:
            return ""
    except (json.JSONDecodeError, TypeError):
        # Fallback: return raw text if not valid JSON
        return comments_json.strip() if comments_json else ""

    parts = []
    for c in comments:
        author = c.get("author", "unknown")
        date = c.get("date", "")[:10]  # YYYY-MM-DD
        body = c.get("body", "").strip()
        if body:
            parts.append(f"**{author}** ({date}):\n{body}")
    return "\n\n---\n\n".join(parts)


def _generate_plan(project_path, idea, context=""):
    """Run Claude to generate a structured plan.

    Args:
        project_path: Path to project for codebase context.
        idea: The idea to plan.
        context: Optional existing issue/comments context.
    """
    from app.prompts import load_skill_prompt

    prompt = load_skill_prompt(Path(__file__).parent, "plan", IDEA=idea, CONTEXT=context)

    from app.cli_provider import build_full_command
    from app.utils import get_model_config

    models = get_model_config()
    cmd = build_full_command(
        prompt=prompt,
        allowed_tools=["Read", "Glob", "Grep", "WebFetch"],
        model=models.get("chat", ""),
        fallback=models.get("fallback", ""),
        max_turns=3,
    )

    result = subprocess.run(
        cmd,
        capture_output=True, text=True, timeout=300,
        cwd=project_path,
    )

    if result.returncode != 0:
        raise RuntimeError(f"Claude plan generation failed: {result.stderr[:300]}")

    return result.stdout.strip()


def _comment_on_issue(owner, repo, issue_number, body):
    """Post a comment on an existing GitHub issue."""
    api(
        f"repos/{owner}/{repo}/issues/{issue_number}/comments",
        input_data=body,
    )


def _extract_title(plan_text):
    """Extract a short title from the plan for the issue title."""
    lines = plan_text.strip().splitlines()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            # Use the first heading as title (strip # prefix)
            title = re.sub(r'^#+\s*', '', line).strip()
            if title:
                return title[:120]
        # Use first non-empty line
        clean = re.sub(r'^[#*>\-]+\s*', '', line).strip()
        if clean:
            return clean[:120]
    return "Implementation Plan"


def _handle_new_plan(ctx, project_name, idea):
    """Generate a plan for a new idea and create a GitHub issue."""
    send = ctx.send_message

    project_path = _resolve_project_path(project_name)
    if not project_path:
        from app.utils import get_known_projects
        known = ", ".join(n for n, _ in get_known_projects()) or "none"
        return f"Project '{project_name}' not found. Known: {known}"

    project_label = project_name or Path(project_path).name

    if send:
        send(f"\U0001f9e0 Planning: {idea[:100]}{'...' if len(idea) > 100 else ''} (project: {project_label})")

    try:
        plan = _generate_plan(project_path, idea)
    except Exception as e:
        return f"Plan generation failed: {str(e)[:300]}"

    if not plan:
        return "Claude returned an empty plan. Try rephrasing your idea."

    # Create GitHub issue
    owner, repo = _get_repo_info(project_path)
    if not owner or not repo:
        # No GitHub repo — return the plan as a message
        if send:
            send(f"Plan (no GitHub repo found, showing inline):\n\n{plan[:3500]}")
        return None

    title = _extract_title(plan)
    issue_body = f"## Plan: {idea}\n\n{plan}\n\n---\n*Generated by Koan /plan*"

    try:
        issue_url = issue_create(title, issue_body, cwd=project_path)
    except Exception as e:
        # Fallback: send plan inline if issue creation fails
        if send:
            send(f"\u26a0\ufe0f Plan ready but issue creation failed ({e}):\n\n{plan[:3000]}")
        return None

    if send:
        send(f"\u2705 Plan created: {issue_url}")
    return None


def _handle_existing_issue(ctx, match):
    """Read an existing issue + comments, generate updated plan, post comment."""
    send = ctx.send_message
    owner = match.group("owner")
    repo = match.group("repo")
    issue_number = match.group("number")

    if send:
        send(f"\U0001f4d6 Reading issue #{issue_number} ({owner}/{repo})...")

    # Resolve project path for codebase context (fallback=True: best-effort match)
    project_path = _resolve_project_path(repo, fallback=True)

    try:
        title, body, comments = _fetch_issue_context(owner, repo, issue_number)
    except Exception as e:
        return f"Failed to fetch issue: {str(e)[:300]}"

    # Build context from issue body + comments
    context_parts = [f"## Original Issue #{issue_number}: {title}\n\n{body}"]
    if comments:
        context_parts.append(f"\n\n## Comments\n\n{comments}")

    context = "\n".join(context_parts)

    # Extract the core idea from the issue body
    idea = _extract_idea_from_issue(body)

    try:
        plan = _generate_plan(
            project_path or str(Path.cwd()),
            idea,
            context=context,
        )
    except Exception as e:
        return f"Plan generation failed: {str(e)[:300]}"

    if not plan:
        return "Claude returned an empty plan. The issue may need more context."

    # Post as a comment on the issue
    comment_body = f"## Updated Plan\n\n{plan}\n\n---\n*Generated by Koan /plan — iteration on existing issue*"

    try:
        _comment_on_issue(owner, repo, issue_number, comment_body)
    except Exception as e:
        # Fallback: send inline
        if send:
            send(f"Plan ready but comment failed ({e}):\n\n{plan[:3000]}")
        return None

    issue_label = f"#{issue_number}"
    if title:
        issue_label = f"#{issue_number} ({title[:60]})"
    if send:
        send(f"\u2705 Plan posted as comment on {issue_label}: https://github.com/{owner}/{repo}/issues/{issue_number}")
    return None


def _extract_idea_from_issue(body):
    """Extract the core idea from an issue body for re-planning."""
    if not body:
        return "Review and update this plan"
    # Use the first non-empty paragraph as the idea
    lines = body.strip().splitlines()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Skip markdown headers/metadata
        if line.startswith("---") or line.startswith("*Generated by"):
            continue
        # Strip markdown header prefix
        clean = re.sub(r'^#+\s*', '', line).strip()
        # Skip "Plan:" prefix if present
        clean = re.sub(r'^Plan:\s*', '', clean).strip()
        if clean and len(clean) > 3:
            return clean[:500]
    return "Review and refine this plan based on the discussion"
