"""Kōan — Prompt builder for run.sh.

Replaces complex sed substitution and string concatenation in run.sh with a single
Python call. Handles agent prompt assembly (template + merge policy + deep research +
verbose mode) and contemplative prompt assembly.

Usage from run.sh:
    PROMPT=$("$PYTHON" -m app.prompt_builder agent \
        --instance "$INSTANCE" \
        --project-name "$PROJECT_NAME" \
        --project-path "$PROJECT_PATH" \
        --run-num "$RUN_NUM" \
        --max-runs "$MAX_RUNS" \
        --autonomous-mode "${AUTONOMOUS_MODE:-implement}" \
        --focus-area "${FOCUS_AREA:-General autonomous work}" \
        --available-pct "${AVAILABLE_PCT:-50}" \
        --mission-title "$MISSION_TITLE")

    CONTEMPLATE_PROMPT=$("$PYTHON" -m app.prompt_builder contemplative \
        --instance "$INSTANCE" \
        --project-name "$PROJECT_NAME" \
        --session-info "$SESSION_INFO")
"""

import argparse
import os
from pathlib import Path


def _load_config_safe() -> dict:
    """Load config.yaml, returning empty dict on failure."""
    try:
        from app.utils import load_config
        return load_config()
    except Exception:
        return {}


def _is_auto_merge_enabled(project_name: str) -> bool:
    """Check if auto-merge is enabled and has rules for the given project."""
    try:
        from app.utils import get_auto_merge_config
        config = _load_config_safe()
        merge_cfg = get_auto_merge_config(config, project_name)
        return bool(merge_cfg.get("enabled", True) and merge_cfg.get("rules"))
    except Exception:
        return False


def _get_branch_prefix() -> str:
    """Get the configured branch prefix."""
    try:
        from app.utils import get_branch_prefix
        return get_branch_prefix()
    except Exception:
        return "koan/"


def _get_merge_policy(project_name: str) -> str:
    """Return the merge policy section to append to the agent prompt."""
    from app.prompts import load_prompt

    prefix = _get_branch_prefix()
    if _is_auto_merge_enabled(project_name):
        return load_prompt("merge-policy-enabled", BRANCH_PREFIX=prefix)
    return load_prompt("merge-policy-disabled", BRANCH_PREFIX=prefix)


def _get_deep_research(instance: str, project_name: str, project_path: str) -> str:
    """Get deep research suggestions for DEEP mode."""
    try:
        from app.deep_research import DeepResearch
        research = DeepResearch(Path(instance), project_name, Path(project_path))
        suggestions = research.format_for_agent()
        if suggestions:
            return f"\n\n# Deep Research Analysis\n\n{suggestions}\n"
    except Exception:
        pass
    return ""


def _get_focus_section(instance: str) -> str:
    """Build the focus mode section if .koan-focus is active."""
    koan_root = str(Path(instance).parent)
    try:
        from app.focus_manager import check_focus
        state = check_focus(koan_root)
    except Exception:
        return ""

    if state is None:
        return ""

    from app.prompts import load_prompt

    remaining = state.remaining_display()
    return load_prompt("focus-mode", REMAINING=remaining)


def _get_verbose_section(instance: str) -> str:
    """Build the verbose mode section if .koan-verbose exists."""
    koan_root = str(Path(instance).parent)
    if not os.path.isfile(os.path.join(koan_root, ".koan-verbose")):
        return ""

    from app.prompts import load_prompt

    return load_prompt("verbose-mode", INSTANCE=instance)


