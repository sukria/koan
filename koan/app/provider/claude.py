"""Claude Code CLI provider implementation."""

from typing import List, Optional

from app.provider.base import CLIProvider


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
