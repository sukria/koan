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
    /claude.md                          -> claudemd_refresh <project-path>

Scoped skills:
    /core.plan <idea>                   -> same as /plan
    /namespace.skill <args>             -> resolved via skill registry
"""

import os
import re
from typing import List, Optional, Tuple


# Mapping of skill command names to their CLI runner modules.
# Each entry: command_name -> (module_name, arg_builder_function_name)
_SKILL_RUNNERS = {
    "plan": "app.plan_runner",
    "rebase": "app.rebase_pr",
    "recreate": "app.recreate_pr",
    "ai": "app.ai_runner",
    "check": "app.check_runner",
    "claude.md": "app.claudemd_refresh",
    "claudemd": "app.claudemd_refresh",
    "claude": "app.claudemd_refresh",
}

# PR URL pattern
_PR_URL_RE = re.compile(
    r"https?://github\.com/[^/]+/[^/]+/pull/\d+"
)

# Issue URL pattern
_ISSUE_URL_RE = re.compile(
    r"https?://github\.com/[^/]+/[^/]+/issues/\d+"
)


def is_skill_mission(mission_text: str) -> bool:
    """Check if a mission starts with a /skill command.

    Args:
        mission_text: The mission title (without the "- [project:X]" prefix).

    Returns:
        True if the mission starts with /command.
    """
    stripped = mission_text.strip()
    return stripped.startswith("/") and len(stripped) > 1


def parse_skill_mission(mission_text: str) -> Tuple[str, str]:
    """Parse a skill mission into (command, args).

    Args:
        mission_text: e.g. "/plan Add dark mode" or "/core.plan Fix bug"

    Returns:
        (command, args) tuple. Command is normalized (no leading /).
        For scoped commands like "/core.plan", returns ("plan", args).
    """
    stripped = mission_text.strip()
    if not stripped.startswith("/"):
        return "", stripped

    # Split into command and args
    parts = stripped[1:].split(None, 1)
    raw_command = parts[0]
    args = parts[1] if len(parts) > 1 else ""

    # Handle scoped commands: /core.plan -> plan, /namespace.skill -> namespace.skill
    if "." in raw_command:
        segments = raw_command.split(".", 1)
        scope = segments[0]
        skill_name = segments[1]
        # core scope is implicit — /core.plan == /plan
        if scope == "core":
            return skill_name, args
        # Keep the full scoped name for external skills
        return raw_command, args

    return raw_command, args


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
    runner_module = _SKILL_RUNNERS.get(command)
    if not runner_module:
        return None

    python = os.path.join(koan_root, ".venv", "bin", "python3")
    base_cmd = [python, "-m", runner_module]

    if command == "plan":
        return _build_plan_cmd(base_cmd, args, project_path)
    elif command in ("rebase", "recreate"):
        return _build_pr_url_cmd(base_cmd, args, project_path)
    elif command == "ai":
        return _build_ai_cmd(base_cmd, project_name, project_path, instance_dir)
    elif command == "check":
        return _build_check_cmd(base_cmd, args, instance_dir, koan_root)
    elif command in ("claude.md", "claudemd", "claude"):
        return _build_claudemd_cmd(base_cmd, project_name, project_path)

    return None


def _build_plan_cmd(
    base_cmd: List[str], args: str, project_path: str,
) -> List[str]:
    """Build plan_runner command."""
    cmd = base_cmd + ["--project-path", project_path]

    # Detect issue URL vs free-text idea
    issue_match = _ISSUE_URL_RE.search(args)
    if issue_match:
        cmd.extend(["--issue-url", issue_match.group(0)])
    else:
        cmd.extend(["--idea", args])

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
    if not is_skill_mission(mission_text):
        return None

    command, args = parse_skill_mission(mission_text)
    if not command:
        return None

    return build_skill_command(
        command=command,
        args=args,
        project_name=project_name,
        project_path=project_path,
        koan_root=koan_root,
        instance_dir=instance_dir,
    )
