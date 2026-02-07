"""Koan list skill -- show current missions (pending + in progress)."""


def handle(ctx):
    """Handle /list command -- display numbered mission list."""
    missions_file = ctx.instance_dir / "missions.md"

    if not missions_file.exists():
        return "No missions file found."

    from app.missions import parse_sections, clean_mission_display

    content = missions_file.read_text()
    sections = parse_sections(content)

    in_progress = sections.get("in_progress", [])
    pending = sections.get("pending", [])

    if not in_progress and not pending:
        return "No missions pending or in progress."

    parts = []

    if in_progress:
        parts.append("IN PROGRESS")
        for i, m in enumerate(in_progress, 1):
            display = clean_mission_display(m)
            parts.append(f"  {i}. {display}")
        parts.append("")

    if pending:
        parts.append("PENDING")
        for i, m in enumerate(pending, 1):
            display = clean_mission_display(m)
            parts.append(f"  {i}. {display}")

    return "\n".join(parts)
