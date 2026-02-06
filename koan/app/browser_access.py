#!/usr/bin/env python3
"""
Kōan — Browser Access via Playwright MCP

Manages the Playwright MCP server configuration for Claude CLI,
enabling browser-based E2E testing, documentation fetching, and
web automation within agent missions.

Uses the @playwright/mcp npm package via Claude's --mcp-config flag.

Config (instance/config.yaml):
    browser:
      enabled: false
      headless: true
      allowed_domains: []    # empty = all domains allowed

CLI entry point (for run.sh):
    python3 -m app.browser_access flags    → print --mcp-config flags
    python3 -m app.browser_access status   → print browser status
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

# Lazy import to avoid circular dependency at module level
_config_cache = None


def _load_config() -> dict:
    """Load config.yaml lazily, with caching."""
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    from app.utils import load_config
    _config_cache = load_config()
    return _config_cache


def _reset_config_cache():
    """Reset config cache (for testing)."""
    global _config_cache
    _config_cache = None


def get_browser_config() -> dict:
    """Get browser configuration from config.yaml.

    Returns dict with keys: enabled, headless, allowed_domains.
    """
    config = _load_config()
    browser = config.get("browser", {})
    return {
        "enabled": bool(browser.get("enabled", False)),
        "headless": bool(browser.get("headless", True)),
        "allowed_domains": list(browser.get("allowed_domains", [])),
    }


def is_browser_enabled() -> bool:
    """Check if browser access is enabled in config."""
    return get_browser_config()["enabled"]


def is_npx_available() -> bool:
    """Check if npx is available on the system."""
    return shutil.which("npx") is not None


def get_mcp_config_path() -> Path:
    """Get path to the generated MCP config JSON file."""
    koan_root = Path(os.environ.get("KOAN_ROOT", "."))
    return koan_root / "instance" / ".mcp-playwright.json"


def build_mcp_config(headless: bool = True) -> dict:
    """Build the MCP server configuration dict for Playwright.

    This generates a Claude CLI-compatible MCP config that launches
    the Playwright MCP server via npx.

    Args:
        headless: Run browser in headless mode (default True).

    Returns:
        Dict suitable for writing to a JSON config file.
    """
    args = ["npx", "-y", "@playwright/mcp@latest"]
    if headless:
        args.append("--headless")

    return {
        "mcpServers": {
            "playwright": {
                "command": args[0],
                "args": args[1:],
            }
        }
    }


def write_mcp_config() -> Optional[Path]:
    """Write the Playwright MCP config file if browser is enabled.

    Creates/updates instance/.mcp-playwright.json with the current
    browser configuration.

    Returns:
        Path to the config file if written, None if browser is disabled.
    """
    browser_config = get_browser_config()
    if not browser_config["enabled"]:
        return None

    config = build_mcp_config(headless=browser_config["headless"])
    config_path = get_mcp_config_path()

    # Atomic write
    config_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(config_path.parent), prefix=".mcp-", suffix=".json"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(config, f, indent=2)
        os.replace(tmp, str(config_path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    return config_path


def get_browser_mcp_flags() -> List[str]:
    """Get CLI flags to enable Playwright MCP for Claude invocations.

    Writes the MCP config file if needed and returns the flags to
    append to a Claude CLI command.

    Returns:
        List of CLI flags (e.g., ["--mcp-config", "/path/to/.mcp-playwright.json"])
        or empty list if browser is disabled.
    """
    if not is_browser_enabled():
        return []

    config_path = write_mcp_config()
    return ["--mcp-config", str(config_path)]


def get_browser_status() -> dict:
    """Get comprehensive browser access status.

    Returns:
        Dict with keys: enabled, headless, npx_available, config_file_exists,
        allowed_domains.
    """
    browser_config = get_browser_config()
    config_path = get_mcp_config_path()

    return {
        "enabled": browser_config["enabled"],
        "headless": browser_config["headless"],
        "npx_available": is_npx_available(),
        "config_file_exists": config_path.exists(),
        "allowed_domains": browser_config["allowed_domains"],
    }


def format_browser_status() -> str:
    """Format browser status for human-readable display (Telegram).

    Returns:
        Multi-line status string.
    """
    status = get_browser_status()

    if not status["enabled"]:
        return (
            "Browser access: DISABLED\n"
            "Enable in config.yaml:\n"
            "  browser:\n"
            "    enabled: true"
        )

    lines = []
    lines.append("Browser access: ENABLED")
    lines.append(f"  Mode: {'headless' if status['headless'] else 'visible'}")

    if status["npx_available"]:
        lines.append("  npx: available")
    else:
        lines.append("  npx: NOT FOUND (install Node.js)")

    if status["allowed_domains"]:
        lines.append(f"  Domains: {', '.join(status['allowed_domains'])}")
    else:
        lines.append("  Domains: all (no restrictions)")

    if status["config_file_exists"]:
        lines.append("  MCP config: written")
    else:
        lines.append("  MCP config: not yet generated")

    return "\n".join(lines)


def get_browser_flags_for_shell() -> str:
    """Get browser MCP flags as a shell-safe string.

    Designed to be called from run.sh:
        BROWSER_FLAGS=$("$PYTHON" -c "from app.browser_access import get_browser_flags_for_shell; print(get_browser_flags_for_shell())")

    Returns:
        Space-separated CLI flags string (may be empty).
    """
    flags = get_browser_mcp_flags()
    return " ".join(flags)


# ---------------------------------------------------------------------------
# CLI entry point (for run.sh integration)
# ---------------------------------------------------------------------------

def main():
    """CLI entry point for browser access module.

    Usage:
        python3 -m app.browser_access flags    → print MCP flags for Claude CLI
        python3 -m app.browser_access status   → print browser status
    """
    if len(sys.argv) < 2:
        print("Usage: python3 -m app.browser_access [flags|status]", file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]

    if command == "flags":
        print(get_browser_flags_for_shell())
    elif command == "status":
        print(format_browser_status())
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
