"""Koan -- Skill mission dispatch.

Detects skill-prefixed missions (e.g. "/plan Add dark mode") and builds
the corresponding CLI command for direct execution.

This replaces the verbose "run: `cd ... && python3 -m app.runner ...`"
pattern embedded in missions.md with a clean, human-readable format.

Mission format:
    /plan <idea>                        -> plan_runner --idea <idea>
    /plan <github-issue-url>            -> plan_runner --issue-url <url>
    /rebase <pr-url>                    -> rebase_pr <url>
    /recreate <pr-url>                  -> recreate_pr <url>
    /ai [project]                       -> ai_runner
    /check <url>                        -> check_runner <url>
    /claudemd                           -> claudemd_refresh <project-path>

Scoped skills:
    /core.plan <idea>                   -> same as /plan
    /namespace.skill <args>             -> resolved via skill registry
"""

import os
import re
from typing import List, Optional, Tuple

from app.github_url_parser import ISSUE_URL_PATTERN, PR_URL_PATTERN


# Mapping of skill command names to their CLI runner modules.
# Each entry: command_name -> (module_name, arg_builder_function_name)
_SKILL_RUNNERS = {
    "plan": "app.plan_runner",
    "implement": "skills.core.implement.implement_runner",
    "rebase": "app.rebase_pr",
    "recreate": "app.recreate_pr",
    "ai": "app.ai_runner",
    "check": "app.check_runner",
    "claudemd": "app.claudemd_refresh",
    "claude": "app.claudemd_refresh",
    "claude.md": "app.claudemd_refresh",
    "claude_md": "app.claudemd_refresh",
}

_PROJECT_TAG_RE = re.compile(r"^\[projec?t:([a-zA-Z0-9_-]+)\]\s*")
_PROJECT_WORD_RE = re.compile(r"^[a-z][a-z0-9_-]*$")

# Compiled patterns for URL matching
_PR_URL_RE = re.compile(PR_URL_PATTERN)
_ISSUE_URL_RE = re.compile(ISSUE_URL_PATTERN)


def _is_known_project(name: str) -> bool:
    """Check if a name matches a known project (case-insensitive)."""
    try:
        from app.utils import get_known_projects
        lower = name.lower()
        return any(n.lower() == lower for n, _ in get_known_projects())
    except Exception as e:
        from app.debug import debug_log
        debug_log(f"[skill_dispatch] _is_known_project: error loading projects: {e}")
        return False


def _strip_project_prefix(text: str) -> Tuple[str, str]:
    """Strip an optional project prefix from mission text.

    Handles two forms:
        - ``[project:koan] /plan Add dark mode``  -> ("koan", "/plan Add dark mode")
        - ``koan /plan Add dark mode``             -> ("koan", "/plan Add dark mode")
        - ``/plan Add dark mode``                  -> ("",     "/plan Add dark mode")

    Returns (project_id, remaining_text).
    """
    stripped = text.strip()

    # 1. [project:X] tag prefix
    tag_match = _PROJECT_TAG_RE.match(stripped)
    if tag_match:
        return tag_match.group(1), stripped[tag_match.end():].strip()

    # 2. Raw word prefix: "koan /plan ..."
    # Only accept known project names to avoid matching common English
    # words (e.g. "the /keyword ..." was incorrectly parsed as project="the").
    parts = stripped.split(None, 1)
    if (len(parts) >= 2
            and not parts[0].startswith("/")
            and parts[1].startswith("/")
            and _PROJECT_WORD_RE.match(parts[0])):
        candidate = parts[0]
        if _is_known_project(candidate):
            return candidate, parts[1]

    # 3. No prefix
    return "", stripped


def is_skill_mission(mission_text: str) -> bool:
    """Check if a mission contains a /skill command.

    Handles optional project-id prefixes:
        - ``/plan Add dark mode``                  (plain)
        - ``[project:koan] /plan Add dark mode``   (tag prefix)
        - ``koan /plan Add dark mode``             (word prefix)

    Args:
        mission_text: The mission title text.

    Returns:
        True if the mission contains a /command.
    """
    _, remainder = _strip_project_prefix(mission_text)
    return remainder.startswith("/") and len(remainder) > 1


