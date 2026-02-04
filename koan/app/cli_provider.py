"""
CLI provider abstraction for Kōan.

Allows switching between Claude Code CLI and GitHub Copilot CLI
as the underlying AI agent binary. Each provider knows how to
translate Kōan's generic command spec into provider-specific flags.

Configuration:
    config.yaml:  cli_provider: "claude"   (default)
    env var:      KOAN_CLI_PROVIDER=copilot (overrides config.yaml)
"""

import os
import shutil
from typing import List, Optional


# ---------------------------------------------------------------------------
# Tool name mapping: Kōan canonical → provider-specific
# ---------------------------------------------------------------------------

# Claude Code tool names (canonical, used throughout koan codebase)
CLAUDE_TOOLS = {"Bash", "Read", "Write", "Glob", "Grep", "Edit"}

# Copilot CLI tool syntax uses a different convention:
# --allow-tool 'shell(git)' or --allow-all-tools
# Copilot's built-in tools: shell, read_file, edit_file, list_dir, grep, glob
# Mapping from Claude tool names to Copilot tool names
_CLAUDE_TO_COPILOT_TOOLS = {
    "Bash": "shell",
    "Read": "read_file",
    "Write": "write_file",
    "Edit": "edit_file",
    "Glob": "glob",
    "Grep": "grep",
}

_COPILOT_TO_CLAUDE_TOOLS = {v: k for k, v in _CLAUDE_TO_COPILOT_TOOLS.items()}


# ---------------------------------------------------------------------------
# Provider implementations
# ---------------------------------------------------------------------------

class CLIProvider:
    """Base class for CLI provider abstraction.

    A provider knows:
    - What binary to invoke
    - How to translate generic flags into provider-specific CLI args
    """

    name: str = ""

    def binary(self) -> str:
        """Return the CLI binary name or path."""
        raise NotImplementedError

    def is_available(self) -> bool:
        """Check if the binary is installed and accessible."""
        return shutil.which(self.binary()) is not None

    def build_prompt_args(self, prompt: str) -> List[str]:
        """Build args for passing a prompt to the CLI."""
        raise NotImplementedError

    def build_tool_args(
        self,
        allowed_tools: Optional[List[str]] = None,
        disallowed_tools: Optional[List[str]] = None,
    ) -> List[str]:
        """Build args for tool access control.

        Args:
            allowed_tools: Explicit list of allowed tools (Claude names).
            disallowed_tools: Tools to block (Claude names).
        """
        raise NotImplementedError

    def build_model_args(
        self,
        model: str = "",
        fallback: str = "",
    ) -> List[str]:
        """Build args for model selection."""
        raise NotImplementedError

    def build_output_args(self, fmt: str = "") -> List[str]:
        """Build args for output format (e.g., 'json')."""
        raise NotImplementedError

    def build_max_turns_args(self, max_turns: int = 0) -> List[str]:
        """Build args for conversation turn limit."""
        raise NotImplementedError

    def build_mcp_args(self, configs: Optional[List[str]] = None) -> List[str]:
        """Build args for MCP server configuration."""
        raise NotImplementedError

    def build_command(
        self,
        prompt: str,
        allowed_tools: Optional[List[str]] = None,
        disallowed_tools: Optional[List[str]] = None,
        model: str = "",
        fallback: str = "",
        output_format: str = "",
        max_turns: int = 0,
        mcp_configs: Optional[List[str]] = None,
    ) -> List[str]:
        """Build a complete CLI command from generic parameters.

        Returns a list of strings suitable for subprocess.run().
        """
        cmd = [self.binary()]
        cmd.extend(self.build_prompt_args(prompt))
        cmd.extend(self.build_tool_args(allowed_tools, disallowed_tools))
        cmd.extend(self.build_model_args(model, fallback))
        cmd.extend(self.build_output_args(output_format))
        cmd.extend(self.build_max_turns_args(max_turns))
        cmd.extend(self.build_mcp_args(mcp_configs))
        return cmd

    def build_extra_flags(
        self,
        model: str = "",
        fallback: str = "",
        disallowed_tools: Optional[List[str]] = None,
    ) -> List[str]:
        """Build extra flags (model + tool restrictions) for appending to a command.

        This is the provider-aware replacement for utils.build_claude_flags().
        """
        flags: List[str] = []
        flags.extend(self.build_model_args(model, fallback))
        flags.extend(self.build_tool_args(disallowed_tools=disallowed_tools))
        return flags


