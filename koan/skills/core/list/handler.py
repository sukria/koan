"""Koan list skill -- show current missions (pending + in progress)."""

import re


def handle(ctx):
    """Handle /list command -- display numbered mission list."""
    missions_file = ctx.instance_dir / "missions.md"

    if not missions_file.exists():
        return "No missions file found."

    from app.missions import parse_sections

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
            display = _clean_mission(m)
            parts.append(f"  {i}. {display}")
        parts.append("")

    if pending:
        parts.append("PENDING")
        for i, m in enumerate(pending, 1):
            display = _clean_mission(m)
            parts.append(f"  {i}. {display}")

    return "\n".join(parts)


def _clean_mission(text: str) -> str:
    """Clean a mission line for display.

    Strips leading '- ', project tags, and truncates long lines.
    """
    # Strip leading "- "
    if text.startswith("- "):
        text = text[2:]

    # Strip project tag but keep project name as prefix
    tag_match = re.search(r'\[projec?t:([a-zA-Z0-9_-]+)\]\s*', text)
    if tag_match:
        project = tag_match.group(1)
        text = re.sub(r'\[projec?t:[a-zA-Z0-9_-]+\]\s*', '', text)
        text = f"[{project}] {text}"

    # Truncate for readability
    if len(text) > 120:
        text = text[:117] + "..."

    return text
