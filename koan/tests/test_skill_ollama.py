"""Tests for the /ollama skill handler."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

# Ensure the koan package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skills.core.ollama.handler import handle, _format_size


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def instance_dir(tmp_path):
    inst = tmp_path / "instance"
    inst.mkdir()
    return inst


@pytest.fixture
def koan_root(tmp_path, instance_dir):
    return tmp_path


def _make_ctx(koan_root, instance_dir, command_name="ollama", args=""):
    return SimpleNamespace(
        koan_root=koan_root,
        instance_dir=instance_dir,
        command_name=command_name,
        args=args,
        send_message=None,
        handle_chat=None,
    )


def _patch_ollama(provider="local", ready=True, version="0.16.0",
                  models=None, running=None):
    """Return a stack of patches for ollama-related calls."""
    if models is None:
        models = []
    if running is None:
        running = []
    return (
        patch("app.provider.get_provider_name", return_value=provider),
        patch("app.ollama_client.is_server_ready", return_value=ready),
        patch("app.ollama_client.get_version", return_value=version),
        patch("app.ollama_client.list_models", return_value=models),
        patch("app.ollama_client.list_running_models", return_value=running),
    )


# ---------------------------------------------------------------------------
# _format_size
# ---------------------------------------------------------------------------

class TestFormatSize:
    def test_zero_returns_empty(self):
        assert _format_size(0) == ""

    def test_none_returns_empty(self):
        assert _format_size(None) == ""

    def test_gb_size(self):
        assert _format_size(5 * 1024 ** 3) == "5.0GB"

    def test_mb_size(self):
        assert _format_size(500 * 1024 ** 2) == "500MB"

    def test_small_gb(self):
        result = _format_size(int(1.5 * 1024 ** 3))
        assert "1.5GB" in result

    def test_exactly_1gb(self):
        assert _format_size(1024 ** 3) == "1.0GB"

    def test_sub_gb_shows_mb(self):
        result = _format_size(100 * 1024 ** 2)
        assert result == "100MB"


# ---------------------------------------------------------------------------
# handle — help subcommand
# ---------------------------------------------------------------------------

class TestHandleHelp:
    def test_help_shows_commands(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir, args="help")
        result = handle(ctx)
        assert "/ollama list" in result
        assert "/ollama pull" in result
        assert "/ollama rm" in result

    def test_help_flag(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir, args="-h")
        result = handle(ctx)
        assert "Ollama management" in result

    def test_help_double_dash(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir, args="--help")
        result = handle(ctx)
        assert "/llama" in result  # mentions alias

    def test_help_does_not_check_provider(self, koan_root, instance_dir):
        """Help should work regardless of provider — no provider check needed."""
        ctx = _make_ctx(koan_root, instance_dir, args="help")
        # No patches needed — help doesn't call _check_provider
        result = handle(ctx)
        assert "not active" not in result


# ---------------------------------------------------------------------------
# handle — provider check
# ---------------------------------------------------------------------------

class TestHandleProviderCheck:
    def test_not_active_for_claude(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir)
        with patch("app.provider.get_provider_name", return_value="claude"):
            result = handle(ctx)
        assert "not active" in result
        assert "claude" in result

    def test_not_active_for_copilot(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir)
        with patch("app.provider.get_provider_name", return_value="copilot"):
            result = handle(ctx)
        assert "not active" in result


# ---------------------------------------------------------------------------
# handle — server not responding
# ---------------------------------------------------------------------------

class TestHandleServerDown:
    def test_server_not_responding(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir)
        patches = _patch_ollama(ready=False)
        with patches[0], patches[1]:
            result = handle(ctx)
        assert "not responding" in result
        assert "ollama serve" in result


# ---------------------------------------------------------------------------
# handle — server running
# ---------------------------------------------------------------------------

class TestHandleServerRunning:
    def test_no_models(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir)
        patches = _patch_ollama(models=[])
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = handle(ctx)
        assert "v0.16.0" in result
        assert "No models pulled" in result
        assert "ollama pull" in result

    def test_models_listed(self, koan_root, instance_dir):
        models = [
            {"name": "qwen2.5-coder:14b", "size": 9 * 1024 ** 3,
             "details": {"parameter_size": "14B", "quantization_level": "Q4_K_M"}},
            {"name": "llama3.2:latest", "size": 2 * 1024 ** 3,
             "details": {"parameter_size": "3B", "quantization_level": "Q8_0"}},
        ]
        ctx = _make_ctx(koan_root, instance_dir)
        patches = _patch_ollama(models=models)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = handle(ctx)
        assert "Models (2)" in result
        assert "qwen2.5-coder:14b" in result
        assert "14B" in result
        assert "Q4_K_M" in result
        assert "llama3.2:latest" in result

    def test_shows_running_models(self, koan_root, instance_dir):
        models = [{"name": "qwen2.5-coder:14b", "size": 9 * 1024 ** 3, "details": {}}]
        running = [{"name": "qwen2.5-coder:14b"}]
        ctx = _make_ctx(koan_root, instance_dir)
        patches = _patch_ollama(models=models, running=running)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = handle(ctx)
        assert "Loaded: qwen2.5-coder:14b" in result

    def test_unknown_version(self, koan_root, instance_dir):
        models = [{"name": "test:latest", "size": 1024 ** 3, "details": {}}]
        ctx = _make_ctx(koan_root, instance_dir)
        patches = _patch_ollama(version=None, models=models)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = handle(ctx)
        assert "unknown" in result

    def test_works_with_ollama_claude_provider(self, koan_root, instance_dir):
        models = [{"name": "test:latest", "size": 1024 ** 3, "details": {}}]
        ctx = _make_ctx(koan_root, instance_dir)
        patches = _patch_ollama(provider="ollama-claude", models=models)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = handle(ctx)
        assert "v0.16.0" in result
        assert "Models (1)" in result

    def test_works_with_ollama_provider(self, koan_root, instance_dir):
        models = [{"name": "test:latest", "size": 1024 ** 3, "details": {}}]
        ctx = _make_ctx(koan_root, instance_dir)
        patches = _patch_ollama(provider="ollama", models=models)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = handle(ctx)
        assert "running" in result


# ---------------------------------------------------------------------------
# handle — configured model check
# ---------------------------------------------------------------------------

class TestHandleConfiguredModel:
    def test_configured_model_ready(self, koan_root, instance_dir):
        models = [{"name": "qwen2.5-coder:14b", "size": 9 * 1024 ** 3, "details": {}}]
        ctx = _make_ctx(koan_root, instance_dir)
        patches = _patch_ollama(models=models)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patch("app.ollama_client.is_model_available", return_value=True), \
             patch("app.provider.local.LocalLLMProvider._get_default_model", return_value="qwen2.5-coder:14b"):
            result = handle(ctx)
        assert "Configured model: qwen2.5-coder:14b" in result
        assert "ready" in result

    def test_configured_model_not_pulled(self, koan_root, instance_dir):
        models = [{"name": "llama3.2:latest", "size": 2 * 1024 ** 3, "details": {}}]
        ctx = _make_ctx(koan_root, instance_dir)
        patches = _patch_ollama(models=models)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patch("app.ollama_client.is_model_available", return_value=False), \
             patch("app.provider.local.LocalLLMProvider._get_default_model", return_value="qwen2.5-coder:14b"):
            result = handle(ctx)
        assert "not pulled" in result
        assert "ollama pull qwen2.5-coder:14b" in result

    def test_no_configured_model(self, koan_root, instance_dir):
        models = [{"name": "test:latest", "size": 1024 ** 3, "details": {}}]
        ctx = _make_ctx(koan_root, instance_dir)
        patches = _patch_ollama(models=models)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patch("app.provider.local.LocalLLMProvider._get_default_model", return_value=""):
            result = handle(ctx)
        assert "Configured model" not in result

    def test_not_pulled_model_suggests_ollama_pull_command(self, koan_root, instance_dir):
        """When no models are pulled, suggest /ollama pull instead of CLI."""
        ctx = _make_ctx(koan_root, instance_dir)
        patches = _patch_ollama(models=[])
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = handle(ctx)
        assert "/ollama pull" in result


# ---------------------------------------------------------------------------
# handle — /ollama pull subcommand
# ---------------------------------------------------------------------------

class TestHandlePull:
    """Tests for /ollama pull <model> subcommand."""

    def test_pull_no_model_shows_usage(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir, args="pull")
        result = handle(ctx)
        assert "Usage" in result
        assert "/ollama pull" in result

    def test_pull_wrong_provider(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir, args="pull llama3.3")
        with patch("app.provider.get_provider_name", return_value="claude"):
            result = handle(ctx)
        assert "not active" in result

    def test_pull_server_not_responding(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir, args="pull llama3.3")
        with patch("app.provider.get_provider_name", return_value="local"), \
             patch("app.ollama_client.is_server_ready", return_value=False):
            result = handle(ctx)
        assert "not responding" in result

    def test_pull_already_available(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir, args="pull llama3.3")
        with patch("app.provider.get_provider_name", return_value="local"), \
             patch("app.ollama_client.is_server_ready", return_value=True), \
             patch("app.ollama_client.is_model_available", return_value=True):
            result = handle(ctx)
        assert "already available" in result

    def test_pull_success(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir, args="pull llama3.3")
        with patch("app.provider.get_provider_name", return_value="local"), \
             patch("app.ollama_client.is_server_ready", return_value=True), \
             patch("app.ollama_client.is_model_available", return_value=False), \
             patch("app.ollama_client.pull_model_streaming", return_value=(True, "success")):
            result = handle(ctx)
        assert "pulled successfully" in result

    def test_pull_failure(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir, args="pull llama3.3")
        with patch("app.provider.get_provider_name", return_value="local"), \
             patch("app.ollama_client.is_server_ready", return_value=True), \
             patch("app.ollama_client.is_model_available", return_value=False), \
             patch("app.ollama_client.pull_model_streaming",
                   return_value=(False, "model not found")):
            result = handle(ctx)
        assert "Failed" in result
        assert "model not found" in result

    def test_pull_works_with_ollama_provider(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir, args="pull test-model")
        with patch("app.provider.get_provider_name", return_value="ollama"), \
             patch("app.ollama_client.is_server_ready", return_value=True), \
             patch("app.ollama_client.is_model_available", return_value=False), \
             patch("app.ollama_client.pull_model_streaming", return_value=(True, "success")):
            result = handle(ctx)
        assert "pulled successfully" in result

    def test_pull_works_with_ollama_claude_provider(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir, args="pull test-model")
        with patch("app.provider.get_provider_name", return_value="ollama-claude"), \
             patch("app.ollama_client.is_server_ready", return_value=True), \
             patch("app.ollama_client.is_model_available", return_value=False), \
             patch("app.ollama_client.pull_model_streaming", return_value=(True, "success")):
            result = handle(ctx)
        assert "pulled successfully" in result

    def test_pull_with_tag(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir, args="pull qwen2.5-coder:14b")
        with patch("app.provider.get_provider_name", return_value="local"), \
             patch("app.ollama_client.is_server_ready", return_value=True), \
             patch("app.ollama_client.is_model_available", return_value=False), \
             patch("app.ollama_client.pull_model_streaming", return_value=(True, "success")):
            result = handle(ctx)
        assert "pulled successfully" in result

    def test_pull_uses_streaming(self, koan_root, instance_dir):
        """Pull now uses pull_model_streaming instead of pull_model."""
        ctx = _make_ctx(koan_root, instance_dir, args="pull llama3.3")
        with patch("app.provider.get_provider_name", return_value="local"), \
             patch("app.ollama_client.is_server_ready", return_value=True), \
             patch("app.ollama_client.is_model_available", return_value=False), \
             patch("app.ollama_client.pull_model_streaming",
                   return_value=(True, "success")) as mock_pull:
            result = handle(ctx)
        mock_pull.assert_called_once()
        assert "pulled successfully" in result


# ---------------------------------------------------------------------------
# handle — /ollama remove subcommand
# ---------------------------------------------------------------------------

class TestHandleRemove:
    """Tests for /ollama remove <model> subcommand."""

    def test_remove_no_model_shows_usage(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir, args="remove")
        result = handle(ctx)
        assert "Usage" in result
        assert "/ollama remove" in result

    def test_rm_alias_no_model_shows_usage(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir, args="rm")
        result = handle(ctx)
        assert "Usage" in result

    def test_remove_wrong_provider(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir, args="remove llama3.3")
        with patch("app.provider.get_provider_name", return_value="claude"):
            result = handle(ctx)
        assert "not active" in result

    def test_remove_server_not_responding(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir, args="remove llama3.3")
        with patch("app.provider.get_provider_name", return_value="local"), \
             patch("app.ollama_client.is_server_ready", return_value=False):
            result = handle(ctx)
        assert "not responding" in result

    def test_remove_success(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir, args="remove llama3.3")
        with patch("app.provider.get_provider_name", return_value="local"), \
             patch("app.ollama_client.is_server_ready", return_value=True), \
             patch("app.ollama_client.get_model_info",
                   return_value={"size": 5 * 1024 ** 3}), \
             patch("app.ollama_client.delete_model",
                   return_value=(True, "deleted")):
            result = handle(ctx)
        assert "removed" in result
        assert "llama3.3" in result

    def test_remove_shows_size(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir, args="remove llama3.3")
        with patch("app.provider.get_provider_name", return_value="local"), \
             patch("app.ollama_client.is_server_ready", return_value=True), \
             patch("app.ollama_client.get_model_info",
                   return_value={"size": 5 * 1024 ** 3}), \
             patch("app.ollama_client.delete_model",
                   return_value=(True, "deleted")):
            result = handle(ctx)
        assert "5.0GB" in result

    def test_remove_failure(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir, args="remove nonexistent")
        with patch("app.provider.get_provider_name", return_value="local"), \
             patch("app.ollama_client.is_server_ready", return_value=True), \
             patch("app.ollama_client.get_model_info", return_value=None), \
             patch("app.ollama_client.delete_model",
                   return_value=(False, "not found locally")):
            result = handle(ctx)
        assert "Failed" in result
        assert "not found" in result

    def test_rm_alias_works(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir, args="rm llama3.3")
        with patch("app.provider.get_provider_name", return_value="local"), \
             patch("app.ollama_client.is_server_ready", return_value=True), \
             patch("app.ollama_client.get_model_info", return_value=None), \
             patch("app.ollama_client.delete_model",
                   return_value=(True, "deleted")):
            result = handle(ctx)
        assert "removed" in result

    def test_remove_with_ollama_claude(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir, args="remove test-model")
        with patch("app.provider.get_provider_name", return_value="ollama-claude"), \
             patch("app.ollama_client.is_server_ready", return_value=True), \
             patch("app.ollama_client.get_model_info", return_value=None), \
             patch("app.ollama_client.delete_model",
                   return_value=(True, "deleted")):
            result = handle(ctx)
        assert "removed" in result


# ---------------------------------------------------------------------------
# handle — /ollama list subcommand
# ---------------------------------------------------------------------------

class TestHandleList:
    """Tests for /ollama list subcommand."""

    def test_list_wrong_provider(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir, args="list")
        with patch("app.provider.get_provider_name", return_value="claude"):
            result = handle(ctx)
        assert "not active" in result

    def test_list_server_not_responding(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir, args="list")
        with patch("app.provider.get_provider_name", return_value="local"), \
             patch("app.ollama_client.is_server_ready", return_value=False):
            result = handle(ctx)
        assert "not responding" in result

    def test_list_no_models(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir, args="list")
        with patch("app.provider.get_provider_name", return_value="local"), \
             patch("app.ollama_client.is_server_ready", return_value=True), \
             patch("app.ollama_client.list_models", return_value=[]):
            result = handle(ctx)
        assert "No models available" in result
        assert "/ollama pull" in result

    def test_list_shows_models(self, koan_root, instance_dir):
        models = [
            {"name": "qwen2.5-coder:14b", "size": 9 * 1024 ** 3,
             "details": {"parameter_size": "14B", "quantization_level": "Q4_K_M"}},
            {"name": "llama3.2:latest", "size": 2 * 1024 ** 3,
             "details": {"parameter_size": "3B", "quantization_level": "Q8_0"}},
        ]
        ctx = _make_ctx(koan_root, instance_dir, args="list")
        with patch("app.provider.get_provider_name", return_value="local"), \
             patch("app.ollama_client.is_server_ready", return_value=True), \
             patch("app.ollama_client.list_models", return_value=models):
            result = handle(ctx)
        assert "Models (2)" in result
        assert "qwen2.5-coder:14b" in result
        assert "llama3.2:latest" in result
        # Should NOT include server version or running models
        assert "running" not in result.lower()

    def test_ls_alias_works(self, koan_root, instance_dir):
        models = [{"name": "test:latest", "size": 1024 ** 3, "details": {}}]
        ctx = _make_ctx(koan_root, instance_dir, args="ls")
        with patch("app.provider.get_provider_name", return_value="local"), \
             patch("app.ollama_client.is_server_ready", return_value=True), \
             patch("app.ollama_client.list_models", return_value=models):
            result = handle(ctx)
        assert "Models (1)" in result

    def test_models_alias_works(self, koan_root, instance_dir):
        models = [{"name": "test:latest", "size": 1024 ** 3, "details": {}}]
        ctx = _make_ctx(koan_root, instance_dir, args="models")
        with patch("app.provider.get_provider_name", return_value="local"), \
             patch("app.ollama_client.is_server_ready", return_value=True), \
             patch("app.ollama_client.list_models", return_value=models):
            result = handle(ctx)
        assert "Models (1)" in result


# ---------------------------------------------------------------------------
# /ollama show <model>
# ---------------------------------------------------------------------------


class TestHandleShow:
    """Tests for /ollama show <model> subcommand."""

    def test_show_requires_model_name(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir, args="show")
        result = handle(ctx)
        assert "Usage:" in result

    def test_show_displays_model_details(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir, args="show llama3.3")
        details_output = "Model: llama3.3\n  Parameters: 8B\n  Family: llama"
        with patch("app.provider.get_provider_name", return_value="local"), \
             patch("app.ollama_client.is_server_ready", return_value=True), \
             patch("app.ollama_client.format_model_details", return_value=details_output):
            result = handle(ctx)
        assert "llama3.3" in result
        assert "8B" in result

    def test_show_not_found(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir, args="show nonexistent")
        with patch("app.provider.get_provider_name", return_value="local"), \
             patch("app.ollama_client.is_server_ready", return_value=True), \
             patch("app.ollama_client.format_model_details", return_value="Model 'nonexistent' not found."):
            result = handle(ctx)
        assert "not found" in result.lower()

    def test_show_requires_ollama_provider(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir, args="show llama3.3")
        with patch("app.provider.get_provider_name", return_value="claude"):
            result = handle(ctx)
        assert "not active" in result.lower()

    def test_show_server_not_responding(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir, args="show llama3.3")
        with patch("app.provider.get_provider_name", return_value="local"), \
             patch("app.ollama_client.is_server_ready", return_value=False):
            result = handle(ctx)
        assert "not responding" in result.lower()

    def test_info_alias_works(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir, args="info llama3.3")
        details_output = "Model: llama3.3\n  Parameters: 8B"
        with patch("app.provider.get_provider_name", return_value="local"), \
             patch("app.ollama_client.is_server_ready", return_value=True), \
             patch("app.ollama_client.format_model_details", return_value=details_output):
            result = handle(ctx)
        assert "llama3.3" in result

    def test_info_bare_shows_usage(self, koan_root, instance_dir):
        ctx = _make_ctx(koan_root, instance_dir, args="info")
        result = handle(ctx)
        assert "Usage:" in result

    def test_help_includes_show(self, koan_root, instance_dir):
        """Help text should mention the show command."""
        ctx = _make_ctx(koan_root, instance_dir, args="help")
        result = handle(ctx)
        assert "show" in result.lower()
