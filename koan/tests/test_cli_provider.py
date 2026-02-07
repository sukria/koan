"""Tests for CLI provider abstraction (app.provider package)."""

from unittest.mock import patch, MagicMock

import pytest

from app.cli_provider import (
    ClaudeProvider,
    CopilotProvider,
    get_provider,
    get_provider_name,
    get_cli_binary,
    build_cli_flags,
    build_tool_flags,
    build_prompt_flags,
    build_output_flags,
    build_max_turns_flags,
    build_full_command,
    CLAUDE_TOOLS,
    _CLAUDE_TO_COPILOT_TOOLS,
)


# ---------------------------------------------------------------------------
# Package structure
# ---------------------------------------------------------------------------

class TestPackageStructure:
    """Verify the provider package is properly split and re-exports work."""

    def test_import_from_provider_package(self):
        from app.provider import ClaudeProvider, CopilotProvider, CLIProvider
        assert ClaudeProvider.name == "claude"
        assert CopilotProvider.name == "copilot"

    def test_import_from_base(self):
        from app.provider.base import CLIProvider, CLAUDE_TOOLS
        assert "Bash" in CLAUDE_TOOLS
        assert hasattr(CLIProvider, "build_command")

    def test_import_from_claude_module(self):
        from app.provider.claude import ClaudeProvider
        assert ClaudeProvider().binary() == "claude"

    def test_import_from_copilot_module(self):
        from app.provider.copilot import CopilotProvider
        assert CopilotProvider.name == "copilot"

    def test_facade_reexports_same_objects(self):
        """cli_provider.py re-exports are identical to provider package objects."""
        from app.cli_provider import ClaudeProvider as Facade
        from app.provider import ClaudeProvider as Package
        assert Facade is Package

    def test_base_class_is_same(self):
        from app.cli_provider import CLIProvider as Facade
        from app.provider.base import CLIProvider as Base
        assert Facade is Base


# ---------------------------------------------------------------------------
# ClaudeProvider
# ---------------------------------------------------------------------------

class TestClaudeProvider:
    """Tests for ClaudeProvider flag generation."""

    def setup_method(self):
        self.provider = ClaudeProvider()

    def test_binary(self):
        assert self.provider.binary() == "claude"

    def test_name(self):
        assert self.provider.name == "claude"

    def test_prompt_args(self):
        assert self.provider.build_prompt_args("hello world") == ["-p", "hello world"]

    def test_tool_args_allowed(self):
        result = self.provider.build_tool_args(allowed_tools=["Bash", "Read"])
        assert result == ["--allowedTools", "Bash,Read"]

    def test_tool_args_disallowed(self):
        result = self.provider.build_tool_args(disallowed_tools=["Bash", "Edit", "Write"])
        assert result == ["--disallowedTools", "Bash", "Edit", "Write"]

    def test_tool_args_empty(self):
        assert self.provider.build_tool_args() == []

    def test_model_args(self):
        result = self.provider.build_model_args(model="opus", fallback="sonnet")
        assert result == ["--model", "opus", "--fallback-model", "sonnet"]

    def test_model_args_empty(self):
        assert self.provider.build_model_args() == []

    def test_model_args_partial(self):
        assert self.provider.build_model_args(model="haiku") == ["--model", "haiku"]
        assert self.provider.build_model_args(fallback="sonnet") == ["--fallback-model", "sonnet"]

    def test_output_args_json(self):
        assert self.provider.build_output_args("json") == ["--output-format", "json"]

    def test_output_args_empty(self):
        assert self.provider.build_output_args() == []

    def test_max_turns_args(self):
        assert self.provider.build_max_turns_args(3) == ["--max-turns", "3"]

    def test_max_turns_args_zero(self):
        assert self.provider.build_max_turns_args(0) == []

    def test_mcp_args(self):
        result = self.provider.build_mcp_args(["config1.json", "config2.json"])
        assert result == ["--mcp-config", "config1.json", "config2.json"]

    def test_mcp_args_empty(self):
        assert self.provider.build_mcp_args() == []
        assert self.provider.build_mcp_args([]) == []

    def test_build_command_full(self):
        cmd = self.provider.build_command(
            prompt="do the thing",
            allowed_tools=["Bash", "Read"],
            model="opus",
            fallback="sonnet",
            output_format="json",
            max_turns=5,
            mcp_configs=["mcp.json"],
        )
        assert cmd[0] == "claude"
        assert "-p" in cmd
        assert "do the thing" in cmd
        assert "--allowedTools" in cmd
        assert "--model" in cmd
        assert "opus" in cmd
        assert "--fallback-model" in cmd
        assert "--output-format" in cmd
        assert "--max-turns" in cmd
        assert "--mcp-config" in cmd

    def test_build_command_minimal(self):
        cmd = self.provider.build_command(prompt="hello")
        assert cmd == ["claude", "-p", "hello"]

    def test_extra_flags(self):
        result = self.provider.build_extra_flags(
            model="opus", fallback="sonnet", disallowed_tools=["Bash"]
        )
        assert "--model" in result
        assert "--fallback-model" in result
        assert "--disallowedTools" in result


