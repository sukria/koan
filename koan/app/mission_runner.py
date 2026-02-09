"""
Kōan -- Mission execution pipeline.

Handles the full lifecycle of a single mission run:
1. Build the Claude CLI command (prompt, tools, flags)
2. Parse Claude JSON output (extract text from various response shapes)
3. Post-mission processing (usage tracking, pending.md archival, reflection,
   auto-merge)

CLI interface:
    python -m app.mission_runner build-command \\
        --instance ... --autonomous-mode ... [--mission-title ...]
    python -m app.mission_runner parse-output <json_file>
    python -m app.mission_runner post-mission \\
        --instance ... --project-name ... --project-path ... \\
        --run-num N --max-runs N --exit-code N \\
        --stdout-file ... --stderr-file ... \\
        [--mission-title ...] [--autonomous-mode ...] [--start-time N]
"""

import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional


def build_mission_command(
    prompt: str,
    autonomous_mode: str = "implement",
    extra_flags: str = "",
    project_name: str = "",
) -> List[str]:
    """Build the CLI command for mission execution (provider-agnostic).

    Args:
        prompt: The full agent prompt text.
        autonomous_mode: Current mode (review/implement/deep).
        extra_flags: Space-separated extra CLI flags from config.
        project_name: Optional project name for per-project tool overrides.

    Returns:
        Complete command list ready for subprocess.
    """
    from app.config import get_mission_tools, get_model_config
    from app.cli_provider import build_full_command

    # Get mission tools (comma-separated list)
    tools_str = get_mission_tools(project_name)
    tools_list = [t.strip() for t in tools_str.split(",") if t.strip()]

    # Get model configuration with per-project overrides
    models = get_model_config(project_name)
    model = models["mission"]
    if autonomous_mode == "review" and models["review_mode"]:
        model = models["review_mode"]
    fallback = models["fallback"]

    # Build provider-specific command
    cmd = build_full_command(
        prompt=prompt,
        allowed_tools=tools_list,
        model=model,
        fallback=fallback,
        output_format="json",
    )

    # Append any extra flags from config
    if extra_flags.strip():
        cmd.extend(extra_flags.strip().split())

    return cmd


def get_mission_flags(autonomous_mode: str = "", project_name: str = "") -> str:
    """Get CLI flags for mission role from config.

    Args:
        autonomous_mode: Current mode (review/implement/deep).
        project_name: Optional project name for per-project model overrides.

    Returns:
        Space-separated CLI flags string (may be empty).
    """
    from app.config import get_claude_flags_for_role

    return get_claude_flags_for_role("mission", autonomous_mode, project_name)


def parse_claude_output(raw_text: str) -> str:
    """Extract human-readable text from Claude JSON output.

    Handles multiple JSON response shapes:
    - {"result": "..."}
    - {"content": "..."}
    - {"text": "..."}
    Falls back to raw text if JSON parsing fails.

    Args:
        raw_text: Raw stdout from Claude CLI (JSON or plain text).

    Returns:
        Extracted text content.
    """
    if not raw_text.strip():
        return ""

    try:
        data = json.loads(raw_text)
        # Try common response keys in order
        for key in ("result", "content", "text"):
            if key in data and isinstance(data[key], str):
                return data[key]
        # If none match, return the raw text
        return raw_text.strip()
    except (json.JSONDecodeError, TypeError):
        return raw_text.strip()


def archive_pending(instance_dir: str, project_name: str, run_num: int) -> bool:
    """Archive pending.md to daily journal if agent didn't clean it up.

    Args:
        instance_dir: Path to instance directory.
        project_name: Current project name.
        run_num: Current run number.

    Returns:
        True if pending.md was archived, False if it didn't exist.
    """
    pending_path = Path(instance_dir) / "journal" / "pending.md"
    if not pending_path.exists():
        return False

    journal_dir = Path(instance_dir) / "journal" / date.today().strftime("%Y-%m-%d")
    journal_dir.mkdir(parents=True, exist_ok=True)
    journal_file = journal_dir / f"{project_name}.md"

    # Append pending content to daily journal
    pending_content = pending_path.read_text()
    now = datetime.now().strftime("%H:%M")
    entry = f"\n## Run {run_num} — {now} (auto-archived from pending)\n\n{pending_content}"

    with open(journal_file, "a") as f:
        f.write(entry)

    pending_path.unlink()
    return True


