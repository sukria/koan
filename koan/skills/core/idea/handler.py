"""Kōan idea skill -- manage the ideas backlog in missions.md."""

import re


def handle(ctx):
    """Handle /idea, /ideas, and /buffer commands."""
    command = ctx.command_name
    args = ctx.args.strip()

    missions_file = ctx.instance_dir / "missions.md"

    # /ideas is always listing
    if command == "ideas":
        return _list_ideas(missions_file)

    # /idea or /buffer with no args → list
    if not args:
        return _list_ideas(missions_file)

    # /idea delete N
    delete_match = re.match(r"^(?:delete|del|remove|rm)\s+(\d+)$", args, re.IGNORECASE)
    if delete_match:
        return _delete_idea(missions_file, int(delete_match.group(1)))

    # /idea promote all
    promote_all_match = re.match(r"^(?:promote|push|activate)\s+all$", args, re.IGNORECASE)
    if promote_all_match:
        return _promote_all_ideas(missions_file)

    # /idea promote N
    promote_match = re.match(r"^(?:promote|push|activate)\s+(\d+)$", args, re.IGNORECASE)
    if promote_match:
        return _promote_idea(missions_file, int(promote_match.group(1)))

    # /idea <text> → add new idea
    return _add_idea(missions_file, args)


def _list_ideas(missions_file):
    """List all ideas with numbered index."""
    if not missions_file.exists():
        return "ℹ️ No missions file found."

    from app.missions import parse_ideas, clean_mission_display

    ideas = parse_ideas(missions_file.read_text())

    if not ideas:
        return "ℹ️ No ideas in the backlog. Add one with /idea <description>"

    parts = ["IDEAS"]
    for i, idea in enumerate(ideas, 1):
        display = clean_mission_display(idea)
        parts.append(f"  {i}. {display}")

    parts.append("")
    parts.append("Commands: /idea delete N, /idea promote N, /idea promote all")
    return "\n".join(parts)


def _add_idea(missions_file, text):
    """Add a new idea to the backlog."""
    from app.missions import insert_idea
    from app.utils import (
        parse_project,
        detect_project_from_text,
        get_known_projects,
        modify_missions_file,
    )

    # Check for explicit [project:name] tag first
    project, clean_text = parse_project(text)

    # Auto-detect project from first word (e.g. "/idea koan some text")
    if not project:
        project, detected_text = detect_project_from_text(text)
        if project:
            clean_text = detected_text

    # Multi-project setup with no project specified → ask user
    if not project:
        known = get_known_projects()
        if len(known) > 1:
            project_list = "\n".join(f"  - {name}" for name, _path in known)
            first_name = known[0][0]
            return (
                f"Which project for this idea?\n\n"
                f"{project_list}\n\n"
                f"Reply with the project, e.g.:\n"
                f"  /idea {first_name} {text[:80]}"
            )

    if project:
        entry = f"- [project:{project}] {clean_text}"
    else:
        entry = f"- {clean_text}"

    modify_missions_file(missions_file, lambda content: insert_idea(content, entry))

    display = clean_text[:100]
    if len(clean_text) > 100:
        display += "..."

    ack = "💡 Idea saved"
    if project:
        ack += f" (project: {project})"
    ack += f": {display}"
    return ack


def _delete_idea(missions_file, index):
    """Delete an idea by index."""
    from app.missions import delete_idea, clean_mission_display
    from app.utils import modify_missions_file

    deleted_text = None

    def _transform(content):
        nonlocal deleted_text
        updated, deleted_text = delete_idea(content, index)
        return updated

    modify_missions_file(missions_file, _transform)

    if deleted_text is None:
        from app.missions import parse_ideas
        count = len(parse_ideas(missions_file.read_text()))
        if count == 0:
            return "ℹ️ No ideas to delete."
        return f"⚠️ Invalid index. Use 1-{count}."

    display = clean_mission_display(deleted_text)
    return f"🗑 Deleted: {display}"


def _promote_idea(missions_file, index):
    """Promote an idea to the pending queue."""
    from app.missions import promote_idea, clean_mission_display
    from app.utils import modify_missions_file

    promoted_text = None

    def _transform(content):
        nonlocal promoted_text
        updated, promoted_text = promote_idea(content, index)
        return updated

    modify_missions_file(missions_file, _transform)

    if promoted_text is None:
        from app.missions import parse_ideas
        count = len(parse_ideas(missions_file.read_text()))
        if count == 0:
            return "ℹ️ No ideas to promote."
        return f"⚠️ Invalid index. Use 1-{count}."

    display = clean_mission_display(promoted_text)
    return f"⬆️ Promoted to pending: {display}"


def _promote_all_ideas(missions_file):
    """Promote all ideas to the pending queue."""
    from app.missions import promote_all_ideas, clean_mission_display
    from app.utils import modify_missions_file

    promoted_list = None

    def _transform(content):
        nonlocal promoted_list
        updated, promoted_list = promote_all_ideas(content)
        return updated

    modify_missions_file(missions_file, _transform)

    if not promoted_list:
        return "ℹ️ No ideas to promote."

    count = len(promoted_list)
    lines = [f"⬆️ Promoted {count} idea{'s' if count > 1 else ''} to pending:"]
    for idea in promoted_list:
        display = clean_mission_display(idea)
        lines.append(f"  - {display}")
    return "\n".join(lines)
