#!/usr/bin/env python3
"""
Kōan — MCP Server Discovery and Management

Discovers MCP servers configured in Claude Code and provides:
- Server listing for /mcp command
- MCP config flags for Claude CLI calls (--mcp-config)
- Capability descriptions from config.yaml for prompt augmentation
"""

import json
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from app.utils import load_config


def list_mcp_servers() -> List[Dict[str, str]]:
    """Discover MCP servers configured in Claude Code.

    Runs `claude mcp list` and parses the output into structured data.

    Returns:
        List of dicts with keys: name, type, status (from CLI output).
        Empty list if none configured or on error.
    """
    try:
        result = subprocess.run(
            ["claude", "mcp", "list"],
            capture_output=True, text=True, timeout=10,
        )
        output = result.stdout.strip()
        if not output or "No MCP servers configured" in output:
            return []
        return _parse_mcp_list(output)
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
        print(f"[mcp] Error listing servers: {e}")
        return []


def _parse_mcp_list(output: str) -> List[Dict[str, str]]:
    """Parse the output of `claude mcp list`.

    The output format is typically:
    - name: server-name
      Type: stdio|http|sse
      Status: connected|disconnected
      ...

    Or tabular format. We handle both.
    """
    servers = []
    current: Dict[str, str] = {}

    for line in output.splitlines():
        line = line.strip()
        if not line:
            if current:
                servers.append(current)
                current = {}
            continue

        # Handle "- name: value" format
        if line.startswith("- "):
            if current:
                servers.append(current)
            current = {"name": line[2:].strip().rstrip(":")}
            # Check if it's "- name: value" on same line
            if ":" in line[2:]:
                parts = line[2:].split(":", 1)
                current = {"name": parts[1].strip()}
            continue

        # Handle "Key: value" lines within a server block
        if ":" in line and current:
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key in ("type", "transport"):
                current["type"] = value
            elif key == "status":
                current["status"] = value
            elif key == "command" or key == "url":
                current["command"] = value

    if current:
        servers.append(current)

    return servers


def get_mcp_capabilities() -> Dict[str, str]:
    """Get MCP server capability descriptions from config.yaml.

    The config.yaml can declare known MCP capabilities:

        mcp:
          capabilities:
            gmail: "Read and send emails via Gmail"
            google-calendar: "Access Google Calendar events"
            slack: "Send and read Slack messages"

    Returns:
        Dict mapping server name to capability description.
    """
    config = load_config()
    mcp_config = config.get("mcp", {})
    return mcp_config.get("capabilities", {})


def get_mcp_server_names() -> List[str]:
    """Get just the names of configured MCP servers.

    Returns:
        Sorted list of server names.
    """
    servers = list_mcp_servers()
    return sorted(s.get("name", "") for s in servers if s.get("name"))


def format_mcp_list(servers: List[Dict[str, str]], capabilities: Dict[str, str]) -> str:
    """Format MCP server list for Telegram display.

    Args:
        servers: List of server dicts from list_mcp_servers()
        capabilities: Dict from get_mcp_capabilities()

    Returns:
        Formatted string for Telegram.
    """
    if not servers:
        return (
            "Aucun serveur MCP configuré.\n\n"
            "Pour en ajouter :\n"
            "  claude mcp add <name> <command>\n\n"
            "Exemples :\n"
            "  claude mcp add gmail -- npx @anthropic/gmail-mcp\n"
            "  claude mcp add --transport http calendar https://calendar-mcp.example.com"
        )

    lines = ["Serveurs MCP configurés :"]
    for s in servers:
        name = s.get("name", "?")
        stype = s.get("type", "")
        status = s.get("status", "")

        line = f"  • {name}"
        if stype:
            line += f" ({stype})"
        if status:
            line += f" — {status}"

        # Add capability description if known
        cap = capabilities.get(name, "")
        if cap:
            line += f"\n    {cap}"

        lines.append(line)

    lines.append("")
    lines.append("Ces serveurs sont disponibles dans les conversations et missions.")
    return "\n".join(lines)


def build_mcp_flags() -> List[str]:
    """Build --mcp-config flags for Claude CLI from config.yaml.

    Reads mcp.configs section from config.yaml and builds CLI flags.

    config.yaml format:
        mcp:
          configs:
            - /path/to/mcp-config.json
            - '{"mcpServers": {"name": {...}}}'

    Returns:
        List of CLI flags (e.g., ["--mcp-config", "config1.json", "config2.json"])
        or empty list if no MCP configs.
    """
    config = load_config()
    mcp_config = config.get("mcp", {})
    configs = mcp_config.get("configs", [])

    if not configs:
        return []

    flags = ["--mcp-config"]
    flags.extend(str(c) for c in configs)
    return flags


def get_mcp_config_paths() -> List[str]:
    """Get raw MCP config file paths from config.yaml.

    Returns:
        List of config paths/strings, or empty list.
    """
    config = load_config()
    mcp_config = config.get("mcp", {})
    configs = mcp_config.get("configs", [])
    return [str(c) for c in configs] if configs else []


def get_mcp_prompt_context() -> str:
    """Build MCP context string for inclusion in prompts.

    When MCP servers are available, this tells Claude what capabilities
    it has access to, so it can use them proactively.

    Returns:
        Context string for prompt injection, or empty string.
    """
    servers = list_mcp_servers()
    if not servers:
        return ""

    capabilities = get_mcp_capabilities()
    names = [s.get("name", "") for s in servers if s.get("name")]

    lines = ["Available MCP servers (you can use tools from these):"]
    for name in sorted(names):
        cap = capabilities.get(name, "")
        if cap:
            lines.append(f"  - {name}: {cap}")
        else:
            lines.append(f"  - {name}")

    return "\n".join(lines)
