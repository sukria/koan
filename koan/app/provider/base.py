"""Base class and constants for CLI provider abstraction."""

import shutil
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Tool name mapping: Kōan canonical -> provider-specific
# ---------------------------------------------------------------------------

# Claude Code tool names (canonical, used throughout koan codebase)
CLAUDE_TOOLS = {"Bash", "Read", "Write", "Glob", "Grep", "Edit"}

# Mapping from Kōan canonical tool names to OpenAI-style function names.
# Used by Copilot provider (--allow-tool) and local LLM runner (function calling).
TOOL_NAME_MAP = {
    "Bash": "shell",
    "Read": "read_file",
    "Write": "write_file",
    "Edit": "edit_file",
    "Glob": "glob",
    "Grep": "grep",
}


# ---------------------------------------------------------------------------
# Base class
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

    def shell_command(self) -> str:
        """Return the full command prefix for shell scripts.

        Defaults to binary(), but providers that need a multi-word command
        (e.g., "gh copilot") should override this.
        """
        return self.binary()

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

    def build_plugin_args(self, plugin_dirs: Optional[List[str]] = None) -> List[str]:
        """Build args for plugin directory loading.

        Args:
            plugin_dirs: Paths to plugin directories to load.

        Returns:
            CLI flags list. Base implementation returns empty (not supported).
        """
        return []

    def get_env(self) -> Dict[str, str]:
        """Return extra environment variables for subprocess invocation.

        Providers that need to inject env vars (e.g., ANTHROPIC_BASE_URL
        for proxy routing) override this. The returned dict is merged into
        the subprocess environment by cli_exec.py.

        Default: empty dict (no env modification).
        """
        return {}

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
        plugin_dirs: Optional[List[str]] = None,
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
        cmd.extend(self.build_plugin_args(plugin_dirs))
        return cmd

    def check_quota_available(self, project_path: str, timeout: int = 15) -> Tuple[bool, str]:
        """Probe real API quota with a minimal CLI call.

        Returns (available: bool, error_detail: str).
        Base implementation returns (True, '') — no check needed
        (e.g. local/ollama providers have no quota).
        """
        return True, ""

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
