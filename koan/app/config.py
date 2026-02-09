"""Configuration loading and access — extracted from utils.py for clarity.

Handles:
- Tool configuration (chat/mission tools, descriptions)
- Model configuration (per-role model selection)
- Claude CLI flag building
- Behavioral settings (max_runs, interval, fast_reply, etc.)
- Auto-merge configuration
- CLI provider shell helpers

Note: load_config() itself lives in utils.py to avoid circular imports.
Functions here call it via import to ensure mocks propagate correctly.
"""

import os
from typing import List, Optional


def _load_config() -> dict:
    """Import and call load_config from utils — ensures mock patches propagate."""
    from app.utils import load_config
    return load_config()


def get_chat_tools() -> str:
    """Get comma-separated list of tools for chat responses.

    Chat uses a restricted set by default (read-only) to prevent prompt
    injection attacks from Telegram messages. Bash is explicitly excluded.

    Config key: tools.chat (default: Read, Glob, Grep)

    Returns:
        Comma-separated tool names.
    """
    config = _load_config()
    default_chat_tools = ["Read", "Glob", "Grep"]
    tools = config.get("tools", {}).get("chat", default_chat_tools)
    return ",".join(tools)


def get_mission_tools() -> str:
    """Get comma-separated list of tools for mission execution.

    Missions run with full tool access including Bash for code execution.

    Config key: tools.mission (default: Read, Glob, Grep, Edit, Write, Bash)

    Returns:
        Comma-separated tool names.
    """
    config = _load_config()
    default_mission_tools = ["Read", "Glob", "Grep", "Edit", "Write", "Bash"]
    tools = config.get("tools", {}).get("mission", default_mission_tools)
    return ",".join(tools)


# Backward compatibility alias
def get_allowed_tools() -> str:
    """Deprecated: Use get_chat_tools() or get_mission_tools() instead."""
    return get_mission_tools()


def get_tools_description() -> str:
    """Get tools description from config for inclusion in prompts."""
    config = _load_config()
    return config.get("tools", {}).get("description", "")


def get_model_config() -> dict:
    """Get model configuration from config.yaml.

    Returns dict with keys: mission, chat, lightweight, fallback, review_mode.
    Empty strings mean "use default model".
    """
    config = _load_config()
    defaults = {
        "mission": "",
        "chat": "",
        "lightweight": "haiku",
        "fallback": "sonnet",
        "review_mode": "",
    }
    models = config.get("models", {})
    return {k: models.get(k, v) for k, v in defaults.items()}


def get_start_on_pause() -> bool:
    """Check if start_on_pause is enabled in config.yaml.

    Returns True if koan should boot directly into pause mode.
    """
    config = _load_config()
    return bool(config.get("start_on_pause", False))


def get_max_runs() -> int:
    """Get maximum runs per day from config.yaml.

    This is the primary source of truth for max_runs configuration.
    Returns default of 20 if not configured.
    """
    config = _load_config()
    return int(config.get("max_runs_per_day", 20))


def get_interval_seconds() -> int:
    """Get interval between runs in seconds from config.yaml.

    This is the primary source of truth for run interval configuration.
    Returns default of 300 (5 minutes) if not configured.
    """
    config = _load_config()
    return int(config.get("interval_seconds", 300))


def get_fast_reply_model() -> str:
    """Get model to use for fast replies (command handlers like /usage, /sparring).

    When config.fast_reply is True, returns the lightweight model (usually Haiku)
    for faster, cheaper responses. When False, returns empty string (use default).

    Returns:
        Model name string (e.g., "haiku") or empty string for default model.
    """
    config = _load_config()
    fast_reply = config.get("fast_reply", False)
    if fast_reply:
        models = get_model_config()
        return models["lightweight"]
    return ""


def get_branch_prefix() -> str:
    """Get the branch prefix used for agent-created branches.

    Reads 'branch_prefix' from config.yaml. Defaults to 'koan' if not set.
    Always returns the prefix with a trailing '/' (e.g., 'koan/').

    This allows multiple bot instances to use distinct prefixes
    (e.g., 'koan-bot1/', 'koan-bot2/') so their branches don't collide.
    """
    config = _load_config()
    prefix = config.get("branch_prefix", "").strip()
    if not prefix:
        prefix = "koan"
    # Strip trailing slash if present, we'll add it ourselves
    prefix = prefix.rstrip("/")
    return f"{prefix}/"


def get_contemplative_chance() -> int:
    """Get probability (0-100) of triggering contemplative mode on autonomous runs.

    When no mission is pending, this is the chance that koan will run a
    contemplative session instead of autonomous work. Allows for regular
    moments of reflection without waiting for budget exhaustion.

    Returns:
        Integer percentage (0-100). Default: 10 (one in ten autonomous runs).
    """
    config = _load_config()
    return int(config.get("contemplative_chance", 10))


