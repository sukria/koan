"""Tests for OllamaClaudeProvider (app.provider.ollama_claude)."""

import os
from unittest.mock import patch, MagicMock

import pytest

from app.provider.ollama_claude import OllamaClaudeProvider
from app.provider import reset_provider, get_provider, get_provider_name, _PROVIDERS
from app.cli_provider import build_full_command


# Helpers
VALID_CONFIG = {
    "ollama_claude": {
        "base_url": "http://localhost:8080",
        "model": "llama3.3",
        "api_key": "test-key",
    }
}


def _mock_config(config=None):
    """Return a patch for load_config returning the given config.

    load_config is lazy-imported inside _get_config() via
    'from app.utils import load_config', so patch at the source module.
    """
    if config is None:
        config = VALID_CONFIG
    return patch("app.utils.load_config", return_value=config)


# ---------------------------------------------------------------------------
# Registry & resolution
# ---------------------------------------------------------------------------

class TestOllamaClaudeRegistry:
    """Provider is registered and resolvable."""

    def setup_method(self):
        reset_provider()

    def teardown_method(self):
        reset_provider()

    def test_registered_in_providers(self):
        assert "ollama-claude" in _PROVIDERS
        assert _PROVIDERS["ollama-claude"] is OllamaClaudeProvider

    @patch.dict("os.environ", {"KOAN_CLI_PROVIDER": "ollama-claude"})
    def test_env_var_resolves_provider(self):
        with _mock_config():
            name = get_provider_name()
            assert name == "ollama-claude"

    @patch.dict("os.environ", {}, clear=False)
    def test_config_resolves_provider(self):
        config = {"cli_provider": "ollama-claude", **VALID_CONFIG}
        with patch("app.utils.load_config", return_value=config), \
             patch("app.utils.get_cli_provider_env", return_value=""):
            name = get_provider_name()
            assert name == "ollama-claude"

    @patch.dict("os.environ", {"KOAN_CLI_PROVIDER": "ollama-claude"})
    def test_get_provider_returns_instance(self):
        with _mock_config():
            provider = get_provider()
            assert isinstance(provider, OllamaClaudeProvider)
            assert provider.name == "ollama-claude"


# ---------------------------------------------------------------------------
# Configuration resolution
# ---------------------------------------------------------------------------

class TestOllamaClaudeConfig:
    """Config resolution: env var > config.yaml > default."""

    def test_base_url_from_config(self):
        with _mock_config():
            p = OllamaClaudeProvider()
            assert p._get_base_url() == "http://localhost:8080"

    def test_base_url_from_env(self):
        with _mock_config(), \
             patch.dict("os.environ", {"KOAN_OLLAMA_CLAUDE_BASE_URL": "http://env:9999"}):
            p = OllamaClaudeProvider()
            assert p._get_base_url() == "http://env:9999"

    def test_model_from_config(self):
        with _mock_config():
            p = OllamaClaudeProvider()
            assert p._get_model() == "llama3.3"

    def test_model_from_env(self):
        with _mock_config(), \
             patch.dict("os.environ", {"KOAN_OLLAMA_CLAUDE_MODEL": "qwen2.5"}):
            p = OllamaClaudeProvider()
            assert p._get_model() == "qwen2.5"

    def test_api_key_default(self):
        config = {"ollama_claude": {"base_url": "http://x", "model": "m"}}
        with _mock_config(config):
            p = OllamaClaudeProvider()
            assert p._get_api_key() == "ollama"

    def test_api_key_from_config(self):
        with _mock_config():
            p = OllamaClaudeProvider()
            assert p._get_api_key() == "test-key"

    def test_api_key_from_env(self):
        with _mock_config(), \
             patch.dict("os.environ", {"KOAN_OLLAMA_CLAUDE_API_KEY": "env-key"}):
            p = OllamaClaudeProvider()
            assert p._get_api_key() == "env-key"

    def test_auth_token_empty_by_default(self):
        with _mock_config():
            p = OllamaClaudeProvider()
            assert p._get_auth_token() == ""

    def test_auth_token_from_config(self):
        config = {"ollama_claude": {
            "base_url": "http://x", "model": "m",
            "auth_token": "bearer-tok",
        }}
        with _mock_config(config):
            p = OllamaClaudeProvider()
            assert p._get_auth_token() == "bearer-tok"

    def test_auth_token_from_env(self):
        with _mock_config(), \
             patch.dict("os.environ", {"KOAN_OLLAMA_CLAUDE_AUTH_TOKEN": "env-tok"}):
            p = OllamaClaudeProvider()
            assert p._get_auth_token() == "env-tok"

    def test_config_section_missing_returns_defaults(self):
        with _mock_config({}):
            p = OllamaClaudeProvider()
            assert p._get_base_url() == ""
            assert p._get_model() == ""
            assert p._get_api_key() == "ollama"

    def test_load_config_exception_returns_empty(self):
        with patch("app.utils.load_config", side_effect=RuntimeError):
            p = OllamaClaudeProvider()
            assert p._get_config() == {}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestOllamaClaudeValidation:
    """Early validation: fail fast on missing required config."""

    def test_validate_raises_on_missing_base_url(self):
        config = {"ollama_claude": {"model": "llama3.3"}}
        with _mock_config(config):
            p = OllamaClaudeProvider()
            with pytest.raises(ValueError, match="base_url"):
                p._validate()

    def test_validate_raises_on_missing_model(self):
        config = {"ollama_claude": {"base_url": "http://x"}}
        with _mock_config(config):
            p = OllamaClaudeProvider()
            with pytest.raises(ValueError, match="model"):
                p._validate()

    def test_validate_raises_on_empty_config(self):
        with _mock_config({}):
            p = OllamaClaudeProvider()
            with pytest.raises(ValueError, match="base_url"):
                p._validate()

    def test_validate_passes_with_valid_config(self):
        with _mock_config():
            p = OllamaClaudeProvider()
            p._validate()  # Should not raise


