"""GitHub Copilot CLI provider implementation."""

import shutil
from typing import List, Optional

from app.provider.base import CLIProvider, CLAUDE_TOOLS, _CLAUDE_TO_COPILOT_TOOLS


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
            # Check if all canonical tools are allowed -> use --allow-all-tools
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