def build_claude_flags(
    model: str = "",
    fallback: str = "",
    disallowed_tools: Optional[List[str]] = None,
) -> List[str]:
    """Build extra CLI flags — provider-aware.

    Delegates to the configured CLI provider for proper flag generation.

    Args:
        model: Model name/alias (empty = use default)
        fallback: Fallback model when primary is overloaded (empty = none)
        disallowed_tools: Tools to block (e.g., ["Bash", "Edit", "Write"] for read-only)

    Returns:
        List of CLI flag strings to append to the command.
    """
    from app.cli_provider import build_cli_flags
    return build_cli_flags(model=model, fallback=fallback, disallowed_tools=disallowed_tools)


def get_claude_flags_for_role(role: str, autonomous_mode: str = "") -> str:
    """Get CLI flags for a Claude invocation role, as a space-separated string.

    Provider-aware: delegates to the configured CLI provider for proper flag generation.
    Designed to be called from run.py to get model/fallback flags.

    Args:
        role: One of "mission", "chat", "lightweight", "contemplative"
        autonomous_mode: Current mode (review/implement/deep) — affects tool restrictions

    Returns:
        Space-separated CLI flags string (may be empty)
    """
    from app.cli_provider import get_provider

    models = get_model_config()
    provider = get_provider()

    model = ""
    fallback = ""
    disallowed: Optional[List[str]] = None

    if role == "mission":
        model = models["mission"]
        if autonomous_mode == "review" and models["review_mode"]:
            model = models["review_mode"]
        fallback = models["fallback"]
        if autonomous_mode == "review":
            disallowed = ["Bash", "Edit", "Write"]
    elif role == "contemplative":
        model = models["lightweight"]
    elif role == "chat":
        model = models["chat"]
        fallback = models["fallback"]

    flags = provider.build_extra_flags(model=model, fallback=fallback, disallowed_tools=disallowed)
    return " ".join(flags)


def get_cli_binary_for_shell() -> str:
    """Get the CLI binary name for shell scripts.

    Returns the binary command (e.g., "claude", "copilot", "gh copilot").
    Called from run.py to set CLI_BIN.
    """
    from app.cli_provider import get_cli_binary
    return get_cli_binary()


def get_cli_provider_name() -> str:
    """Get the configured CLI provider name for display.

    Returns "claude" or "copilot".
    """
    from app.cli_provider import get_provider_name
    return get_provider_name()


def get_tool_flags_for_shell(tools: str) -> str:
    """Convert comma-separated tool names to provider-specific flag string.

    Args:
        tools: Comma-separated Claude tool names (e.g., "Read,Write,Glob,Grep")

    Returns:
        Space-separated CLI flags for the configured provider.
    """
    from app.cli_provider import build_tool_flags
    tool_list = [t.strip() for t in tools.split(",") if t.strip()]
    flags = build_tool_flags(allowed_tools=tool_list)
    return " ".join(flags)


def get_output_flags_for_shell(fmt: str) -> str:
    """Convert output format to provider-specific flag string.

    Args:
        fmt: Output format (e.g., "json")

    Returns:
        Space-separated CLI flags for the configured provider.
    """
    from app.cli_provider import build_output_flags
    flags = build_output_flags(fmt)
    return " ".join(flags)


def get_auto_merge_config(config: dict, project_name: str) -> dict:
    """Get auto-merge config with per-project override support.

    Resolution order:
    1. projects.yaml (if it exists) — per-project git_auto_merge
    2. config.yaml — global git_auto_merge only

    Args:
        config: Full config dict from load_config()
        project_name: Name of the project (e.g., "koan", "anantys-back")

    Returns:
        Merged config with keys: enabled, base_branch, strategy, rules
    """
    # Try projects.yaml first
    try:
        from app.projects_config import load_projects_config, get_project_auto_merge
        koan_root = os.environ.get("KOAN_ROOT", "")
        projects_config = load_projects_config(koan_root) if koan_root else None
        if projects_config and project_name in projects_config.get("projects", {}):
            return get_project_auto_merge(projects_config, project_name)
    except Exception:
        pass

    # Fall back to config.yaml global settings
    global_cfg = config.get("git_auto_merge", {})
    return {
        "enabled": global_cfg.get("enabled", True),
        "base_branch": global_cfg.get("base_branch", "main"),
        "strategy": global_cfg.get("strategy", "squash"),
        "rules": global_cfg.get("rules", []),
    }