def build_agent_prompt(
    instance: str,
    project_name: str,
    project_path: str,
    run_num: int,
    max_runs: int,
    autonomous_mode: str,
    focus_area: str,
    available_pct: int,
    mission_title: str = "",
) -> str:
    """Build the complete agent prompt from template + dynamic sections.

    Args:
        instance: Path to instance directory
        project_name: Current project name
        project_path: Path to project directory
        run_num: Current run number
        max_runs: Maximum runs per session
        autonomous_mode: Current mode (review/implement/deep)
        focus_area: Description of current focus
        available_pct: Budget percentage available
        mission_title: Mission title (empty for autonomous mode)

    Returns:
        Complete prompt string ready for Claude CLI
    """
    from app.prompts import load_prompt

    # Build mission instruction
    if mission_title:
        mission_instruction = (
            f"Your assigned mission is: **{mission_title}** "
            "Mark it In Progress in missions.md. Execute it thoroughly. "
            "Take your time — go deep, don't rush."
        )
    else:
        mission_instruction = (
            f"No specific mission assigned. Look for pending missions for "
            f"{project_name} in missions.md (check [project:{project_name}] "
            f"tags and ### project:{project_name} sub-headers). "
            "If none found, proceed to autonomous mode."
        )

    # Load template and substitute placeholders
    branch_prefix = _get_branch_prefix()
    prompt = load_prompt(
        "agent",
        INSTANCE=instance,
        PROJECT_PATH=project_path,
        PROJECT_NAME=project_name,
        RUN_NUM=str(run_num),
        MAX_RUNS=str(max_runs),
        AUTONOMOUS_MODE=autonomous_mode,
        FOCUS_AREA=focus_area,
        AVAILABLE_PCT=str(available_pct),
        MISSION_INSTRUCTION=mission_instruction,
        BRANCH_PREFIX=branch_prefix,
    )

    # Append merge policy
    prompt += _get_merge_policy(project_name)

    # Append deep research suggestions (DEEP mode, autonomous only)
    if autonomous_mode == "deep" and not mission_title:
        prompt += _get_deep_research(instance, project_name, project_path)

    # Append focus mode section if active
    prompt += _get_focus_section(instance)

    # Append verbose mode section if active
    prompt += _get_verbose_section(instance)

    return prompt


def build_contemplative_prompt(
    instance: str,
    project_name: str,
    session_info: str,
) -> str:
    """Build the contemplative session prompt from template.

    Args:
        instance: Path to instance directory
        project_name: Current project name
        session_info: Context about current session state

    Returns:
        Complete contemplative prompt string
    """
    from app.prompts import load_prompt

    return load_prompt(
        "contemplative",
        INSTANCE=instance,
        PROJECT_NAME=project_name,
        SESSION_INFO=session_info,
    )


def main():
    """CLI entry point for run.sh integration."""
    parser = argparse.ArgumentParser(description="Build prompts for Kōan agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Agent prompt subcommand
    agent_parser = subparsers.add_parser("agent", help="Build agent mission prompt")
    agent_parser.add_argument("--instance", required=True)
    agent_parser.add_argument("--project-name", required=True)
    agent_parser.add_argument("--project-path", required=True)
    agent_parser.add_argument("--run-num", type=int, required=True)
    agent_parser.add_argument("--max-runs", type=int, required=True)
    agent_parser.add_argument("--autonomous-mode", default="implement")
    agent_parser.add_argument("--focus-area", default="General autonomous work")
    agent_parser.add_argument("--available-pct", type=int, default=50)
    agent_parser.add_argument("--mission-title", default="")

    # Contemplative prompt subcommand
    contemplate_parser = subparsers.add_parser(
        "contemplative", help="Build contemplative session prompt"
    )
    contemplate_parser.add_argument("--instance", required=True)
    contemplate_parser.add_argument("--project-name", required=True)
    contemplate_parser.add_argument("--session-info", required=True)

    args = parser.parse_args()

    if args.command == "agent":
        print(build_agent_prompt(
            instance=args.instance,
            project_name=args.project_name,
            project_path=args.project_path,
            run_num=args.run_num,
            max_runs=args.max_runs,
            autonomous_mode=args.autonomous_mode,
            focus_area=args.focus_area,
            available_pct=args.available_pct,
            mission_title=args.mission_title,
        ))
    elif args.command == "contemplative":
        print(build_contemplative_prompt(
            instance=args.instance,
            project_name=args.project_name,
            session_info=args.session_info,
        ))


if __name__ == "__main__":
    main()
