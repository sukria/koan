"""Merged project registry — unifies workspace + projects.yaml.

Single source of truth for the project list. Combines auto-discovered
workspace projects with manually configured projects.yaml entries.

Resolution: projects.yaml entries take precedence over workspace entries
with the same name.
"""

import logging
import threading
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_MAX_PROJECTS = 50

# Thread-safe cache
_lock = threading.Lock()
_cached_projects: Optional[List[Tuple[str, str]]] = None
_cached_warnings: List[str] = []
_github_url_cache: Dict[str, str] = {}


def get_all_projects(koan_root: str) -> List[Tuple[str, str]]:
    """Return merged, deduplicated, sorted list of (name, path) tuples.

    Sources (in priority order):
    1. projects.yaml — explicit configuration always wins
    2. workspace/ — auto-discovered projects fill gaps

    Duplicates: yaml entry wins; a warning is emitted.
    Limit: 50 projects total across both sources.
    """
    with _lock:
        if _cached_projects is not None:
            return list(_cached_projects)

    return refresh_projects(koan_root)


def refresh_projects(koan_root: str) -> List[Tuple[str, str]]:
    """Re-scan workspace and reload projects.yaml. Updates cache.

    Call at startup and when /projects command is invoked.
    Returns the merged project list.
    """
    from app.projects_config import load_projects_config, get_projects_from_config
    from app.workspace_discovery import discover_workspace_projects

    warnings = []

    # 1. Load yaml projects
    yaml_projects = []
    try:
        config = load_projects_config(koan_root)
        if config is not None:
            yaml_projects = get_projects_from_config(config)
    except Exception as e:
        warnings.append(f"⚠️ Cannot load projects.yaml: {e}")

    # 2. Discover workspace projects
    workspace_projects = discover_workspace_projects(koan_root)

    # 3. Merge with deduplication (yaml wins)
    yaml_names = {name.lower() for name, _ in yaml_projects}
    merged = dict(yaml_projects)  # name -> path

    for name, path in workspace_projects:
        if name.lower() in yaml_names:
            # Check if yaml project has same name but we need to find it
            yaml_path = merged.get(name, "")
            if not yaml_path:
                # Case-insensitive match — find the actual yaml name
                for yn, yp in yaml_projects:
                    if yn.lower() == name.lower():
                        yaml_path = yp
                        break
            warnings.append(
                f"⚠️ Duplicate project '{name}': "
                f"using {yaml_path} (yaml) instead of {path} (workspace)"
            )
            continue
        merged[name] = path

    # 4. Enforce limit
    sorted_projects = sorted(merged.items(), key=lambda x: x[0].lower())
    if len(sorted_projects) > _MAX_PROJECTS:
        warnings.append(
            f"⚠️ {len(sorted_projects)} projects found, "
            f"limit is {_MAX_PROJECTS}. Keeping first {_MAX_PROJECTS} alphabetically."
        )
        sorted_projects = sorted_projects[:_MAX_PROJECTS]

    result = [(name, path) for name, path in sorted_projects]

    # Update cache
    with _lock:
        global _cached_projects, _cached_warnings
        _cached_projects = list(result)
        _cached_warnings = warnings

    return result


def get_warnings() -> List[str]:
    """Return warnings from last refresh (duplicates, limit, errors)."""
    with _lock:
        return list(_cached_warnings)


def invalidate_cache() -> None:
    """Clear the project cache. Next get_all_projects() call will re-scan."""
    with _lock:
        global _cached_projects, _cached_warnings
        _cached_projects = None
        _cached_warnings = []


def get_github_url_cache() -> Dict[str, str]:
    """Return the in-memory github_url cache (project_name -> url)."""
    with _lock:
        return dict(_github_url_cache)


def set_github_url(project_name: str, url: str) -> None:
    """Cache a github_url for a project (workspace or yaml)."""
    with _lock:
        _github_url_cache[project_name] = url


def get_github_url(project_name: str) -> Optional[str]:
    """Get cached github_url for a project, or None."""
    with _lock:
        return _github_url_cache.get(project_name)


def clear_github_url_cache() -> None:
    """Clear the github_url memory cache."""
    with _lock:
        _github_url_cache.clear()
