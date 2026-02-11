"""Project configuration loader — reads projects.yaml.

Provides:
- load_projects_config(koan_root) -> dict: Load and validate projects.yaml
- get_projects_from_config(config) -> list[tuple[str, str]]: Extract (name, path) tuples
- get_project_config(config, name) -> dict: Get merged defaults + project overrides
- get_project_auto_merge(config, name) -> dict: Get auto-merge config for a project
- get_project_cli_provider(config, name) -> str: Get CLI provider for a project
- get_project_models(config, name) -> dict: Get model overrides for a project
- get_project_tools(config, name) -> dict: Get tool restrictions for a project

File location: projects.yaml at KOAN_ROOT (next to .env).
"""

from pathlib import Path
from typing import List, Optional, Tuple

import yaml


def load_projects_config(koan_root: str) -> Optional[dict]:
    """Load projects.yaml from KOAN_ROOT.

    Returns the parsed config dict, or None if file doesn't exist.
    Raises ValueError on invalid YAML or schema violations.
    """
    config_path = Path(koan_root) / "projects.yaml"
    if not config_path.exists():
        return None

    try:
        with open(config_path, "r") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in projects.yaml: {e}")

    if data is None:
        return None

    if not isinstance(data, dict):
        raise ValueError("projects.yaml must be a YAML mapping (dict)")

    _validate_config(data)
    return data


def _validate_config(config: dict) -> None:
    """Validate the structure of the projects config.

    Raises ValueError on validation failures.
    """
    # defaults section is optional, must be dict if present
    defaults = config.get("defaults")
    if defaults is not None and not isinstance(defaults, dict):
        raise ValueError("'defaults' must be a mapping")

    # projects section is required
    projects = config.get("projects")
    if projects is None:
        raise ValueError("'projects' section is required in projects.yaml")

    if not isinstance(projects, dict):
        raise ValueError("'projects' must be a mapping of project_name -> config")

    if not projects:
        raise ValueError("'projects' must contain at least one project")

    if len(projects) > 50:
        raise ValueError(f"Max 50 projects allowed. You have {len(projects)}.")

    for name, project in projects.items():
        if not isinstance(name, str):
            raise ValueError(f"Project name must be a string, got: {type(name).__name__}")

        if project is None:
            raise ValueError(f"Project '{name}' has no configuration (must have at least 'path')")

        if not isinstance(project, dict):
            raise ValueError(f"Project '{name}' must be a mapping, got: {type(project).__name__}")

        if "path" not in project:
            raise ValueError(f"Project '{name}' is missing required 'path' field")

        path = project["path"]
        if not isinstance(path, str) or not path.strip():
            raise ValueError(f"Project '{name}' has invalid path: {path!r}")


def validate_project_paths(config: dict) -> Optional[str]:
    """Check that all project paths exist on disk.

    Returns an error message if any path is missing, or None if all valid.
    Separated from _validate_config() so tests can skip filesystem checks.
    """
    projects = config.get("projects", {})
    for name, project in projects.items():
        path = project.get("path", "")
        if not Path(path).is_dir():
            return f"Project '{name}' path does not exist: {path}"
    return None


def get_projects_from_config(config: dict) -> List[Tuple[str, str]]:
    """Extract sorted (name, path) tuples from config.

    Same format as get_known_projects() returns — enables drop-in replacement.
    """
    projects = config.get("projects", {})
    return sorted(
        [(name, proj.get("path", "").strip()) for name, proj in projects.items()],
        key=lambda x: x[0].lower(),
    )