# ---------------------------------------------------------------------------
# CopilotProvider
# ---------------------------------------------------------------------------

class TestCopilotProvider:
    """Tests for CopilotProvider flag translation."""

    def setup_method(self):
        self.provider = CopilotProvider()

    def test_name(self):
        assert self.provider.name == "copilot"

    @patch("shutil.which")
    def test_binary_standalone(self, mock_which):
        """Uses 'copilot' when standalone binary is available."""
        mock_which.side_effect = lambda x: "/usr/local/bin/copilot" if x == "copilot" else None
        assert self.provider.binary() == "copilot"

    @patch("shutil.which")
    def test_binary_gh_fallback(self, mock_which):
        """Falls back to 'gh' when copilot binary not found."""
        mock_which.side_effect = lambda x: "/usr/bin/gh" if x == "gh" else None
        assert self.provider.binary() == "gh"

    @patch("shutil.which")
    def test_is_available_copilot(self, mock_which):
        mock_which.side_effect = lambda x: "/usr/local/bin/copilot" if x == "copilot" else None
        assert self.provider.is_available()

    @patch("shutil.which")
    def test_is_available_gh(self, mock_which):
        mock_which.side_effect = lambda x: "/usr/bin/gh" if x == "gh" else None
        assert self.provider.is_available()

    @patch("shutil.which", return_value=None)
    def test_not_available(self, mock_which):
        assert not self.provider.is_available()

    @patch("shutil.which")
    def test_prompt_args_standalone(self, mock_which):
        mock_which.side_effect = lambda x: "/usr/local/bin/copilot" if x == "copilot" else None
        assert self.provider.build_prompt_args("test") == ["-p", "test"]

    @patch("shutil.which")
    def test_prompt_args_gh_mode(self, mock_which):
        mock_which.side_effect = lambda x: "/usr/bin/gh" if x == "gh" else None
        result = self.provider.build_prompt_args("test")
        assert result == ["copilot", "-p", "test"]

    def test_tool_args_individual(self):
        """Maps Claude tool names to Copilot equivalents."""
        result = self.provider.build_tool_args(allowed_tools=["Read", "Grep"])
        assert "--allow-tool" in result
        assert "read_file" in result
        assert "grep" in result

    def test_tool_args_all_tools(self):
        """Uses --allow-all-tools when all canonical tools are requested."""
        all_tools = list(CLAUDE_TOOLS)
        result = self.provider.build_tool_args(allowed_tools=all_tools)
        assert "--allow-all-tools" in result

    def test_tool_args_disallowed_inverse(self):
        """Computes inverse set when using disallowed_tools."""
        result = self.provider.build_tool_args(disallowed_tools=["Bash", "Edit", "Write"])
        # Should allow the remaining tools: Read, Glob, Grep
        assert "--allow-tool" in result
        tool_names = [result[i + 1] for i in range(len(result)) if result[i] == "--allow-tool"]
        assert set(tool_names) == {"read_file", "glob", "grep"}

    def test_model_args(self):
        result = self.provider.build_model_args(model="opus", fallback="sonnet")
        assert result == ["--model", "opus"]
        # No --fallback-model for copilot

    def test_model_args_empty(self):
        assert self.provider.build_model_args() == []

    def test_output_args_json(self):
        assert self.provider.build_output_args("json") == ["--json"]

    def test_output_args_empty(self):
        assert self.provider.build_output_args() == []

    def test_max_turns_args(self):
        assert self.provider.build_max_turns_args(3) == ["--max-turns", "3"]

    def test_mcp_args(self):
        result = self.provider.build_mcp_args(["config.json"])
        assert result == ["--mcp-config", "config.json"]

    @patch("shutil.which")
    def test_build_command_gh_mode(self, mock_which):
        """Full command in gh mode includes 'copilot' subcommand."""
        mock_which.side_effect = lambda x: "/usr/bin/gh" if x == "gh" else None
        cmd = self.provider.build_command(
            prompt="hello",
            allowed_tools=["Read"],
            max_turns=1,
        )
        assert cmd[0] == "gh"
        assert "copilot" in cmd
        assert "-p" in cmd
        assert "--allow-tool" in cmd
        assert "read_file" in cmd

    @patch("shutil.which")
    def test_build_command_standalone(self, mock_which):
        mock_which.side_effect = lambda x: "/usr/local/bin/copilot" if x == "copilot" else None
        cmd = self.provider.build_command(prompt="hello")
        assert cmd[0] == "copilot"
        assert "copilot" not in cmd[1:]  # No redundant 'copilot' subcommand