# ---------------------------------------------------------------------------
# get_env()
# ---------------------------------------------------------------------------

class TestOllamaClaudeGetEnv:
    """Environment variable generation for subprocess injection."""

    def test_basic_env_dict(self):
        with _mock_config():
            p = OllamaClaudeProvider()
            env = p.get_env()
            assert env["ANTHROPIC_BASE_URL"] == "http://localhost:8080"
            assert env["ANTHROPIC_API_KEY"] == "test-key"
            assert env["ANTHROPIC_MODEL"] == "llama3.3"

    def test_auth_token_included_when_set(self):
        config = {"ollama_claude": {
            "base_url": "http://x", "model": "m",
            "auth_token": "tok123",
        }}
        with _mock_config(config):
            p = OllamaClaudeProvider()
            env = p.get_env()
            assert env["ANTHROPIC_AUTH_TOKEN"] == "tok123"

    def test_auth_token_excluded_when_empty(self):
        with _mock_config():
            p = OllamaClaudeProvider()
            env = p.get_env()
            assert "ANTHROPIC_AUTH_TOKEN" not in env

    def test_sonnet_model_included_when_set(self):
        config = {"ollama_claude": {
            "base_url": "http://x", "model": "m",
            "sonnet_model": "my-sonnet",
        }}
        with _mock_config(config):
            p = OllamaClaudeProvider()
            env = p.get_env()
            assert env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "my-sonnet"

    def test_haiku_model_included_when_set(self):
        config = {"ollama_claude": {
            "base_url": "http://x", "model": "m",
            "haiku_model": "my-haiku",
        }}
        with _mock_config(config):
            p = OllamaClaudeProvider()
            env = p.get_env()
            assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "my-haiku"

    def test_optional_models_excluded_when_empty(self):
        with _mock_config():
            p = OllamaClaudeProvider()
            env = p.get_env()
            assert "ANTHROPIC_DEFAULT_SONNET_MODEL" not in env
            assert "ANTHROPIC_DEFAULT_HAIKU_MODEL" not in env

    def test_get_env_raises_on_invalid_config(self):
        with _mock_config({}):
            p = OllamaClaudeProvider()
            with pytest.raises(ValueError):
                p.get_env()

    def test_env_var_overrides_in_get_env(self):
        with _mock_config(), \
             patch.dict("os.environ", {
                 "KOAN_OLLAMA_CLAUDE_BASE_URL": "http://env-url",
                 "KOAN_OLLAMA_CLAUDE_MODEL": "env-model",
                 "KOAN_OLLAMA_CLAUDE_API_KEY": "env-key",
             }):
            p = OllamaClaudeProvider()
            env = p.get_env()
            assert env["ANTHROPIC_BASE_URL"] == "http://env-url"
            assert env["ANTHROPIC_MODEL"] == "env-model"
            assert env["ANTHROPIC_API_KEY"] == "env-key"


# ---------------------------------------------------------------------------
# is_available()
# ---------------------------------------------------------------------------

class TestOllamaClaudeAvailability:
    """Availability checks: binary + valid config."""

    @patch("shutil.which", return_value="/usr/local/bin/claude")
    def test_available_with_valid_config(self, _which):
        with _mock_config():
            p = OllamaClaudeProvider()
            assert p.is_available() is True

    @patch("shutil.which", return_value=None)
    def test_unavailable_without_claude_binary(self, _which):
        with _mock_config():
            p = OllamaClaudeProvider()
            assert p.is_available() is False

    @patch("shutil.which", return_value="/usr/local/bin/claude")
    def test_unavailable_with_missing_config(self, _which):
        with _mock_config({}):
            p = OllamaClaudeProvider()
            assert p.is_available() is False