def get_project_config(config: dict, project_name: str) -> dict:
    """Get merged config for a project (defaults + project overrides).

    Deep-merges per-section: project-level keys override default-level keys.
    Unknown sections are passed through as-is.
    """
    defaults = config.get("defaults", {}) or {}
    project = config.get("projects", {}).get(project_name, {}) or {}

    merged = {}
    # Start with all default keys
    for key, value in defaults.items():
        if isinstance(value, dict):
            # Deep merge dicts (one level)
            project_value = project.get(key, {}) or {}
            merged[key] = {**value, **project_value}
        else:
            merged[key] = project.get(key, value)

    # Add project-only keys not in defaults
    for key, value in project.items():
        if key == "path":
            continue  # path is structural, not a setting
        if key not in merged:
            merged[key] = value

    return merged


def get_project_auto_merge(config: dict, project_name: str) -> dict:
    """Get auto-merge config for a project from projects.yaml.

    Returns a dict with keys: enabled, base_branch, strategy, rules.
    Falls back to defaults section, then sensible defaults.
    """
    project_cfg = get_project_config(config, project_name)
    am = project_cfg.get("git_auto_merge", {}) or {}

    return {
        "enabled": am.get("enabled", False),
        "base_branch": am.get("base_branch", "main"),
        "strategy": am.get("strategy", "squash"),
        "rules": am.get("rules", []),
    }


def get_project_cli_provider(config: dict, project_name: str) -> str:
    """Get CLI provider for a project from projects.yaml.

    Returns the provider name ("claude", "copilot", "local") or empty string
    if not configured (meaning: use the global provider).

    Note: Data accessor only — the provider resolution in cli_provider.py
    does not yet call this. Per-project provider switching requires changes
    to get_provider() to accept a project_name parameter.
    """
    project_cfg = get_project_config(config, project_name)
    return str(project_cfg.get("cli_provider", "")).strip().lower()


def get_project_models(config: dict, project_name: str) -> dict:
    """Get model overrides for a project from projects.yaml.

    Returns a dict with model role keys (mission, chat, lightweight, etc.).
    Only includes keys that are explicitly set — caller should merge with
    global defaults.
    """
    project_cfg = get_project_config(config, project_name)
    models = project_cfg.get("models", {})
    if not isinstance(models, dict):
        return {}
    return models


def get_project_tools(config: dict, project_name: str) -> dict:
    """Get tool restrictions for a project from projects.yaml.

    Returns a dict with keys: mission, chat (lists of tool names).
    Only includes keys that are explicitly set — caller should merge with
    global defaults.
    """
    project_cfg = get_project_config(config, project_name)
    tools = project_cfg.get("tools", {})
    if not isinstance(tools, dict):
        return {}
    return tools


def save_projects_config(koan_root: str, config: dict) -> None:
    """Write config back to projects.yaml atomically."""
    from app.utils import atomic_write

    config_path = Path(koan_root) / "projects.yaml"
    header = (
        "# projects.yaml — Project configuration for Kōan\n"
        "# Auto-managed — manual edits are preserved.\n\n"
    )
    content = header + yaml.dump(config, default_flow_style=False, sort_keys=False)
    atomic_write(config_path, content)


def ensure_github_urls(koan_root: str) -> List[str]:
    """Populate missing github_url fields in projects.yaml from git remotes.

    Iterates all projects, calls get_github_remote() on any project without
    a github_url field, and saves the discovered URL back to projects.yaml.

    Returns a list of log messages for discovered URLs.
    Does NOT overwrite existing github_url values.
    """
    config = load_projects_config(koan_root)
    if config is None:
        return []

    projects = config.get("projects", {})
    if not projects:
        return []

    from app.utils import get_github_remote

    messages = []
    modified = False

    for name, project in projects.items():
        if not isinstance(project, dict):
            continue
        if project.get("github_url"):
            continue

        path = project.get("path", "")
        if not path or not Path(path).is_dir():
            continue

        github_url = get_github_remote(path)
        if github_url:
            project["github_url"] = github_url
            messages.append(f"Discovered github_url for '{name}': {github_url}")
            modified = True

    if modified:
        try:
            save_projects_config(koan_root, config)
        except OSError as e:
            messages.append(f"Warning: could not save projects.yaml: {e}")

    return messages