# ---------------------------------------------------------------------------
# Tool name mapping
# ---------------------------------------------------------------------------

class TestToolMapping:
    """Verify tool name mapping between providers."""

    def test_all_claude_tools_mapped(self):
        for tool in CLAUDE_TOOLS:
            assert tool in _CLAUDE_TO_COPILOT_TOOLS

    def test_mapping_values(self):
        assert _CLAUDE_TO_COPILOT_TOOLS["Bash"] == "shell"
        assert _CLAUDE_TO_COPILOT_TOOLS["Read"] == "read_file"
        assert _CLAUDE_TO_COPILOT_TOOLS["Write"] == "write_file"
        assert _CLAUDE_TO_COPILOT_TOOLS["Edit"] == "edit_file"
        assert _CLAUDE_TO_COPILOT_TOOLS["Glob"] == "glob"
        assert _CLAUDE_TO_COPILOT_TOOLS["Grep"] == "grep"


# ---------------------------------------------------------------------------
# Provider resolution
# ---------------------------------------------------------------------------

class TestProviderResolution:
    """Tests for get_provider_name() and get_provider()."""

    @patch.dict("os.environ", {"KOAN_CLI_PROVIDER": "copilot"})
    def test_env_var_override(self):
        assert get_provider_name() == "copilot"

    @patch.dict("os.environ", {"KOAN_CLI_PROVIDER": "claude"})
    def test_env_var_claude(self):
        assert get_provider_name() == "claude"

    @patch.dict("os.environ", {"KOAN_CLI_PROVIDER": "invalid"})
    @patch("app.utils.load_config", return_value={})
    def test_env_var_invalid_falls_to_default(self, mock_config):
        assert get_provider_name() == "claude"

    @patch.dict("os.environ", {}, clear=False)
    @patch("app.utils.load_config", return_value={"cli_provider": "copilot"})
    def test_config_yaml(self, mock_config):
        # Remove env var if present
        import os
        os.environ.pop("KOAN_CLI_PROVIDER", None)
        assert get_provider_name() == "copilot"

    @patch.dict("os.environ", {}, clear=False)
    @patch("app.utils.load_config", return_value={})
    def test_default_claude(self, mock_config):
        import os
        os.environ.pop("KOAN_CLI_PROVIDER", None)
        assert get_provider_name() == "claude"

    @patch.dict("os.environ", {"KOAN_CLI_PROVIDER": "claude"})
    def test_get_provider_returns_claude(self):
        provider = get_provider()
        assert isinstance(provider, ClaudeProvider)

    @patch.dict("os.environ", {"KOAN_CLI_PROVIDER": "copilot"})
    def test_get_provider_returns_copilot(self):
        provider = get_provider()
        assert isinstance(provider, CopilotProvider)


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------

