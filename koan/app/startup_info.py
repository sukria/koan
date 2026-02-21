"""Gather system information for the startup banner.

Lightweight module that reads config files directly — no heavy imports
(bridge_state, awake) that trigger side effects.
"""

import os
from pathlib import Path


def _get_config_value(key: str, default: str) -> str:
    """Load a value from config.yaml with fallback to default.
    
    Args:
        key: The configuration key to retrieve.
        default: The fallback value if key is not found or config fails to load.
    
    Returns:
        The configuration value or default.
    """
    try:
        from app.utils import load_config
        config = load_config()
        return config.get(key, default)
    except Exception:
        return default


def gather_startup_info(koan_root: Path) -> dict:
    """Collect system info for display in the startup banner.

    Returns a dict with keys like 'provider', 'projects', 'skills', etc.
    All values are strings ready for display. Missing values default to
    placeholder text rather than crashing.
    """
    instance = koan_root / "instance"
    info = {}

    # Provider
    info["provider"] = _get_provider(koan_root)

    # Projects
    info["projects"] = _get_projects_summary(koan_root)

    # Skills
    info["skills"] = _get_skills_summary(koan_root, instance)

    # Soul
    info["soul"] = _get_file_size(instance / "soul.md")

    # Messaging
    info["messaging"] = _get_messaging_provider()

    # Ollama (only when provider needs it)
    if info["provider"] in ("local", "ollama", "ollama-claude"):
        info["ollama"] = _get_ollama_summary()

    return info


def _get_provider(koan_root: Path) -> str:
    """Detect the CLI provider from env or config."""
    try:
        from app.utils import get_cli_provider_env
        provider = get_cli_provider_env()
    except Exception:
        provider = ""
    if not provider:
        provider = _get_config_value("cli_provider", "claude")
    return provider


def _get_projects_summary(koan_root: Path) -> str:
    """Count configured projects."""
    try:
        from app.utils import get_known_projects
        projects = get_known_projects()
        count = len(projects)
        if count == 0:
            return "none configured"
        names = [p[0] for p in projects[:3]]
        suffix = f" +{count - 3} more" if count > 3 else ""
        return f"{count} ({', '.join(names)}{suffix})"
    except Exception:
        return "unavailable"


def _get_skills_summary(koan_root: Path, instance: Path) -> str:
    """Count core and extra skills."""
    try:
        from app.skills import build_registry
        extra_dirs = []
        instance_skills = instance / "skills"
        if instance_skills.is_dir():
            extra_dirs.append(instance_skills)
        registry = build_registry(extra_dirs)
        core = len(registry.list_by_scope("core"))
        total = len(registry.all_skills())
        extra = total - core
        if extra > 0:
            return f"{core} core + {extra} extra"
        return f"{core} core"
    except Exception:
        return "unavailable"


def _get_file_size(path: Path) -> str:
    """Return human-readable file size."""
    try:
        if not path.exists():
            return "not found"
        size = len(path.read_text())
        if size >= 1000:
            return f"{size // 1000}k chars"
        return f"{size} chars"
    except Exception:
        return "unavailable"


def _get_ollama_summary() -> str:
    """Get Ollama server status summary for startup banner.

    Includes version, model count, and configured model readiness.
    """
    try:
        from app.ollama_client import get_version, is_model_available, is_server_ready, list_models
        if not is_server_ready(timeout=2.0):
            return "not responding"
        parts = []
        version = get_version(timeout=2.0)
        if version:
            parts.append(f"v{version}")
        models = list_models(timeout=2.0)
        count = len(models)
        parts.append(f"{count} model{'s' if count != 1 else ''}")

        # Show configured model readiness
        configured = _get_configured_model()
        if configured:
            ready = is_model_available(configured, timeout=2.0)
            status = "ready" if ready else "not pulled"
            parts.append(f"{configured} ({status})")

        return ", ".join(parts)
    except Exception:
        return "unavailable"


def _get_configured_model() -> str:
    """Get the configured model name for the current Ollama provider."""
    try:
        from app.utils import get_cli_provider_env
        provider = get_cli_provider_env()
        if not provider:
            provider = _get_config_value("cli_provider", "claude")

        if provider == "ollama-claude":
            from app.provider.ollama_claude import OllamaClaudeProvider
            return OllamaClaudeProvider()._get_model()
        elif provider in ("local", "ollama"):
            from app.provider.local import LocalLLMProvider
            return LocalLLMProvider()._get_default_model()
    except Exception:
        pass
    return ""


def _get_messaging_provider() -> str:
    """Detect configured messaging provider."""
    provider = os.environ.get("KOAN_MESSAGING_PROVIDER", "").strip()
    if not provider:
        provider = _get_config_value("messaging_provider", "telegram")
    return provider