# ---------------------------------------------------------------------------
# check_quota_available()
# ---------------------------------------------------------------------------

class TestOllamaClaudeQuota:
    """Quota check validates proxy reachability and model availability."""

    def test_available_when_server_and_model_ready(self):
        with _mock_config(), \
             patch("app.ollama_client.check_server_and_model", return_value=(True, "")):
            p = OllamaClaudeProvider()
            ok, detail = p.check_quota_available("/tmp/project")
            assert ok is True
            assert detail == ""


# ---------------------------------------------------------------------------
# Inherited CLI flag building
# ---------------------------------------------------------------------------

class TestOllamaClaudeFlagBuilding:
    """Verify ClaudeProvider flags are inherited correctly."""

    def test_binary_is_claude(self):
        with _mock_config():
            p = OllamaClaudeProvider()
            assert p.binary() == "claude"

    def test_build_prompt_args(self):
        with _mock_config():
            p = OllamaClaudeProvider()
            assert p.build_prompt_args("hello") == ["-p", "hello"]

    def test_build_tool_args_allowed(self):
        with _mock_config():
            p = OllamaClaudeProvider()
            args = p.build_tool_args(allowed_tools=["Bash", "Read"])
            assert args == ["--allowedTools", "Bash,Read"]

    def test_build_tool_args_disallowed(self):
        with _mock_config():
            p = OllamaClaudeProvider()
            args = p.build_tool_args(disallowed_tools=["Bash"])
            assert args == ["--disallowedTools", "Bash"]

    def test_build_output_args(self):
        with _mock_config():
            p = OllamaClaudeProvider()
            assert p.build_output_args("json") == ["--output-format", "json"]

    def test_build_max_turns_args(self):
        with _mock_config():
            p = OllamaClaudeProvider()
            assert p.build_max_turns_args(5) == ["--max-turns", "5"]

    def test_build_mcp_args(self):
        with _mock_config():
            p = OllamaClaudeProvider()
            args = p.build_mcp_args(["config.json"])
            assert args == ["--mcp-config", "config.json"]

    def test_build_plugin_args(self):
        with _mock_config():
            p = OllamaClaudeProvider()
            args = p.build_plugin_args(["/tmp/plugins"])
            assert args == ["--plugin-dir", "/tmp/plugins"]


# ---------------------------------------------------------------------------
# build_model_args — uses configured model as default
# ---------------------------------------------------------------------------

class TestOllamaClaudeModelArgs:
    """Model args use Ollama model config as default."""

    def test_uses_configured_model_when_no_explicit(self):
        with _mock_config():
            p = OllamaClaudeProvider()
            args = p.build_model_args()
            assert args == ["--model", "llama3.3"]

    def test_explicit_model_overrides_config(self):
        with _mock_config():
            p = OllamaClaudeProvider()
            args = p.build_model_args(model="custom-model")
            assert args == ["--model", "custom-model"]

    def test_fallback_ignored(self):
        with _mock_config():
            p = OllamaClaudeProvider()
            args = p.build_model_args(fallback="sonnet")
            # Fallback not meaningful for local inference
            assert "--fallback-model" not in args

    def test_no_model_configured_returns_empty(self):
        config = {"ollama_claude": {"base_url": "http://x"}}
        with _mock_config(config):
            p = OllamaClaudeProvider()
            args = p.build_model_args()
            assert args == []


# ---------------------------------------------------------------------------
# build_command — complete command assembly
# ---------------------------------------------------------------------------

class TestOllamaClaudeBuildCommand:
    """Full command building."""

    def test_build_command_includes_model(self):
        with _mock_config():
            p = OllamaClaudeProvider()
            cmd = p.build_command(prompt="test", max_turns=3)
            assert cmd[0] == "claude"
            assert "-p" in cmd
            assert "--model" in cmd
            idx = cmd.index("--model")
            assert cmd[idx + 1] == "llama3.3"
            assert "--max-turns" in cmd

    def test_build_command_with_explicit_model(self):
        with _mock_config():
            p = OllamaClaudeProvider()
            cmd = p.build_command(prompt="test", model="override")
            idx = cmd.index("--model")
            assert cmd[idx + 1] == "override"


# ---------------------------------------------------------------------------
# build_full_command via resolution
# ---------------------------------------------------------------------------