class ClaudeProvider(CLIProvider):
    """Claude Code CLI provider."""

    name = "claude"

    def binary(self) -> str:
        return "claude"

    def build_prompt_args(self, prompt: str) -> List[str]:
        return ["-p", prompt]

    def build_tool_args(
        self,
        allowed_tools: Optional[List[str]] = None,
        disallowed_tools: Optional[List[str]] = None,
    ) -> List[str]:
        flags: List[str] = []
        if allowed_tools:
            flags.extend(["--allowedTools", ",".join(allowed_tools)])
        if disallowed_tools:
            flags.extend(["--disallowedTools"] + disallowed_tools)
        return flags

    def build_model_args(self, model: str = "", fallback: str = "") -> List[str]:
        flags: List[str] = []
        if model:
            flags.extend(["--model", model])
        if fallback:
            flags.extend(["--fallback-model", fallback])
        return flags

    def build_output_args(self, fmt: str = "") -> List[str]:
        if fmt:
            return ["--output-format", fmt]
        return []

    def build_max_turns_args(self, max_turns: int = 0) -> List[str]:
        if max_turns > 0:
            return ["--max-turns", str(max_turns)]
        return []

    def build_mcp_args(self, configs: Optional[List[str]] = None) -> List[str]:
        if not configs:
            return []
        flags = ["--mcp-config"]
        flags.extend(configs)
        return flags


class CopilotProvider(CLIProvider):
    """GitHub Copilot CLI provider.

    Translates Claude Code flags into Copilot CLI equivalents.

    Key differences from Claude CLI:
    - Binary: 'copilot' (standalone) or 'gh copilot' (via gh)
    - Tool control: --allow-tool 'tool_name' (per tool) or --allow-all-tools
    - No --disallowedTools equivalent (use explicit allow-list instead)
    - Model: --model flag (same as Claude)
    - No --fallback-model equivalent
    - Output: --json flag instead of --output-format json
    - MCP: supported via config files
    """

    name = "copilot"

    def binary(self) -> str:
        # Prefer standalone 'copilot' binary, fallback to 'gh copilot' via wrapper
        if shutil.which("copilot"):
            return "copilot"
        return "gh"

    def _is_gh_mode(self) -> bool:
        """Check if we need to use 'gh copilot' instead of standalone 'copilot'."""
        return not shutil.which("copilot") and shutil.which("gh") is not None

    def is_available(self) -> bool:
        return shutil.which("copilot") is not None or shutil.which("gh") is not None

    def build_prompt_args(self, prompt: str) -> List[str]:
        prefix = ["copilot"] if self._is_gh_mode() else []
        return prefix + ["-p", prompt]

    def build_tool_args(
        self,
        allowed_tools: Optional[List[str]] = None,
        disallowed_tools: Optional[List[str]] = None,
    ) -> List[str]:
        flags: List[str] = []

        if allowed_tools:
            # Check if all canonical tools are allowed → use --allow-all-tools
            if set(allowed_tools) >= CLAUDE_TOOLS:
                flags.append("--allow-all-tools")
            else:
                for tool in allowed_tools:
                    copilot_name = _CLAUDE_TO_COPILOT_TOOLS.get(tool, tool.lower())
                    flags.extend(["--allow-tool", copilot_name])

        # Copilot doesn't have --disallowedTools.
        # If we have disallowed tools, we compute the inverse:
        # allowed = ALL_TOOLS - disallowed
        if disallowed_tools and not allowed_tools:
            remaining = CLAUDE_TOOLS - set(disallowed_tools)
            for tool in sorted(remaining):
                copilot_name = _CLAUDE_TO_COPILOT_TOOLS.get(tool, tool.lower())
                flags.extend(["--allow-tool", copilot_name])

        return flags

    def build_model_args(self, model: str = "", fallback: str = "") -> List[str]:
        flags: List[str] = []
        if model:
            flags.extend(["--model", model])
        # Copilot has no --fallback-model; ignored silently
        return flags

    def build_output_args(self, fmt: str = "") -> List[str]:
        if fmt == "json":
            return ["--json"]
        return []

    def build_max_turns_args(self, max_turns: int = 0) -> List[str]:
        if max_turns > 0:
            return ["--max-turns", str(max_turns)]
        return []

    def build_mcp_args(self, configs: Optional[List[str]] = None) -> List[str]:
        if not configs:
            return []
        # Copilot supports MCP config files (same format)
        flags = ["--mcp-config"]
        flags.extend(configs)
        return flags

    def build_command(
        self,
        prompt: str,
        allowed_tools: Optional[List[str]] = None,
        disallowed_tools: Optional[List[str]] = None,
        model: str = "",
        fallback: str = "",
        output_format: str = "",
        max_turns: int = 0,
        mcp_configs: Optional[List[str]] = None,
    ) -> List[str]:
        """Build a complete Copilot CLI command.

        Handles the gh-mode prefix correctly.
        """
        cmd = [self.binary()]
        # build_prompt_args already includes 'copilot' prefix for gh mode
        cmd.extend(self.build_prompt_args(prompt))
        cmd.extend(self.build_tool_args(allowed_tools, disallowed_tools))
        cmd.extend(self.build_model_args(model, fallback))
        cmd.extend(self.build_output_args(output_format))
        cmd.extend(self.build_max_turns_args(max_turns))
        cmd.extend(self.build_mcp_args(mcp_configs))
        return cmd