def update_usage(stdout_file: str, usage_state: str, usage_md: str) -> bool:
    """Update token usage state from Claude JSON output.

    Args:
        stdout_file: Path to Claude stdout capture file.
        usage_state: Path to usage_state.json.
        usage_md: Path to usage.md.

    Returns:
        True if update succeeded.
    """
    try:
        from app.usage_estimator import cmd_update

        cmd_update(Path(stdout_file), Path(usage_state), Path(usage_md))
        return True
    except Exception:
        return False


def trigger_reflection(
    instance_dir: str,
    mission_title: str,
    duration_minutes: int,
    project_name: str = "",
) -> bool:
    """Trigger post-mission reflection if the mission was significant.

    Reads today's journal file for the project to provide context to the
    reflection prompt. The dual heuristic (keyword + substantial journal)
    prevents noise from trivial missions.

    Args:
        instance_dir: Path to instance directory.
        mission_title: Mission description text.
        duration_minutes: Duration in minutes.
        project_name: Current project name (for journal file lookup).

    Returns:
        True if reflection was generated.
    """
    try:
        from app.post_mission_reflection import (
            _read_journal_file,
            is_significant_mission,
            run_reflection,
            write_to_journal,
        )

        inst = Path(instance_dir)
        journal_content = _read_journal_file(inst, project_name)

        if not is_significant_mission(mission_title, duration_minutes, journal_content):
            return False

        reflection = run_reflection(inst, mission_title, journal_content)
        if reflection:
            write_to_journal(inst, reflection)
            return True
    except Exception:
        pass
    return False


def check_auto_merge(
    instance_dir: str,
    project_name: str,
    project_path: str,
) -> Optional[str]:
    """Check if current branch should be auto-merged.

    Args:
        instance_dir: Path to instance directory.
        project_name: Current project name.
        project_path: Path to project directory.

    Returns:
        Branch name if auto-merge was attempted, None otherwise.
    """
    try:
        from app.git_sync import run_git
        branch = run_git(project_path, "rev-parse", "--abbrev-ref", "HEAD")
        if not branch:
            return None
        from app.config import get_branch_prefix
        if not branch.startswith(get_branch_prefix()):
            return None

        from app.git_auto_merge import auto_merge_branch

        auto_merge_branch(instance_dir, project_name, project_path, branch)
        return branch
    except Exception:
        return None


def run_post_mission(
    instance_dir: str,
    project_name: str,
    project_path: str,
    run_num: int,
    exit_code: int,
    stdout_file: str,
    stderr_file: str,
    mission_title: str = "",
    autonomous_mode: str = "",
    start_time: int = 0,
) -> dict:
    """Run the complete post-mission processing pipeline.

    This replaces ~50 lines of bash that call 5 different Python scripts.

    Args:
        instance_dir: Path to instance directory.
        project_name: Current project name.
        project_path: Path to project directory.
        run_num: Current run number.
        exit_code: Claude CLI exit code.
        stdout_file: Path to Claude stdout capture file.
        stderr_file: Path to Claude stderr capture file.
        mission_title: Mission description (empty for autonomous).
        autonomous_mode: Current mode (review/implement/deep).
        start_time: Mission start time as unix timestamp.

    Returns:
        Dict with keys:
            success (bool): Whether Claude exited successfully.
            usage_updated (bool): Whether usage tracking was updated.
            pending_archived (bool): Whether pending.md was archived.
            reflection_written (bool): Whether a reflection was generated.
            auto_merge_branch (str|None): Branch name if auto-merge attempted.
            quota_exhausted (bool): Whether quota exhaustion was detected.
            quota_info (tuple|None): (reset_display, resume_message) if exhausted.
    """
    result = {
        "success": exit_code == 0,
        "usage_updated": False,
        "pending_archived": False,
        "reflection_written": False,
        "auto_merge_branch": None,
        "quota_exhausted": False,
        "quota_info": None,
    }

    # 1. Update token usage from JSON output
    usage_state = os.path.join(instance_dir, "usage_state.json")
    usage_md = os.path.join(instance_dir, "usage.md")
    result["usage_updated"] = update_usage(stdout_file, usage_state, usage_md)

    # 2. Check for quota exhaustion
    from app.quota_handler import handle_quota_exhaustion

    koan_root = str(Path(instance_dir).parent)
    quota_result = handle_quota_exhaustion(
        koan_root=koan_root,
        instance_dir=instance_dir,
        project_name=project_name,
        run_count=run_num,
        stdout_file=stdout_file,
        stderr_file=stderr_file,
    )
    if quota_result is not None:
        result["quota_exhausted"] = True
        result["quota_info"] = quota_result
        return result  # Early return — no further processing on quota exhaustion

    # 3. Archive pending.md if agent didn't clean up
    result["pending_archived"] = archive_pending(instance_dir, project_name, run_num)

    # 4. Post-mission processing (only on success)
    if exit_code == 0:
        # Reflection
        if start_time > 0:
            duration_minutes = (int(datetime.now().timestamp()) - start_time) // 60
        else:
            duration_minutes = 0

        mission_text = mission_title if mission_title else f"Autonomous {autonomous_mode} on {project_name}"
        result["reflection_written"] = trigger_reflection(
            instance_dir, mission_text, duration_minutes,
            project_name=project_name,
        )

        # Auto-merge check
        result["auto_merge_branch"] = check_auto_merge(
            instance_dir, project_name, project_path
        )

    return result


