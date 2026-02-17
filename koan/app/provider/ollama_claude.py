"""Ollama-Claude provider: Claude Code CLI routed through a local proxy.

Runs the real Claude Code CLI against a local Ollama backend (or any
Anthropic-compatible proxy like LiteLLM) by injecting ANTHROPIC_BASE_URL
and related environment variables into the subprocess.

Unlike the ``local`` provider which runs a custom Python agentic loop,
this provider inherits all Claude CLI features (MCP, extended thinking,
tool sophistication) by extending ClaudeProvider.

Configuration (config.yaml):
    cli_provider: "ollama-claude"
    ollama_claude:
        base_url: "http://localhost:11434"   # Proxy endpoint (NOT /v1)
        api_key: "ollama"                     # Dummy key (most proxies accept anything)
        auth_token: ""                        # Optional Bearer token
        model: "llama3.3"                     # Default model (required)
        haiku_model: ""                       # Override for ANTHROPIC_DEFAULT_HAIKU_MODEL
        sonnet_model: ""                      # Override for ANTHROPIC_DEFAULT_SONNET_MODEL

Can also be set via KOAN_OLLAMA_CLAUDE_* env vars (override config values).
"""

import os
from typing import Dict, Tuple

from app.provider.claude import ClaudeProvider


class OllamaClaudeProvider(ClaudeProvider):
    """Claude Code CLI provider routed through a local Anthropic-compatible proxy.

    Inherits all flag building from ClaudeProvider. The only difference
    is the environment variables injected at subprocess launch time via
    ``get_env()``.

    Raises ValueError at init if ``base_url`` or ``model`` are not configured.
    """

    name = "ollama-claude"

    def __init__(self):
        # Validate required settings at init time (fail early)
        base_url = self._get_setting(
            "KOAN_OLLAMA_CLAUDE_BASE_URL", "base_url", ""
        )
        if not base_url:
            raise ValueError(
                "ollama-claude provider requires 'base_url' — "
                "set KOAN_OLLAMA_CLAUDE_BASE_URL or "
                "ollama_claude.base_url in config.yaml"
            )
        model = self._get_setting(
            "KOAN_OLLAMA_CLAUDE_MODEL", "model", ""
        )
        if not model:
            raise ValueError(
                "ollama-claude provider requires 'model' — "
                "set KOAN_OLLAMA_CLAUDE_MODEL or "
                "ollama_claude.model in config.yaml"
            )

    def _get_config(self) -> dict:
        """Get ollama_claude config section from config.yaml."""
        try:
            from app.utils import load_config
            config = load_config()
            return config.get("ollama_claude", {})
        except Exception:
            return {}

    def _get_setting(self, env_key: str, config_key: str, default: str = "") -> str:
        """Resolve a setting: env var > config.yaml > default."""
        env_val = os.environ.get(env_key, "")
        if env_val:
            return env_val
        return self._get_config().get(config_key, default)

    def is_available(self) -> bool:
        """Check if claude binary is installed and required config is present."""
        if not super().is_available():
            return False
        base_url = self._get_setting("KOAN_OLLAMA_CLAUDE_BASE_URL", "base_url", "")
        model = self._get_setting("KOAN_OLLAMA_CLAUDE_MODEL", "model", "")
        return bool(base_url and model)

    def get_env(self) -> Dict[str, str]:
        """Return environment overrides to route Claude CLI through the proxy."""
        env: Dict[str, str] = {}

        base_url = self._get_setting("KOAN_OLLAMA_CLAUDE_BASE_URL", "base_url", "")
        if base_url:
            env["ANTHROPIC_BASE_URL"] = base_url

        api_key = self._get_setting("KOAN_OLLAMA_CLAUDE_API_KEY", "api_key", "ollama")
        if api_key:
            env["ANTHROPIC_API_KEY"] = api_key

        auth_token = self._get_setting("KOAN_OLLAMA_CLAUDE_AUTH_TOKEN", "auth_token", "")
        if auth_token:
            env["ANTHROPIC_AUTH_TOKEN"] = auth_token

        model = self._get_setting("KOAN_OLLAMA_CLAUDE_MODEL", "model", "")
        if model:
            env["ANTHROPIC_MODEL"] = model

        sonnet_model = self._get_setting("KOAN_OLLAMA_CLAUDE_SONNET_MODEL", "sonnet_model", "")
        if sonnet_model:
            env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = sonnet_model

        haiku_model = self._get_setting("KOAN_OLLAMA_CLAUDE_HAIKU_MODEL", "haiku_model", "")
        if haiku_model:
            env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = haiku_model

        return env

    def check_quota_available(self, project_path: str, timeout: int = 15) -> Tuple[bool, str]:
        """No quota concept with local models — always available."""
        return True, ""