# ---------------------------------------------------------------------------
# Provider registry & resolution
# ---------------------------------------------------------------------------

_PROVIDERS = {
    "claude": ClaudeProvider,
    "copilot": CopilotProvider,
}


def get_provider_name() -> str:
    """Determine which CLI provider to use.

    Resolution order:
    1. KOAN_CLI_PROVIDER env var (highest priority)
    2. config.yaml cli_provider key
    3. Default: "claude"
    """
    env_val = os.environ.get("KOAN_CLI_PROVIDER", "").strip().lower()
    if env_val and env_val in _PROVIDERS:
        return env_val

    # Lazy import to avoid circular dependency
    try:
        from app.utils import load_config
        config = load_config()
        config_val = str(config.get("cli_provider", "")).strip().lower()
        if config_val and config_val in _PROVIDERS:
            return config_val
    except Exception:
        pass

    return "claude"


def get_provider() -> CLIProvider:
    """Get the configured CLI provider instance."""
    name = get_provider_name()
    return _PROVIDERS[name]()


def get_cli_binary() -> str:
    """Get the CLI binary command for the configured provider.

    For shell scripts: returns the full command prefix needed to invoke
    the provider (e.g., "claude" or "copilot" or "gh copilot").
    """
    provider = get_provider()
    if isinstance(provider, CopilotProvider) and provider._is_gh_mode():
        return "gh copilot"
    return provider.binary()


def build_cli_flags(
    model: str = "",
    fallback: str = "",
    disallowed_tools: Optional[List[str]] = None,
) -> List[str]:
    """Build extra CLI flags for the configured provider.

    Drop-in replacement for utils.build_claude_flags() that respects
    the configured CLI provider.
    """
    return get_provider().build_extra_flags(model, fallback, disallowed_tools)


def build_tool_flags(
    allowed_tools: Optional[List[str]] = None,
    disallowed_tools: Optional[List[str]] = None,
) -> List[str]:
    """Build tool access flags for the configured provider.

    Translates Claude-style tool names (Bash, Read, Write, etc.) into
    provider-specific flags.
    """
    return get_provider().build_tool_args(allowed_tools, disallowed_tools)


def build_prompt_flags(prompt: str) -> List[str]:
    """Build prompt flags for the configured provider.

    Returns ["-p", prompt] for Claude, or ["copilot", "-p", prompt] for gh mode.
    """
    return get_provider().build_prompt_args(prompt)


def build_output_flags(fmt: str = "") -> List[str]:
    """Build output format flags for the configured provider."""
    return get_provider().build_output_args(fmt)


def build_max_turns_flags(max_turns: int = 0) -> List[str]:
    """Build max-turns flags for the configured provider."""
    return get_provider().build_max_turns_args(max_turns)


def build_full_command(
    prompt: str,
    allowed_tools: Optional[List[str]] = None,
    disallowed_tools: Optional[List[str]] = None,
    model: str = "",
    fallback: str = "",
    output_format: str = "",
    max_turns: int = 0,
    mcp_configs: Optional[List[str]] = None,
) -> List[str]:
    """Build a complete CLI command for the configured provider.

    This is the high-level API: pass generic parameters, get back a
    provider-specific command list ready for subprocess.run().
    """
    return get_provider().build_command(
        prompt=prompt,
        allowed_tools=allowed_tools,
        disallowed_tools=disallowed_tools,
        model=model,
        fallback=fallback,
        output_format=output_format,
        max_turns=max_turns,
        mcp_configs=mcp_configs,
    )