def parse_skill_mission(mission_text: str) -> Tuple[str, str, str]:
    """Parse a skill mission into (project_id, command, args).

    Handles optional project-id prefixes:
        - ``/plan Add dark mode``                  -> ("", "plan", "Add dark mode")
        - ``[project:koan] /plan Add dark mode``   -> ("koan", "plan", "Add dark mode")
        - ``koan /plan Add dark mode``             -> ("koan", "plan", "Add dark mode")

    Args:
        mission_text: e.g. "/plan Add dark mode" or "[project:koan] /core.plan Fix bug"

    Returns:
        (project_id, command, args) tuple. project_id is "" when no prefix.
        Command is normalized (no leading /).
        For scoped commands like "/core.plan", returns ("", "plan", args).
    """
    project_id, remainder = _strip_project_prefix(mission_text)

    if not remainder.startswith("/"):
        return project_id, "", remainder

    # Split into command and args
    parts = remainder[1:].split(None, 1)
    raw_command = parts[0]
    args = parts[1] if len(parts) > 1 else ""

    # Handle scoped commands: /core.plan -> plan, /namespace.skill -> namespace.skill
    if "." in raw_command:
        segments = raw_command.split(".", 1)
        scope = segments[0]
        skill_name = segments[1]
        # core scope is implicit — /core.plan == /plan
        if scope == "core":
            return project_id, skill_name, args
        # Keep the full scoped name for external skills
        return project_id, raw_command, args

    return project_id, raw_command, args


def build_skill_command(
    command: str,
    args: str,
    project_name: str,
    project_path: str,
    koan_root: str,
    instance_dir: str,
) -> Optional[List[str]]:
    """Build the CLI command list for a skill mission.

    Args:
        command: Skill command name (e.g. "plan", "rebase").
        args: Arguments string from the mission.
        project_name: Current project name.
        project_path: Path to the project directory.
        koan_root: Path to koan root directory.
        instance_dir: Path to instance directory.

    Returns:
        Command list ready for subprocess, or None if the skill
        is not recognized as a direct-dispatch skill.
    """
    from app.debug import debug_log

    runner_module = _SKILL_RUNNERS.get(command)
    if not runner_module:
        debug_log(
            f"[skill_dispatch] build_skill_command: no runner for '{command}' "
            f"(known: {', '.join(sorted(_SKILL_RUNNERS))})"
        )
        return None
    debug_log(f"[skill_dispatch] build_skill_command: '{command}' -> {runner_module}")

    python = os.path.join(koan_root, ".venv", "bin", "python3")
    base_cmd = [python, "-m", runner_module]

    # Dispatch to command-specific builder
    _COMMAND_BUILDERS = {
        "plan": lambda: _build_plan_cmd(base_cmd, args, project_path),
        "implement": lambda: _build_implement_cmd(base_cmd, args, project_path),
        "rebase": lambda: _build_pr_url_cmd(base_cmd, args, project_path),
        "recreate": lambda: _build_pr_url_cmd(base_cmd, args, project_path),
        "ai": lambda: _build_ai_cmd(base_cmd, project_name, project_path, instance_dir),
        "check": lambda: _build_check_cmd(base_cmd, args, instance_dir, koan_root),
        "claudemd": lambda: _build_claudemd_cmd(base_cmd, project_name, project_path),
        "claude": lambda: _build_claudemd_cmd(base_cmd, project_name, project_path),
        "claude.md": lambda: _build_claudemd_cmd(base_cmd, project_name, project_path),
        "claude_md": lambda: _build_claudemd_cmd(base_cmd, project_name, project_path),
    }

    builder = _COMMAND_BUILDERS.get(command)
    return builder() if builder else None


def _extract_issue_url_and_context(args: str) -> Optional[Tuple[str, str]]:
    """Extract issue URL and remaining context from arguments.
    
    Args:
        args: Argument string potentially containing an issue URL.
        
    Returns:
        Tuple of (issue_url, context) or None if no URL found.
        Context is the text after the URL, stripped.
    """
    issue_match = _ISSUE_URL_RE.search(args)
    if not issue_match:
        return None
    
    issue_url = issue_match.group(0)
    context = args[issue_match.end():].strip()
    return issue_url, context


