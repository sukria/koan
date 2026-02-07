"""Koan mission skill — create missions."""

from pathlib import Path


def handle(ctx):
    """Handle /mission <text> command."""
    from app.utils import (
        parse_project as _parse_project,
        insert_pending_mission,
        get_known_projects,
    )

    raw_args = ctx.args.strip()
    if not raw_args:
        return (
            "Usage: /mission <description>\n\n"
            "Examples:\n"
            "  /mission fix the login bug\n"
            "  /mission [project:koan] add retry logic"
        )

    # Check for project tag
    project, mission_text = _parse_project(raw_args)

    if not project:
        known = get_known_projects()
        if len(known) > 1:
            project_list = "\n".join(f"  - {name}" for name in known)
            return (
                f"Which project for this mission?\n\n"
                f"{project_list}\n\n"
                f"Reply with the tag, e.g.:\n"
                f"  /mission [project:{known[0]}] {raw_args[:80]}"
            )

    # Clean up mission prefix
    if mission_text.lower().startswith("mission:"):
        mission_text = mission_text[8:].strip()
    elif mission_text.lower().startswith("mission :"):
        mission_text = mission_text[9:].strip()

    # Format mission entry with project tag
    if project:
        mission_entry = f"- [project:{project}] {mission_text}"
    else:
        mission_entry = f"- {mission_text}"

    missions_file = ctx.instance_dir / "missions.md"
    insert_pending_mission(missions_file, mission_entry)

    ack = "✅ Mission received"
    if project:
        ack += f" (project: {project})"
    ack += f":\n\n{mission_text[:500]}"
    return ack
