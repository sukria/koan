"""Kōan explore/noexplore skill — toggle per-project exploration mode."""

from pathlib import Path


def handle(ctx):
    """Toggle exploration mode for a project, or show status."""
    koan_root = str(ctx.koan_root)
    args = ctx.args.strip() if ctx.args else ""
    is_disable = ctx.command_name == "noexplore"

    config = _load_config(koan_root)
    if config is None:
        return "❌ No projects.yaml found. Configure projects first."

    projects = config.get("projects", {})
    if not projects:
        return "❌ No projects configured in projects.yaml."

    # No args → show status
    if not args:
        return _show_status(config, projects)

    # /explore all or /explore none
    lower_args = args.lower()
    if lower_args == "all":
        return _set_all(koan_root, config, projects, True)
    if lower_args == "none":
        return _set_all(koan_root, config, projects, False)

    # /explore <project> or /noexplore <project>
    enable = not is_disable
    return _set_exploration(koan_root, config, projects, args, enable)


def _load_config(koan_root):
    """Load projects.yaml, returning None on failure."""
    from app.projects_config import load_projects_config

    try:
        return load_projects_config(koan_root)
    except (ValueError, OSError):
        return None


def _resolve_project_name(projects, name):
    """Case-insensitive project name lookup.

    Returns the canonical name from projects dict, or None.
    """
    lower = name.lower()
    for key in projects:
        if key.lower() == lower:
            return key
    return None


def _get_exploration_status(config, project_name):
    """Get effective exploration status for a project (with defaults merge)."""
    from app.projects_config import get_project_exploration

    return get_project_exploration(config, project_name)


def _show_status(config, projects):
    """Show exploration status for all projects."""
    lines = ["🔭 Exploration status:"]
    for name in sorted(projects, key=str.lower):
        enabled = _get_exploration_status(config, name)
        icon = "✅" if enabled else "❌"
        state = "ON" if enabled else "OFF"
        lines.append(f"  {icon} {name}: {state}")

    lines.append("")
    lines.append("/explore <project> to enable")
    lines.append("/noexplore <project> to disable")
    return "\n".join(lines)


def _set_exploration(koan_root, config, projects, name, enable):
    """Enable or disable exploration for a single project."""
    canonical = _resolve_project_name(projects, name)
    if canonical is None:
        known = ", ".join(sorted(projects.keys(), key=str.lower))
        return f"❌ Unknown project: '{name}'. Known projects: {known}"

    current = _get_exploration_status(config, canonical)
    if current == enable:
        state = "enabled" if enable else "disabled"
        return f"🔭 Exploration already {state} for {canonical}."

    # Write override at project level
    project_entry = projects.get(canonical)
    if project_entry is None:
        projects[canonical] = {}
        project_entry = projects[canonical]
    project_entry["exploration"] = enable

    _save_config(koan_root, config)

    if enable:
        return f"🔭 Exploration enabled for {canonical}. Autonomous work will include this project."
    return f"🔭 Exploration disabled for {canonical}. Only explicit missions will run."


def _set_all(koan_root, config, projects, enable):
    """Enable or disable exploration for all projects."""
    changed = 0
    for name in projects:
        current = _get_exploration_status(config, name)
        if current != enable:
            project_entry = projects.get(name)
            if project_entry is None:
                projects[name] = {}
                project_entry = projects[name]
            project_entry["exploration"] = enable
            changed += 1

    if changed == 0:
        state = "enabled" if enable else "disabled"
        return f"🔭 Exploration already {state} for all projects."

    _save_config(koan_root, config)

    state = "enabled" if enable else "disabled"
    return f"🔭 Exploration {state} for {changed} project(s)."


def _save_config(koan_root, config):
    """Persist config to projects.yaml."""
    from app.projects_config import save_projects_config

    save_projects_config(koan_root, config)