def commit_instance(instance_dir: str) -> bool:
    """Commit and push instance directory changes.

    Args:
        instance_dir: Path to instance directory.

    Returns:
        True if a commit was created.
    """
    try:
        from app.git_sync import run_git

        run_git(instance_dir, "add", "-A")

        # Check if there are staged changes
        status = run_git(instance_dir, "diff", "--cached", "--name-only")
        if not status:
            return False  # No changes

        now = datetime.now().strftime("%Y-%m-%d-%H:%M")
        run_git(instance_dir, "commit", "-m", f"koan: {now}")
        run_git(instance_dir, "push", "origin", "main")
        return True
    except Exception:
        return False


# --- CLI interface ---

def _cli_build_command(args: list) -> None:
    """CLI: python -m app.mission_runner build-command ..."""
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--autonomous-mode", default="implement")
    parser.add_argument("--extra-flags", default="")
    parsed = parser.parse_args(args)

    cmd = build_mission_command(
        prompt=parsed.prompt,
        autonomous_mode=parsed.autonomous_mode,
        extra_flags=parsed.extra_flags,
    )
    # Output as space-separated for bash consumption
    # (prompt will be handled separately via file)
    print("\n".join(cmd))


def _cli_parse_output(args: list) -> None:
    """CLI: python -m app.mission_runner parse-output <json_file>"""
    if len(args) < 1:
        print("Usage: mission_runner.py parse-output <json_file>", file=sys.stderr)
        sys.exit(1)

    filepath = args[0]
    try:
        raw = Path(filepath).read_text()
    except OSError as e:
        print(f"Error reading {filepath}: {e}", file=sys.stderr)
        sys.exit(1)

    text = parse_claude_output(raw)
    if text:
        print(text)


def _cli_post_mission(args: list) -> None:
    """CLI: python -m app.mission_runner post-mission ..."""
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--instance", required=True)
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--project-path", required=True)
    parser.add_argument("--run-num", type=int, required=True)
    parser.add_argument("--exit-code", type=int, required=True)
    parser.add_argument("--stdout-file", required=True)
    parser.add_argument("--stderr-file", required=True)
    parser.add_argument("--mission-title", default="")
    parser.add_argument("--autonomous-mode", default="")
    parser.add_argument("--start-time", type=int, default=0)
    parsed = parser.parse_args(args)

    result = run_post_mission(
        instance_dir=parsed.instance,
        project_name=parsed.project_name,
        project_path=parsed.project_path,
        run_num=parsed.run_num,
        exit_code=parsed.exit_code,
        stdout_file=parsed.stdout_file,
        stderr_file=parsed.stderr_file,
        mission_title=parsed.mission_title,
        autonomous_mode=parsed.autonomous_mode,
        start_time=parsed.start_time,
    )

    # Output key results for bash consumption
    if result["quota_exhausted"] and result["quota_info"]:
        reset_display, resume_msg = result["quota_info"]
        print(f"QUOTA_EXHAUSTED|{reset_display}|{resume_msg}")
        sys.exit(2)  # Special exit code for quota exhaustion

    if result["pending_archived"]:
        print("PENDING_ARCHIVED", file=sys.stderr)
    if result["auto_merge_branch"]:
        print(f"AUTO_MERGE|{result['auto_merge_branch']}", file=sys.stderr)

    sys.exit(0 if result["success"] else 1)


def main() -> None:
    """CLI entry point."""
    if len(sys.argv) < 2:
        print(
            "Usage: mission_runner.py <build-command|parse-output|post-mission> [args]",
            file=sys.stderr,
        )
        sys.exit(1)

    subcommand = sys.argv[1]
    remaining = sys.argv[2:]

    if subcommand == "build-command":
        _cli_build_command(remaining)
    elif subcommand == "parse-output":
        _cli_parse_output(remaining)
    elif subcommand == "post-mission":
        _cli_post_mission(remaining)
    else:
        print(f"Unknown subcommand: {subcommand}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