class TestOllamaClaudeFullCommand:
    """Integration: build_full_command resolves to OllamaClaudeProvider."""

    def setup_method(self):
        reset_provider()

    def teardown_method(self):
        reset_provider()

    @patch.dict("os.environ", {"KOAN_CLI_PROVIDER": "ollama-claude"})
    def test_full_command_uses_claude_binary(self):
        with _mock_config():
            cmd = build_full_command(prompt="hello", max_turns=5)
            assert cmd[0] == "claude"
            assert "-p" in cmd
            assert "--max-turns" in cmd

    @patch.dict("os.environ", {"KOAN_CLI_PROVIDER": "ollama-claude"})
    def test_full_command_includes_model_from_config(self):
        with _mock_config():
            cmd = build_full_command(prompt="hello")
            assert "--model" in cmd
            idx = cmd.index("--model")
            assert cmd[idx + 1] == "llama3.3"


# ---------------------------------------------------------------------------
# Base class get_env() default
# ---------------------------------------------------------------------------

class TestBaseProviderGetEnv:
    """Base CLIProvider.get_env() returns empty dict."""

    def test_default_get_env(self):
        from app.provider.base import CLIProvider
        p = CLIProvider()
        assert p.get_env() == {}

    def test_claude_get_env(self):
        from app.provider.claude import ClaudeProvider
        p = ClaudeProvider()
        assert p.get_env() == {}

    def test_local_get_env(self):
        from app.provider.local import LocalLLMProvider
        p = LocalLLMProvider()
        assert p.get_env() == {}


# ---------------------------------------------------------------------------
# check_quota_available — proxy validation
# ---------------------------------------------------------------------------

class TestOllamaClaudeQuotaCheck:
    """check_quota_available validates proxy reachability and model."""

    def test_quota_ok_when_server_and_model_ready(self):
        with _mock_config(), \
             patch("app.ollama_client.check_server_and_model", return_value=(True, "")):
            p = OllamaClaudeProvider()
            ok, detail = p.check_quota_available("/tmp/project")
            assert ok is True
            assert detail == ""

    def test_quota_fails_when_proxy_unreachable(self):
        with _mock_config(), \
             patch("app.ollama_client.check_server_and_model",
                   return_value=(False, "Ollama server not responding at http://localhost:8080")):
            p = OllamaClaudeProvider()
            ok, detail = p.check_quota_available("/tmp/project")
            assert ok is False
            assert "not responding" in detail

    def test_quota_fails_when_no_base_url(self):
        config = {"ollama_claude": {"model": "llama3.3"}}
        with _mock_config(config):
            p = OllamaClaudeProvider()
            ok, detail = p.check_quota_available("/tmp/project")
            assert ok is False
            assert "base_url" in detail

    def test_quota_fails_when_no_model(self):
        config = {"ollama_claude": {"base_url": "http://localhost:8080"}}
        with _mock_config(config):
            p = OllamaClaudeProvider()
            ok, detail = p.check_quota_available("/tmp/project")
            assert ok is False
            assert "model" in detail

    def test_quota_passes_base_url_and_model(self):
        with _mock_config(), \
             patch("app.ollama_client.check_server_and_model",
                   return_value=(True, "")) as mock_check:
            p = OllamaClaudeProvider()
            p.check_quota_available("/tmp/project")
            mock_check.assert_called_once_with(
                model_name="llama3.3",
                base_url="http://localhost:8080",
                timeout=15.0,
                auto_pull=False,
            )

    def test_quota_passes_timeout(self):
        with _mock_config(), \
             patch("app.ollama_client.check_server_and_model",
                   return_value=(True, "")) as mock_check:
            p = OllamaClaudeProvider()
            p.check_quota_available("/tmp/project", timeout=30)
            mock_check.assert_called_once_with(
                model_name="llama3.3",
                base_url="http://localhost:8080",
                timeout=30.0,
                auto_pull=False,
            )

    def test_auto_pull_disabled_by_default(self):
        with _mock_config(), \
             patch("app.ollama_client.check_server_and_model",
                   return_value=(True, "")) as mock_check:
            p = OllamaClaudeProvider()
            p.check_quota_available("/tmp/project")
            _, kwargs = mock_check.call_args
            assert kwargs["auto_pull"] is False

    def test_auto_pull_enabled_from_config(self):
        config = {
            "ollama_claude": {
                "base_url": "http://localhost:8080",
                "model": "llama3.3",
                "api_key": "ollama",
                "auto_pull": True,
            }
        }
        with _mock_config(config), \
             patch("app.ollama_client.check_server_and_model",
                   return_value=(True, "")) as mock_check:
            p = OllamaClaudeProvider()
            p.check_quota_available("/tmp/project")
            _, kwargs = mock_check.call_args
            assert kwargs["auto_pull"] is True
