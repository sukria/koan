"""Tests for provider check_quota_available implementations."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from app.provider.base import CLIProvider
from app.provider.claude import ClaudeProvider
from app.provider.copilot import CopilotProvider
from app.provider.local import LocalLLMProvider


class TestBaseProviderQuota:
    """Tests for CLIProvider.check_quota_available() base implementation."""

    def test_base_always_returns_available(self):
        """Base implementation returns (True, '') — no quota concept."""

        class StubProvider(CLIProvider):
            name = "stub"
            def binary(self): return "stub"
            def build_prompt_args(self, p): return []
            def build_tool_args(self, a=None, d=None): return []
            def build_model_args(self, m="", f=""): return []
            def build_output_args(self, f=""): return []
            def build_max_turns_args(self, m=0): return []
            def build_mcp_args(self, c=None): return []

        ok, detail = StubProvider().check_quota_available("/tmp")
        assert ok is True
        assert detail == ""


class TestClaudeProviderQuota:
    """Tests for ClaudeProvider.check_quota_available()."""

    def setup_method(self):
        self.provider = ClaudeProvider()

    @patch("subprocess.run")
    @patch("app.quota_handler.detect_quota_exhaustion", return_value=False)
    def test_quota_available(self, mock_detect, mock_run):
        """When usage shows quota available, returns (True, '')."""
        mock_run.return_value = MagicMock(
            stdout="Tokens used: 1000/50000",
            stderr="",
            returncode=0,
        )

        ok, detail = self.provider.check_quota_available("/tmp/project")
        assert ok is True
        assert detail == ""
        mock_run.assert_called_once()
        # Verify 'claude usage' command
        cmd = mock_run.call_args[0][0]
        assert cmd == ["claude", "usage"]

    @patch("subprocess.run")
    @patch("app.quota_handler.detect_quota_exhaustion", return_value=True)
    def test_quota_exhausted(self, mock_detect, mock_run):
        """When quota is exhausted, returns (False, combined_output)."""
        mock_run.return_value = MagicMock(
            stdout="Tokens used: 50000/50000\nQuota exceeded!",
            stderr="Rate limit exceeded",
            returncode=0,
        )

        ok, detail = self.provider.check_quota_available("/tmp/project")
        assert ok is False
        assert "Rate limit exceeded" in detail
        assert "Quota exceeded!" in detail

    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 15))
    def test_timeout_returns_optimistic(self, mock_run):
        """On timeout, proceed optimistically (True, '')."""
        ok, detail = self.provider.check_quota_available("/tmp/project")
        assert ok is True
        assert detail == ""

    @patch("subprocess.run", side_effect=FileNotFoundError("claude not found"))
    def test_binary_not_found_returns_optimistic(self, mock_run):
        """When CLI binary is missing, proceed optimistically."""
        ok, detail = self.provider.check_quota_available("/tmp/project")
        assert ok is True
        assert detail == ""

    @patch("subprocess.run", side_effect=OSError("disk error"))
    def test_os_error_returns_optimistic(self, mock_run):
        """On generic OS error, proceed optimistically."""
        ok, detail = self.provider.check_quota_available("/tmp/project")
        assert ok is True
        assert detail == ""

    @patch("subprocess.run")
    @patch("app.quota_handler.detect_quota_exhaustion", return_value=False)
    def test_passes_cwd(self, mock_detect, mock_run):
        """Verify project_path is forwarded as cwd."""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)

        self.provider.check_quota_available("/my/custom/path")
        kwargs = mock_run.call_args[1]
        assert kwargs["cwd"] == "/my/custom/path"

    @patch("subprocess.run")
    @patch("app.quota_handler.detect_quota_exhaustion", return_value=False)
    def test_captures_output(self, mock_detect, mock_run):
        """Verify subprocess captures stdout/stderr."""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)

        self.provider.check_quota_available("/tmp")
        kwargs = mock_run.call_args[1]
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True

    @patch("subprocess.run")
    @patch("app.quota_handler.detect_quota_exhaustion", return_value=False)
    def test_custom_timeout(self, mock_detect, mock_run):
        """Custom timeout parameter is respected."""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)

        self.provider.check_quota_available("/tmp", timeout=30)
        kwargs = mock_run.call_args[1]
        assert kwargs["timeout"] == 30

    @patch("subprocess.run")
    @patch("app.quota_handler.detect_quota_exhaustion", return_value=True)
    def test_combines_stderr_and_stdout(self, mock_detect, mock_run):
        """Combined output includes both stderr and stdout."""
        mock_run.return_value = MagicMock(
            stdout="stdout data",
            stderr="stderr data",
            returncode=0,
        )

        ok, detail = self.provider.check_quota_available("/tmp")
        assert ok is False
        # detect_quota_exhaustion receives combined stderr + stdout
        combined = mock_detect.call_args[0][0]
        assert "stderr data" in combined
        assert "stdout data" in combined

    @patch("subprocess.run")
    @patch("app.quota_handler.detect_quota_exhaustion", return_value=False)
    def test_handles_none_stdout(self, mock_detect, mock_run):
        """When stdout or stderr is None, doesn't crash."""
        mock_run.return_value = MagicMock(
            stdout=None,
            stderr=None,
            returncode=0,
        )

        ok, detail = self.provider.check_quota_available("/tmp")
        assert ok is True


class TestLocalProviderQuota:
    """Local/Ollama providers check server readiness instead of quota."""

    def test_local_available_when_server_and_model_ok(self):
        """LocalLLMProvider checks Ollama server + model availability."""
        provider = LocalLLMProvider()
        with patch("app.ollama_client.check_server_and_model", return_value=(True, "")):
            ok, detail = provider.check_quota_available("/tmp")
            assert ok is True
            assert detail == ""

    def test_local_unavailable_when_server_down(self):
        """LocalLLMProvider reports server connectivity issues."""
        provider = LocalLLMProvider()
        with patch("app.ollama_client.check_server_and_model",
                   return_value=(False, "Ollama server not responding")):
            ok, detail = provider.check_quota_available("/tmp")
            assert ok is False
            assert "not responding" in detail


class TestCopilotProviderQuota:
    """CopilotProvider uses base implementation (no quota check)."""

    def test_copilot_always_available(self):
        """CopilotProvider inherits base (True, '')."""
        provider = CopilotProvider()
        ok, detail = provider.check_quota_available("/tmp")
        assert ok is True
        assert detail == ""
