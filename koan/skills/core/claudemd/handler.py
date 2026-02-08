"""Koan claudemd skill -- queue a CLAUDE.md refresh mission."""


def handle(ctx):
    """Handle /claude.md <project-name> command.

    Queues a mission that updates or creates CLAUDE.md for the specified
    project, focusing on architecturally significant changes.
    """
    from app.utils import get_known_projects, insert_pending_mission

    args = ctx.args.strip()

    if not args:
        return (
            "Usage: /claude.md <project-name>\n\n"
            "Refreshes the CLAUDE.md file for a project based on recent "
            "architectural changes.\n"
            "If CLAUDE.md doesn't exist, creates one from scratch.\n\n"
            "Example: /claude.md koan"
        )

    # Extract project name (first word)
    project_name = args.split()[0].lower()

    # Resolve project path
    known = get_known_projects()
    project_path = None
    matched_name = None
    for name, path in known:
        if name.lower() == project_name:
            project_path = path
            matched_name = name
            break

    if not project_path:
        names = ", ".join(n for n, _ in known) or "none"
        return f"Project '{project_name}' not found. Known projects: {names}"

    # Build CLI command
    koan_root = ctx.koan_root
    cmd = (
        f"cd {koan_root}/koan && "
        f"{koan_root}/.venv/bin/python3 -m app.claudemd_refresh "
        f"{project_path} --project-name {matched_name}"
    )

    # Queue the mission
    mission_entry = (
        f"- [project:{matched_name}] Refresh CLAUDE.md "
        f"\u2014 run: `{cmd}`"
    )
    missions_path = ctx.instance_dir / "missions.md"
    insert_pending_mission(missions_path, mission_entry)

    return f"CLAUDE.md refresh queued for project {matched_name}"