def _build_plan_cmd(
    base_cmd: List[str], args: str, project_path: str,
) -> List[str]:
    """Build plan_runner command."""
    cmd = base_cmd + ["--project-path", project_path]

    # Detect issue URL vs free-text idea
    url_and_context = _extract_issue_url_and_context(args)
    if url_and_context:
        issue_url, _ = url_and_context
        cmd.extend(["--issue-url", issue_url])
    else:
        cmd.extend(["--idea", args])

    return cmd


def _build_implement_cmd(
    base_cmd: List[str], args: str, project_path: str,
) -> Optional[List[str]]:
    """Build implement_runner command.

    Expects an issue URL and optional context text after it.
    Example args: "https://github.com/o/r/issues/42 Phase 1 to 3"
    """
    url_and_context = _extract_issue_url_and_context(args)
    if not url_and_context:
        return None
    
    issue_url, context = url_and_context
    cmd = base_cmd + [
        "--project-path", project_path,
        "--issue-url", issue_url,
    ]

    if context:
        cmd.extend(["--context", context])

    return cmd


def _build_pr_url_cmd(
    base_cmd: List[str], args: str, project_path: str,
) -> Optional[List[str]]:
    """Build command for PR-URL-based skills (rebase, recreate)."""
    url_match = _PR_URL_RE.search(args)
    if not url_match:
        return None
    return base_cmd + [url_match.group(0), "--project-path", project_path]


def _build_ai_cmd(
    base_cmd: List[str],
    project_name: str,
    project_path: str,
    instance_dir: str,
) -> List[str]:
    """Build ai_runner command."""
    return base_cmd + [
        "--project-path", project_path,
        "--project-name", project_name,
        "--instance-dir", instance_dir,
    ]


def _build_check_cmd(
    base_cmd: List[str],
    args: str,
    instance_dir: str,
    koan_root: str,
) -> Optional[List[str]]:
    """Build check_runner command."""
    # Extract URL from args
    url_match = _PR_URL_RE.search(args) or _ISSUE_URL_RE.search(args)
    if not url_match:
        return None
    return base_cmd + [
        url_match.group(0),
        "--instance-dir", instance_dir,
        "--koan-root", koan_root,
    ]


def _build_claudemd_cmd(
    base_cmd: List[str],
    project_name: str,
    project_path: str,
) -> List[str]:
    """Build claudemd_refresh command."""
    return base_cmd + [
        project_path,
        "--project-name", project_name,
    ]


def dispatch_skill_mission(
    mission_text: str,
    project_name: str,
    project_path: str,
    koan_root: str,
    instance_dir: str,
) -> Optional[List[str]]:
    """High-level entry point: parse + build command for a skill mission.

    Args:
        mission_text: The mission title (e.g. "/plan Add dark mode").
        project_name: Current project name.
        project_path: Path to the project directory.
        koan_root: Path to koan root.
        instance_dir: Path to instance directory.

    Returns:
        Command list ready for subprocess, or None if not a skill mission
        or the skill is not recognized.
    """
    from app.debug import debug_log

    preview = f"{mission_text[:100]}..." if len(mission_text) > 100 else mission_text
    debug_log(f"[skill_dispatch] dispatch: mission_text='{preview}'")

    if not is_skill_mission(mission_text):
        debug_log("[skill_dispatch] dispatch: regular mission (no /command prefix), proceeding to Claude")
        return None

    parsed_project, command, args = parse_skill_mission(mission_text)
    debug_log(
        f"[skill_dispatch] dispatch: parsed project='{parsed_project}' "
        f"command='{command}' args='{args[:80]}'"
    )
    if not command:
        return None

    # Use parsed project-id as fallback when caller's project_name is empty
    effective_project = project_name or parsed_project

    result = build_skill_command(
        command=command,
        args=args,
        project_name=effective_project,
        project_path=project_path,
        koan_root=koan_root,
        instance_dir=instance_dir,
    )
    if result:
        debug_log(f"[skill_dispatch] dispatch: built command: {' '.join(result[:5])}")
    else:
        debug_log("[skill_dispatch] dispatch: build_skill_command returned None")
    return result