class TestConvenienceFunctions:
    """Tests for module-level helper functions."""

    @patch.dict("os.environ", {"KOAN_CLI_PROVIDER": "claude"})
    def test_get_cli_binary_claude(self):
        assert get_cli_binary() == "claude"

    @patch.dict("os.environ", {"KOAN_CLI_PROVIDER": "copilot"})
    @patch("shutil.which")
    def test_get_cli_binary_copilot_standalone(self, mock_which):
        mock_which.side_effect = lambda x: "/usr/local/bin/copilot" if x == "copilot" else None
        assert get_cli_binary() == "copilot"

    @patch.dict("os.environ", {"KOAN_CLI_PROVIDER": "copilot"})
    @patch("shutil.which")
    def test_get_cli_binary_copilot_gh_mode(self, mock_which):
        mock_which.side_effect = lambda x: "/usr/bin/gh" if x == "gh" else None
        assert get_cli_binary() == "gh copilot"

    @patch.dict("os.environ", {"KOAN_CLI_PROVIDER": "claude"})
    def test_build_cli_flags_claude(self):
        flags = build_cli_flags(model="opus", fallback="sonnet")
        assert flags == ["--model", "opus", "--fallback-model", "sonnet"]

    @patch.dict("os.environ", {"KOAN_CLI_PROVIDER": "copilot"})
    def test_build_cli_flags_copilot(self):
        flags = build_cli_flags(model="opus", fallback="sonnet")
        assert flags == ["--model", "opus"]
        # Copilot ignores fallback

    @patch.dict("os.environ", {"KOAN_CLI_PROVIDER": "claude"})
    def test_build_tool_flags_claude(self):
        flags = build_tool_flags(allowed_tools=["Bash", "Read"])
        assert flags == ["--allowedTools", "Bash,Read"]

    @patch.dict("os.environ", {"KOAN_CLI_PROVIDER": "copilot"})
    def test_build_tool_flags_copilot(self):
        flags = build_tool_flags(allowed_tools=["Bash", "Read"])
        assert "--allow-tool" in flags
        assert "shell" in flags
        assert "read_file" in flags

    @patch.dict("os.environ", {"KOAN_CLI_PROVIDER": "claude"})
    def test_build_output_flags_claude(self):
        assert build_output_flags("json") == ["--output-format", "json"]

    @patch.dict("os.environ", {"KOAN_CLI_PROVIDER": "copilot"})
    def test_build_output_flags_copilot(self):
        assert build_output_flags("json") == ["--json"]

    @patch.dict("os.environ", {"KOAN_CLI_PROVIDER": "claude"})
    def test_build_full_command_claude(self):
        cmd = build_full_command(
            prompt="hello",
            allowed_tools=["Bash", "Read"],
            model="opus",
            max_turns=3,
            output_format="json",
        )
        assert cmd[0] == "claude"
        assert "--allowedTools" in cmd
        assert "--output-format" in cmd

    @patch.dict("os.environ", {"KOAN_CLI_PROVIDER": "copilot"})
    @patch("shutil.which")
    def test_build_full_command_copilot(self, mock_which):
        mock_which.side_effect = lambda x: "/usr/local/bin/copilot" if x == "copilot" else None
        cmd = build_full_command(
            prompt="hello",
            allowed_tools=["Bash", "Read"],
            model="opus",
            max_turns=3,
            output_format="json",
        )
        assert cmd[0] == "copilot"
        assert "--allow-tool" in cmd
        assert "--json" in cmd
