"""Tests for CLI provider abstraction (app.provider package)."""

import os
from unittest.mock import patch, MagicMock

import pytest

from app.cli_provider import (
    ClaudeProvider,
    CopilotProvider,
    LocalLLMProvider,
    OllamaClaudeProvider,
    get_provider,
    get_provider_name,
    get_cli_binary,
    reset_provider,
    build_cli_flags,
    build_tool_flags,
    build_prompt_flags,
    build_output_flags,
    build_max_turns_flags,
    build_full_command,
    CLAUDE_TOOLS,
    TOOL_NAME_MAP,
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

    def test_import_from_local_module(self):
        from app.provider.local import LocalLLMProvider
        assert LocalLLMProvider.name == "local"

    def test_facade_reexports_local(self):
        from app.cli_provider import LocalLLMProvider as Facade
        from app.provider import LocalLLMProvider as Package
        assert Facade is Package

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
    """Tests for CopilotProvider flag translation.

    CopilotProvider caches shutil.which() at __init__, so tests that need
    specific which() results must create the provider inside the patch.
    """

    def _make(self, which_side_effect=None):
        """Create a CopilotProvider with mocked shutil.which."""
        if which_side_effect is None:
            return CopilotProvider()
        with patch("app.provider.copilot.shutil.which", side_effect=which_side_effect):
            return CopilotProvider()

    @staticmethod
    def _standalone(x):
        return "/usr/local/bin/copilot" if x == "copilot" else None

    @staticmethod
    def _gh_only(x):
        return "/usr/bin/gh" if x == "gh" else None

    def test_name(self):
        assert CopilotProvider.name == "copilot"

    def test_binary_standalone(self):
        """Uses 'copilot' when standalone binary is available."""
        p = self._make(self._standalone)
        assert p.binary() == "copilot"

    def test_binary_gh_fallback(self):
        """Falls back to 'gh' when copilot binary not found."""
        p = self._make(self._gh_only)
        assert p.binary() == "gh"

    def test_shell_command_standalone(self):
        p = self._make(self._standalone)
        assert p.shell_command() == "copilot"

    def test_shell_command_gh_mode(self):
        p = self._make(self._gh_only)
        assert p.shell_command() == "gh copilot"

    def test_is_available_copilot(self):
        p = self._make(self._standalone)
        assert p.is_available()

    def test_is_available_gh(self):
        p = self._make(self._gh_only)
        assert p.is_available()

    def test_not_available(self):
        p = self._make(lambda x: None)
        assert not p.is_available()

    def test_prompt_args_standalone(self):
        p = self._make(self._standalone)
        assert p.build_prompt_args("test") == ["-p", "test"]

    def test_prompt_args_gh_mode(self):
        p = self._make(self._gh_only)
        result = p.build_prompt_args("test")
        assert result == ["copilot", "-p", "test"]

    def test_tool_args_individual(self):
        """Maps Claude tool names to Copilot equivalents."""
        p = self._make()
        result = p.build_tool_args(allowed_tools=["Read", "Grep"])
        assert "--allow-tool" in result
        assert "read_file" in result
        assert "grep" in result

    def test_tool_args_all_tools(self):
        """Uses --allow-all-tools when all canonical tools are requested."""
        p = self._make()
        all_tools = list(CLAUDE_TOOLS)
        result = p.build_tool_args(allowed_tools=all_tools)
        assert "--allow-all-tools" in result

    def test_tool_args_disallowed_inverse(self):
        """Computes inverse set when using disallowed_tools."""
        p = self._make()
        result = p.build_tool_args(disallowed_tools=["Bash", "Edit", "Write"])
        # Should allow the remaining tools: Read, Glob, Grep
        assert "--allow-tool" in result
        tool_names = [result[i + 1] for i in range(len(result)) if result[i] == "--allow-tool"]
        assert set(tool_names) == {"read_file", "glob", "grep"}

    def test_model_args(self):
        p = self._make()
        result = p.build_model_args(model="opus", fallback="sonnet")
        assert result == ["--model", "opus"]
        # No --fallback-model for copilot

    def test_model_args_empty(self):
        p = self._make()
        assert p.build_model_args() == []

    def test_output_args_json(self):
        p = self._make()
        # Copilot doesn't support --json, should return empty
        assert p.build_output_args("json") == []

    def test_output_args_empty(self):
        p = self._make()
        assert p.build_output_args() == []

    def test_max_turns_args(self):
        p = self._make()
        # Copilot doesn't support --max-turns, should return empty
        assert p.build_max_turns_args(3) == []
        assert p.build_max_turns_args(0) == []

    def test_mcp_args(self):
        p = self._make()
        result = p.build_mcp_args(["config.json"])
        assert result == ["--mcp-config", "config.json"]

    def test_build_command_gh_mode(self):
        """Full command in gh mode includes 'copilot' subcommand."""
        p = self._make(self._gh_only)
        cmd = p.build_command(
            prompt="hello",
            allowed_tools=["Read"],
            max_turns=1,
        )
        assert cmd[0] == "gh"
        assert "copilot" in cmd
        assert "-p" in cmd
        assert "--allow-tool" in cmd
        assert "read_file" in cmd

    def test_build_command_standalone(self):
        p = self._make(self._standalone)
        cmd = p.build_command(prompt="hello")
        assert cmd[0] == "copilot"
        assert "copilot" not in cmd[1:]  # No redundant 'copilot' subcommand


# ---------------------------------------------------------------------------
# Tool name mapping
# ---------------------------------------------------------------------------

class TestToolMapping:
    """Verify tool name mapping between providers."""

    def test_all_claude_tools_mapped(self):
        for tool in CLAUDE_TOOLS:
            assert tool in TOOL_NAME_MAP

    def test_mapping_values(self):
        assert TOOL_NAME_MAP["Bash"] == "shell"
        assert TOOL_NAME_MAP["Read"] == "read_file"
        assert TOOL_NAME_MAP["Write"] == "write_file"
        assert TOOL_NAME_MAP["Edit"] == "edit_file"
        assert TOOL_NAME_MAP["Glob"] == "glob"
        assert TOOL_NAME_MAP["Grep"] == "grep"


# ---------------------------------------------------------------------------
# Provider resolution
# ---------------------------------------------------------------------------

class TestProviderResolution:
    """Tests for get_provider_name() and get_provider()."""

    def setup_method(self):
        reset_provider()

    def teardown_method(self):
        reset_provider()

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

    @patch.dict("os.environ", {"KOAN_CLI_PROVIDER": "claude"})
    def test_get_provider_caches_instance(self):
        """Same provider name returns the same cached instance."""
        p1 = get_provider()
        p2 = get_provider()
        assert p1 is p2

    def test_get_provider_invalidates_on_name_change(self, monkeypatch):
        """Changing provider name returns a new instance."""
        monkeypatch.setenv("KOAN_CLI_PROVIDER", "claude")
        p1 = get_provider()
        reset_provider()
        monkeypatch.setenv("KOAN_CLI_PROVIDER", "copilot")
        p2 = get_provider()
        assert type(p1) is not type(p2)

    @patch.dict("os.environ", {"CLI_PROVIDER": "copilot"}, clear=True)
    @patch("app.utils.load_config", return_value={})
    def test_fallback_to_cli_provider(self, mock_config, capsys):
        """CLI_PROVIDER fallback works when KOAN_CLI_PROVIDER is not set."""
        # Import after patching env to reset the warning flag
        import app.utils
        app.utils._cli_provider_warned = False

        assert get_provider_name() == "copilot"
        captured = capsys.readouterr()
        assert "CLI_PROVIDER is deprecated" in captured.out

    @patch.dict("os.environ", {"KOAN_CLI_PROVIDER": "claude", "CLI_PROVIDER": "copilot"})
    def test_koan_cli_provider_takes_priority(self, capsys):
        """KOAN_CLI_PROVIDER takes priority over CLI_PROVIDER."""
        import app.utils
        app.utils._cli_provider_warned = False

        assert get_provider_name() == "claude"
        captured = capsys.readouterr()
        # Should not warn since KOAN_CLI_PROVIDER is set
        assert "deprecated" not in captured.out

    @patch.dict("os.environ", {"CLI_PROVIDER": "claude"}, clear=True)
    @patch("app.utils.load_config", return_value={})
    def test_fallback_warning_only_once(self, mock_config, capsys):
        """Deprecation warning is shown only once per process."""
        import app.utils
        app.utils._cli_provider_warned = False

        # First call should warn
        get_provider_name()
        captured1 = capsys.readouterr()
        assert "CLI_PROVIDER is deprecated" in captured1.out

        # Second call should not warn
        reset_provider()
        get_provider_name()
        captured2 = capsys.readouterr()
        assert "deprecated" not in captured2.out

    @patch.dict("os.environ", {}, clear=True)
    @patch("app.utils.load_config", return_value={})
    def test_empty_when_neither_set(self, mock_config):
        """Falls back to default when neither env var is set."""
        import os
        os.environ.pop("KOAN_CLI_PROVIDER", None)
        os.environ.pop("CLI_PROVIDER", None)
        assert get_provider_name() == "claude"


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------

class TestConvenienceFunctions:
    """Tests for module-level helper functions."""

    def setup_method(self):
        reset_provider()

    def teardown_method(self):
        reset_provider()

    @patch.dict("os.environ", {"KOAN_CLI_PROVIDER": "claude"})
    def test_get_cli_binary_claude(self):
        assert get_cli_binary() == "claude"

    @patch.dict("os.environ", {"KOAN_CLI_PROVIDER": "copilot"})
    @patch("app.provider.copilot.shutil.which")
    def test_get_cli_binary_copilot_standalone(self, mock_which):
        mock_which.side_effect = lambda x: "/usr/local/bin/copilot" if x == "copilot" else None
        assert get_cli_binary() == "copilot"

    @patch.dict("os.environ", {"KOAN_CLI_PROVIDER": "copilot"})
    @patch("app.provider.copilot.shutil.which")
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
        # Copilot doesn't support --json, returns empty
        assert build_output_flags("json") == []

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
    @patch("app.provider.copilot.shutil.which")
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
        # Copilot doesn't support --json or --max-turns
        assert "--json" not in cmd
        assert "--max-turns" not in cmd


# ---------------------------------------------------------------------------
# LocalLLMProvider
# ---------------------------------------------------------------------------

class TestLocalLLMProvider:
    """Tests for LocalLLMProvider flag generation."""

    def setup_method(self):
        self.provider = LocalLLMProvider()

    def test_name(self):
        assert self.provider.name == "local"

    def test_binary_is_python(self):
        import sys
        assert self.provider.binary() == sys.executable

    def test_prompt_args(self):
        result = self.provider.build_prompt_args("do the thing")
        assert "-m" in result
        assert "app.local_llm_runner" in result
        assert "-p" in result
        assert "do the thing" in result

    def test_tool_args_allowed(self):
        result = self.provider.build_tool_args(allowed_tools=["Bash", "Read"])
        assert result == ["--allowed-tools", "Bash,Read"]

    def test_tool_args_disallowed(self):
        result = self.provider.build_tool_args(disallowed_tools=["Bash", "Edit"])
        assert result == ["--disallowed-tools", "Bash,Edit"]

    def test_tool_args_empty(self):
        assert self.provider.build_tool_args() == []

    @patch("app.utils.load_config", return_value={"local_llm": {"model": "glm4"}})
    def test_model_args_from_config(self, mock_config):
        """Uses model from config when no explicit model given."""
        result = self.provider.build_model_args()
        assert result == ["--model", "glm4"]

    def test_model_args_explicit(self):
        result = self.provider.build_model_args(model="nemotron-nano")
        assert result == ["--model", "nemotron-nano"]

    def test_model_args_fallback_ignored(self):
        result = self.provider.build_model_args(model="glm4", fallback="sonnet")
        assert result == ["--model", "glm4"]
        assert "--fallback" not in " ".join(result)

    def test_output_args_json(self):
        assert self.provider.build_output_args("json") == ["--output-format", "json"]

    def test_output_args_empty(self):
        assert self.provider.build_output_args() == []

    def test_max_turns_args(self):
        assert self.provider.build_max_turns_args(5) == ["--max-turns", "5"]

    def test_max_turns_args_zero(self):
        assert self.provider.build_max_turns_args(0) == []

    def test_mcp_args_ignored(self):
        """MCP not supported — always returns empty."""
        assert self.provider.build_mcp_args(["config.json"]) == []
        assert self.provider.build_mcp_args() == []

    @patch.dict("os.environ", {
        "KOAN_LOCAL_LLM_BASE_URL": "http://myserver:8080/v1",
        "KOAN_LOCAL_LLM_MODEL": "test-model",
    })
    def test_build_command_full(self):
        import sys
        cmd = self.provider.build_command(
            prompt="analyze code",
            allowed_tools=["Read", "Grep"],
            model="glm4",
            output_format="json",
            max_turns=3,
        )
        assert cmd[0] == sys.executable
        assert "-m" in cmd
        assert "app.local_llm_runner" in cmd
        assert "-p" in cmd
        assert "analyze code" in cmd
        assert "--allowed-tools" in cmd
        assert "Read,Grep" in cmd
        assert "--model" in cmd
        assert "glm4" in cmd
        assert "--output-format" in cmd
        assert "json" in cmd
        assert "--max-turns" in cmd
        assert "3" in cmd
        assert "--base-url" in cmd
        assert "http://myserver:8080/v1" in cmd

    @patch("app.utils.load_config", return_value={"local_llm": {"model": "test"}})
    def test_build_command_minimal(self, mock_config):
        import sys
        cmd = self.provider.build_command(prompt="hello")
        assert cmd[0] == sys.executable
        assert "-p" in cmd
        assert "hello" in cmd
        assert "--base-url" in cmd

    @patch.dict("os.environ", {"KOAN_LOCAL_LLM_BASE_URL": "http://custom:1234/v1"})
    def test_base_url_from_env(self):
        assert self.provider._get_base_url() == "http://custom:1234/v1"

    @patch.dict("os.environ", {}, clear=False)
    @patch("app.utils.load_config", return_value={"local_llm": {"base_url": "http://cfg:5555/v1"}})
    def test_base_url_from_config(self, mock_config):
        os.environ.pop("KOAN_LOCAL_LLM_BASE_URL", None)
        assert self.provider._get_base_url() == "http://cfg:5555/v1"

    @patch.dict("os.environ", {}, clear=False)
    @patch("app.utils.load_config", return_value={})
    def test_base_url_default(self, mock_config):
        os.environ.pop("KOAN_LOCAL_LLM_BASE_URL", None)
        assert self.provider._get_base_url() == "http://localhost:11434/v1"

    @patch.dict("os.environ", {"KOAN_LOCAL_LLM_MODEL": "env-model"})
    def test_model_from_env(self):
        assert self.provider._get_default_model() == "env-model"

    @patch.dict("os.environ", {}, clear=False)
    @patch("app.utils.load_config", return_value={"local_llm": {"model": "cfg-model"}})
    def test_model_from_config(self, mock_config):
        os.environ.pop("KOAN_LOCAL_LLM_MODEL", None)
        assert self.provider._get_default_model() == "cfg-model"

    @patch.dict("os.environ", {"KOAN_LOCAL_LLM_MODEL": "some-model"})
    def test_is_available_with_model(self):
        assert self.provider.is_available()

    @patch.dict("os.environ", {}, clear=False)
    @patch("app.utils.load_config", return_value={})
    def test_not_available_without_model(self, mock_config):
        os.environ.pop("KOAN_LOCAL_LLM_MODEL", None)
        assert not self.provider.is_available()

    @patch.dict("os.environ", {"KOAN_LOCAL_LLM_API_KEY": "sk-test"})
    def test_api_key_from_env(self):
        assert self.provider._get_api_key() == "sk-test"

    @patch.dict("os.environ", {
        "KOAN_LOCAL_LLM_API_KEY": "sk-test",
        "KOAN_LOCAL_LLM_BASE_URL": "http://localhost:11434/v1",
        "KOAN_LOCAL_LLM_MODEL": "test",
    })
    def test_build_command_with_api_key(self):
        cmd = self.provider.build_command(prompt="test", model="test")
        assert "--api-key" in cmd
        assert "sk-test" in cmd

    def test_extra_flags(self):
        result = self.provider.build_extra_flags(
            model="glm4", disallowed_tools=["Bash"]
        )
        assert "--model" in result
        assert "glm4" in result
        assert "--disallowed-tools" in result


# ---------------------------------------------------------------------------
# Provider resolution with local provider
# ---------------------------------------------------------------------------

class TestLocalProviderResolution:
    """Tests for provider resolution with local LLM provider."""

    def setup_method(self):
        reset_provider()

    def teardown_method(self):
        reset_provider()

    @patch.dict("os.environ", {"KOAN_CLI_PROVIDER": "local"})
    def test_env_var_local(self):
        assert get_provider_name() == "local"

    @patch.dict("os.environ", {"KOAN_CLI_PROVIDER": "local"})
    def test_get_provider_returns_local(self):
        provider = get_provider()
        assert isinstance(provider, LocalLLMProvider)

    @patch.dict("os.environ", {"KOAN_CLI_PROVIDER": "local"})
    def test_get_cli_binary_local(self):
        import sys
        binary = get_cli_binary()
        assert sys.executable in binary

    @patch.dict("os.environ", {}, clear=False)
    @patch("app.utils.load_config", return_value={"cli_provider": "local"})
    def test_config_yaml_local(self, mock_config):
        os.environ.pop("KOAN_CLI_PROVIDER", None)
        assert get_provider_name() == "local"

    @patch.dict("os.environ", {
        "KOAN_CLI_PROVIDER": "local",
        "KOAN_LOCAL_LLM_BASE_URL": "http://localhost:11434/v1",
        "KOAN_LOCAL_LLM_MODEL": "glm4",
    })
    def test_build_full_command_local(self):
        import sys
        cmd = build_full_command(
            prompt="hello",
            allowed_tools=["Read", "Grep"],
            model="glm4",
            max_turns=3,
            output_format="json",
        )
        assert cmd[0] == sys.executable
        assert "-m" in cmd
        assert "app.local_llm_runner" in cmd
        assert "--allowed-tools" in cmd
        assert "--output-format" in cmd


# ---------------------------------------------------------------------------
# OllamaClaudeProvider
# ---------------------------------------------------------------------------

# Default env for a valid ollama-claude configuration
_OLLAMA_CLAUDE_ENV = {
    "KOAN_OLLAMA_CLAUDE_BASE_URL": "http://localhost:11434",
    "KOAN_OLLAMA_CLAUDE_MODEL": "llama3.3",
}


class TestOllamaClaudeProvider:
    """Tests for OllamaClaudeProvider flag generation and env injection.

    get_env() reads env vars at call time (not init), so tests that
    check get_env() output must keep the env vars active.
    """

    def setup_method(self):
        reset_provider()

    def teardown_method(self):
        reset_provider()

    @patch.dict("os.environ", _OLLAMA_CLAUDE_ENV)
    def test_name(self):
        p = OllamaClaudeProvider()
        assert p.name == "ollama-claude"

    @patch.dict("os.environ", _OLLAMA_CLAUDE_ENV)
    def test_binary(self):
        p = OllamaClaudeProvider()
        assert p.binary() == "claude"

    @patch.dict("os.environ", _OLLAMA_CLAUDE_ENV)
    def test_inherits_claude_prompt_args(self):
        p = OllamaClaudeProvider()
        assert p.build_prompt_args("hello world") == ["-p", "hello world"]

    @patch.dict("os.environ", _OLLAMA_CLAUDE_ENV)
    def test_inherits_claude_tool_args_allowed(self):
        p = OllamaClaudeProvider()
        result = p.build_tool_args(allowed_tools=["Bash", "Read"])
        assert result == ["--allowedTools", "Bash,Read"]

    @patch.dict("os.environ", _OLLAMA_CLAUDE_ENV)
    def test_inherits_claude_tool_args_disallowed(self):
        p = OllamaClaudeProvider()
        result = p.build_tool_args(disallowed_tools=["Bash", "Edit"])
        assert result == ["--disallowedTools", "Bash", "Edit"]

    @patch.dict("os.environ", _OLLAMA_CLAUDE_ENV)
    def test_inherits_claude_model_args(self):
        p = OllamaClaudeProvider()
        result = p.build_model_args(model="opus", fallback="sonnet")
        assert result == ["--model", "opus", "--fallback-model", "sonnet"]

    @patch.dict("os.environ", _OLLAMA_CLAUDE_ENV)
    def test_inherits_claude_output_args(self):
        p = OllamaClaudeProvider()
        assert p.build_output_args("json") == ["--output-format", "json"]

    @patch.dict("os.environ", _OLLAMA_CLAUDE_ENV)
    def test_inherits_claude_max_turns_args(self):
        p = OllamaClaudeProvider()
        assert p.build_max_turns_args(5) == ["--max-turns", "5"]

    @patch.dict("os.environ", _OLLAMA_CLAUDE_ENV)
    def test_inherits_claude_mcp_args(self):
        p = OllamaClaudeProvider()
        result = p.build_mcp_args(["mcp.json"])
        assert result == ["--mcp-config", "mcp.json"]

    @patch.dict("os.environ", _OLLAMA_CLAUDE_ENV)
    def test_build_command_full(self):
        p = OllamaClaudeProvider()
        cmd = p.build_command(
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

    @patch.dict("os.environ", _OLLAMA_CLAUDE_ENV)
    def test_build_command_minimal(self):
        p = OllamaClaudeProvider()
        cmd = p.build_command(prompt="hello")
        assert cmd == ["claude", "-p", "hello"]

    # --- get_env() ---

    @patch.dict("os.environ", _OLLAMA_CLAUDE_ENV)
    def test_get_env_returns_correct_dict(self):
        p = OllamaClaudeProvider()
        env = p.get_env()
        assert env["ANTHROPIC_BASE_URL"] == "http://localhost:11434"
        assert env["ANTHROPIC_API_KEY"] == "ollama"
        assert env["ANTHROPIC_MODEL"] == "llama3.3"

    @patch.dict("os.environ", _OLLAMA_CLAUDE_ENV)
    def test_get_env_default_api_key(self):
        """Default API key is 'ollama' when not explicitly set."""
        p = OllamaClaudeProvider()
        assert p.get_env()["ANTHROPIC_API_KEY"] == "ollama"

    @patch.dict("os.environ", {**_OLLAMA_CLAUDE_ENV, "KOAN_OLLAMA_CLAUDE_API_KEY": "sk-custom"})
    def test_get_env_custom_api_key(self):
        p = OllamaClaudeProvider()
        assert p.get_env()["ANTHROPIC_API_KEY"] == "sk-custom"

    @patch.dict("os.environ", {**_OLLAMA_CLAUDE_ENV, "KOAN_OLLAMA_CLAUDE_AUTH_TOKEN": "bearer-token"})
    def test_get_env_auth_token_when_set(self):
        p = OllamaClaudeProvider()
        env = p.get_env()
        assert env["ANTHROPIC_AUTH_TOKEN"] == "bearer-token"

    @patch.dict("os.environ", _OLLAMA_CLAUDE_ENV)
    def test_get_env_no_auth_token_by_default(self):
        p = OllamaClaudeProvider()
        assert "ANTHROPIC_AUTH_TOKEN" not in p.get_env()

    @patch.dict("os.environ", {**_OLLAMA_CLAUDE_ENV, "KOAN_OLLAMA_CLAUDE_HAIKU_MODEL": "phi3"})
    def test_get_env_haiku_model_when_set(self):
        p = OllamaClaudeProvider()
        env = p.get_env()
        assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "phi3"

    @patch.dict("os.environ", _OLLAMA_CLAUDE_ENV)
    def test_get_env_no_haiku_model_by_default(self):
        p = OllamaClaudeProvider()
        assert "ANTHROPIC_DEFAULT_HAIKU_MODEL" not in p.get_env()

    @patch.dict("os.environ", {**_OLLAMA_CLAUDE_ENV, "KOAN_OLLAMA_CLAUDE_SONNET_MODEL": "mistral"})
    def test_get_env_sonnet_model_when_set(self):
        p = OllamaClaudeProvider()
        env = p.get_env()
        assert env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "mistral"

    @patch.dict("os.environ", _OLLAMA_CLAUDE_ENV)
    def test_get_env_no_sonnet_model_by_default(self):
        p = OllamaClaudeProvider()
        assert "ANTHROPIC_DEFAULT_SONNET_MODEL" not in p.get_env()

    # --- env var overrides config ---

    @patch("app.utils.load_config", return_value={
        "ollama_claude": {
            "base_url": "http://config:1234",
            "model": "config-model",
            "api_key": "config-key",
        }
    })
    @patch.dict("os.environ", {
        "KOAN_OLLAMA_CLAUDE_BASE_URL": "http://env:5678",
        "KOAN_OLLAMA_CLAUDE_MODEL": "env-model",
        "KOAN_OLLAMA_CLAUDE_API_KEY": "env-key",
    })
    def test_env_var_overrides_config(self, _mock_config):
        """Env vars take precedence over config.yaml values."""
        p = OllamaClaudeProvider()
        env = p.get_env()
        assert env["ANTHROPIC_BASE_URL"] == "http://env:5678"
        assert env["ANTHROPIC_MODEL"] == "env-model"
        assert env["ANTHROPIC_API_KEY"] == "env-key"

    @patch("app.utils.load_config", return_value={
        "ollama_claude": {
            "base_url": "http://config:1234",
            "model": "config-model",
        }
    })
    def test_config_values_when_no_env(self, _mock_config):
        """Config values are used when env vars are not set."""
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("KOAN_OLLAMA_CLAUDE_BASE_URL", None)
            os.environ.pop("KOAN_OLLAMA_CLAUDE_MODEL", None)
            os.environ.pop("KOAN_OLLAMA_CLAUDE_API_KEY", None)
            p = OllamaClaudeProvider()
            env = p.get_env()
            assert env["ANTHROPIC_BASE_URL"] == "http://config:1234"
            assert env["ANTHROPIC_MODEL"] == "config-model"

    # --- Early validation ---

    def test_raises_on_missing_base_url(self):
        with pytest.raises(ValueError, match="base_url"):
            with patch.dict("os.environ", {"KOAN_OLLAMA_CLAUDE_MODEL": "llama3.3"}, clear=False):
                os.environ.pop("KOAN_OLLAMA_CLAUDE_BASE_URL", None)
                with patch("app.utils.load_config", return_value={}):
                    OllamaClaudeProvider()

    def test_raises_on_missing_model(self):
        with pytest.raises(ValueError, match="model"):
            with patch.dict("os.environ", {"KOAN_OLLAMA_CLAUDE_BASE_URL": "http://localhost:11434"}, clear=False):
                os.environ.pop("KOAN_OLLAMA_CLAUDE_MODEL", None)
                with patch("app.utils.load_config", return_value={}):
                    OllamaClaudeProvider()

    # --- check_quota_available ---

    @patch.dict("os.environ", _OLLAMA_CLAUDE_ENV)
    def test_check_quota_always_available(self):
        p = OllamaClaudeProvider()
        available, detail = p.check_quota_available("/tmp")
        assert available is True
        assert detail == ""

    # --- is_available ---

    @patch("shutil.which", return_value="/usr/bin/claude")
    @patch.dict("os.environ", _OLLAMA_CLAUDE_ENV)
    def test_is_available_when_configured(self, _mock_which):
        p = OllamaClaudeProvider()
        assert p.is_available()

    @patch("shutil.which", return_value=None)
    @patch.dict("os.environ", _OLLAMA_CLAUDE_ENV)
    def test_not_available_without_binary(self, _mock_which):
        p = OllamaClaudeProvider()
        assert not p.is_available()

    @patch("shutil.which", return_value="/usr/bin/claude")
    @patch("app.utils.load_config", return_value={})
    @patch.dict("os.environ", _OLLAMA_CLAUDE_ENV)
    def test_not_available_without_config(self, _mock_config, _mock_which):
        """Not available if base_url or model are missing (after init)."""
        p = OllamaClaudeProvider()
        # Remove env vars to simulate missing config post-init
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("KOAN_OLLAMA_CLAUDE_BASE_URL", None)
            os.environ.pop("KOAN_OLLAMA_CLAUDE_MODEL", None)
            assert not p.is_available()

    # --- Extra flags (inherited) ---

    @patch.dict("os.environ", _OLLAMA_CLAUDE_ENV)
    def test_extra_flags(self):
        p = OllamaClaudeProvider()
        result = p.build_extra_flags(
            model="opus", fallback="sonnet", disallowed_tools=["Bash"]
        )
        assert "--model" in result
        assert "--fallback-model" in result
        assert "--disallowedTools" in result

    # --- Base class get_env() default ---

    def test_base_class_get_env_empty(self):
        """CLIProvider.get_env() returns empty dict."""
        from app.provider.base import CLIProvider
        p = CLIProvider()
        assert p.get_env() == {}

    def test_claude_provider_get_env_empty(self):
        """ClaudeProvider.get_env() returns empty dict (inherited default)."""
        p = ClaudeProvider()
        assert p.get_env() == {}

    def test_local_provider_get_env_empty(self):
        """LocalLLMProvider.get_env() returns empty dict (inherited default)."""
        p = LocalLLMProvider()
        assert p.get_env() == {}


# ---------------------------------------------------------------------------
# Provider resolution with ollama-claude
# ---------------------------------------------------------------------------

class TestOllamaClaudeProviderResolution:
    """Tests for provider resolution with ollama-claude provider."""

    def setup_method(self):
        reset_provider()

    def teardown_method(self):
        reset_provider()

    @patch.dict("os.environ", {
        "KOAN_CLI_PROVIDER": "ollama-claude",
        "KOAN_OLLAMA_CLAUDE_BASE_URL": "http://localhost:11434",
        "KOAN_OLLAMA_CLAUDE_MODEL": "llama3.3",
    })
    def test_env_var_resolves(self):
        assert get_provider_name() == "ollama-claude"

    @patch.dict("os.environ", {
        "KOAN_CLI_PROVIDER": "ollama-claude",
        "KOAN_OLLAMA_CLAUDE_BASE_URL": "http://localhost:11434",
        "KOAN_OLLAMA_CLAUDE_MODEL": "llama3.3",
    })
    def test_get_provider_returns_ollama_claude(self):
        provider = get_provider()
        assert isinstance(provider, OllamaClaudeProvider)

    @patch.dict("os.environ", {
        "KOAN_OLLAMA_CLAUDE_BASE_URL": "http://localhost:11434",
        "KOAN_OLLAMA_CLAUDE_MODEL": "llama3.3",
    }, clear=False)
    @patch("app.utils.load_config", return_value={"cli_provider": "ollama-claude"})
    def test_config_yaml_resolves(self, _mock_config):
        os.environ.pop("KOAN_CLI_PROVIDER", None)
        assert get_provider_name() == "ollama-claude"

    @patch.dict("os.environ", {
        "KOAN_CLI_PROVIDER": "ollama-claude",
        "KOAN_OLLAMA_CLAUDE_BASE_URL": "http://localhost:11434",
        "KOAN_OLLAMA_CLAUDE_MODEL": "llama3.3",
    })
    def test_build_full_command_ollama_claude(self):
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
        assert "--max-turns" in cmd

    # --- Package structure ---

    def test_import_from_provider_module(self):
        from app.provider.ollama_claude import OllamaClaudeProvider
        assert OllamaClaudeProvider.name == "ollama-claude"

    def test_facade_reexports_ollama_claude(self):
        from app.cli_provider import OllamaClaudeProvider as Facade
        from app.provider import OllamaClaudeProvider as Package
        assert Facade is Package
