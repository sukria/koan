"""Tests for app.cli_exec — secure prompt passing via temp files."""

import os
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from app.cli_exec import (
    STDIN_PLACEHOLDER,
    prepare_prompt_file,
    run_cli,
    popen_cli,
    _cleanup_prompt_file,
)


# ---------------------------------------------------------------------------
# prepare_prompt_file
# ---------------------------------------------------------------------------

class TestPreparePromptFile:
    """Tests for prepare_prompt_file()."""

    def test_extracts_prompt_and_writes_temp_file(self):
        cmd = ["claude", "-p", "my secret prompt", "--model", "opus"]
        new_cmd, path = prepare_prompt_file(cmd)
        try:
            assert path is not None
            assert os.path.isfile(path)
            assert new_cmd == ["claude", "-p", STDIN_PLACEHOLDER, "--model", "opus"]
            with open(path) as f:
                assert f.read() == "my secret prompt"
            # Check permissions are restrictive
            mode = os.stat(path).st_mode & 0o777
            assert mode == 0o600
        finally:
            _cleanup_prompt_file(path)

    def test_no_p_flag_returns_unchanged(self):
        cmd = ["claude", "--model", "opus"]
        new_cmd, path = prepare_prompt_file(cmd)
        assert new_cmd is cmd
        assert path is None

    def test_p_at_end_with_no_value_returns_unchanged(self):
        cmd = ["claude", "-p"]
        new_cmd, path = prepare_prompt_file(cmd)
        assert new_cmd is cmd
        assert path is None

    def test_already_placeholder_returns_none(self):
        cmd = ["claude", "-p", STDIN_PLACEHOLDER, "--model", "opus"]
        new_cmd, path = prepare_prompt_file(cmd)
        assert new_cmd is cmd
        assert path is None

    def test_preserves_original_cmd(self):
        cmd = ["claude", "-p", "secret"]
        original = cmd.copy()
        new_cmd, path = prepare_prompt_file(cmd)
        try:
            assert cmd == original  # original not mutated
            assert new_cmd is not cmd
        finally:
            _cleanup_prompt_file(path)

    def test_handles_unicode_prompt(self):
        cmd = ["claude", "-p", "日本語のプロンプト 🎯"]
        new_cmd, path = prepare_prompt_file(cmd)
        try:
            with open(path, encoding="utf-8") as f:
                assert f.read() == "日本語のプロンプト 🎯"
        finally:
            _cleanup_prompt_file(path)

    def test_handles_empty_prompt(self):
        cmd = ["claude", "-p", ""]
        new_cmd, path = prepare_prompt_file(cmd)
        try:
            assert path is not None
            with open(path) as f:
                assert f.read() == ""
            assert new_cmd[2] == STDIN_PLACEHOLDER
        finally:
            _cleanup_prompt_file(path)

    def test_copilot_gh_mode(self):
        cmd = ["gh", "copilot", "-p", "my prompt", "--model", "opus"]
        new_cmd, path = prepare_prompt_file(cmd)
        try:
            assert new_cmd == ["gh", "copilot", "-p", STDIN_PLACEHOLDER, "--model", "opus"]
            with open(path) as f:
                assert f.read() == "my prompt"
        finally:
            _cleanup_prompt_file(path)

    @patch("app.provider.get_provider_name", return_value="copilot")
    def test_copilot_provider_skips_stdin_passing(self, _mock):
        """Copilot provider should skip @stdin mechanism entirely."""
        cmd = ["copilot", "-p", "my prompt", "--allow-all-tools"]
        new_cmd, path = prepare_prompt_file(cmd)
        assert new_cmd is cmd
        assert path is None


# ---------------------------------------------------------------------------
# _cleanup_prompt_file
# ---------------------------------------------------------------------------

class TestCleanupPromptFile:

    def test_removes_existing_file(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("data")
        _cleanup_prompt_file(str(f))
        assert not f.exists()

    def test_ignores_none(self):
        _cleanup_prompt_file(None)  # should not raise

    def test_ignores_missing_file(self):
        _cleanup_prompt_file("/nonexistent/path/file.md")  # should not raise


# ---------------------------------------------------------------------------
# run_cli
# ---------------------------------------------------------------------------

class TestRunCli:

    @patch("app.cli_exec.subprocess.run")
    def test_passes_prompt_via_stdin_fd(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0, "ok", "")
        cmd = ["claude", "-p", "secret prompt", "--model", "opus"]

        result = run_cli(cmd, capture_output=True, text=True, timeout=60)

        call_args = mock_run.call_args
        actual_cmd = call_args[0][0]
        assert actual_cmd[2] == STDIN_PLACEHOLDER
        assert "secret prompt" not in actual_cmd
        # stdin should be a file object, not DEVNULL
        assert call_args[1]["stdin"] != subprocess.DEVNULL

    @patch("app.cli_exec.subprocess.run")
    def test_falls_back_to_devnull_without_p(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0, "ok", "")
        cmd = ["git", "status"]

        run_cli(cmd, capture_output=True, text=True)

        call_args = mock_run.call_args
        assert call_args[1]["stdin"] == subprocess.DEVNULL

    @patch("app.cli_exec.subprocess.run")
    def test_cleans_up_temp_file_on_success(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0, "ok", "")
        cmd = ["claude", "-p", "test prompt"]

        import glob
        before = set(glob.glob("/tmp/koan-prompt-*"))
        run_cli(cmd, capture_output=True, text=True)
        after = set(glob.glob("/tmp/koan-prompt-*"))
        assert after - before == set()

    @patch("app.cli_exec.subprocess.run", side_effect=Exception("boom"))
    def test_cleans_up_temp_file_on_exception(self, mock_run):
        cmd = ["claude", "-p", "test prompt"]

        import glob
        before = set(glob.glob("/tmp/koan-prompt-*"))
        with pytest.raises(Exception, match="boom"):
            run_cli(cmd, capture_output=True, text=True)
        after = set(glob.glob("/tmp/koan-prompt-*"))
        assert after - before == set()

    @patch("app.cli_exec.subprocess.run")
    def test_removes_existing_stdin_kwarg(self, mock_run):
        """If caller passes stdin=DEVNULL, it gets replaced with the file."""
        mock_run.return_value = subprocess.CompletedProcess([], 0, "ok", "")
        cmd = ["claude", "-p", "prompt"]

        run_cli(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True)

        call_args = mock_run.call_args
        assert call_args[1]["stdin"] != subprocess.DEVNULL


# ---------------------------------------------------------------------------
# popen_cli
# ---------------------------------------------------------------------------

class TestPopenCli:

    @patch("app.cli_exec.subprocess.Popen")
    def test_returns_proc_and_cleanup(self, mock_popen):
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc
        cmd = ["claude", "-p", "secret", "--model", "opus"]

        proc, cleanup = popen_cli(cmd, stdout=subprocess.PIPE)

        assert proc is mock_proc
        actual_cmd = mock_popen.call_args[0][0]
        assert actual_cmd[2] == STDIN_PLACEHOLDER

        # Cleanup should remove the temp file
        import glob
        before = set(glob.glob("/tmp/koan-prompt-*"))
        cleanup()
        after = set(glob.glob("/tmp/koan-prompt-*"))
        assert len(after) <= len(before)

    @patch("app.cli_exec.subprocess.Popen")
    def test_no_p_flag_returns_noop_cleanup(self, mock_popen):
        mock_popen.return_value = MagicMock()
        cmd = ["git", "status"]

        proc, cleanup = popen_cli(cmd)

        call_args = mock_popen.call_args
        assert call_args[1].get("stdin", subprocess.DEVNULL) == subprocess.DEVNULL
        cleanup()  # should not raise

    @patch("app.cli_exec.subprocess.Popen")
    def test_stdin_is_file_object(self, mock_popen):
        mock_popen.return_value = MagicMock()
        cmd = ["claude", "-p", "prompt"]

        proc, cleanup = popen_cli(cmd)

        call_args = mock_popen.call_args
        stdin_arg = call_args[1]["stdin"]
        assert hasattr(stdin_arg, "read")  # it's a file object
        cleanup()


# ---------------------------------------------------------------------------
# Provider env injection
# ---------------------------------------------------------------------------

class TestProviderEnvInjection:
    """Tests for provider environment injection in run_cli/popen_cli."""

    def setup_method(self):
        from app.provider import reset_provider
        reset_provider()

    def teardown_method(self):
        from app.provider import reset_provider
        reset_provider()

    @patch("app.cli_exec.subprocess.run")
    @patch.dict("os.environ", {
        "KOAN_CLI_PROVIDER": "ollama-claude",
        "KOAN_OLLAMA_CLAUDE_BASE_URL": "http://localhost:11434",
        "KOAN_OLLAMA_CLAUDE_MODEL": "llama3.3",
    })
    def test_run_cli_injects_env_for_ollama_claude(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0, "ok", "")
        run_cli(["claude", "-p", "test"], capture_output=True, text=True)

        call_kwargs = mock_run.call_args[1]
        assert "env" in call_kwargs
        env = call_kwargs["env"]
        assert env["ANTHROPIC_BASE_URL"] == "http://localhost:11434"
        assert env["ANTHROPIC_API_KEY"] == "ollama"
        assert env["ANTHROPIC_MODEL"] == "llama3.3"

    @patch("app.cli_exec.subprocess.run")
    @patch.dict("os.environ", {"KOAN_CLI_PROVIDER": "claude"})
    def test_run_cli_no_env_for_claude_provider(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0, "ok", "")
        run_cli(["claude", "-p", "test"], capture_output=True, text=True)

        call_kwargs = mock_run.call_args[1]
        assert "env" not in call_kwargs

    @patch("app.cli_exec.subprocess.Popen")
    @patch.dict("os.environ", {
        "KOAN_CLI_PROVIDER": "ollama-claude",
        "KOAN_OLLAMA_CLAUDE_BASE_URL": "http://localhost:11434",
        "KOAN_OLLAMA_CLAUDE_MODEL": "llama3.3",
    })
    def test_popen_cli_injects_env_for_ollama_claude(self, mock_popen):
        mock_popen.return_value = MagicMock()
        proc, cleanup = popen_cli(["claude", "-p", "test"], stdout=subprocess.PIPE)

        call_kwargs = mock_popen.call_args[1]
        assert "env" in call_kwargs
        env = call_kwargs["env"]
        assert env["ANTHROPIC_BASE_URL"] == "http://localhost:11434"
        assert env["ANTHROPIC_MODEL"] == "llama3.3"
        cleanup()

    @patch("app.cli_exec.subprocess.run")
    @patch.dict("os.environ", {
        "KOAN_CLI_PROVIDER": "ollama-claude",
        "KOAN_OLLAMA_CLAUDE_BASE_URL": "http://localhost:11434",
        "KOAN_OLLAMA_CLAUDE_MODEL": "llama3.3",
    })
    def test_explicit_env_not_overridden(self, mock_run):
        """When caller passes env= explicitly, provider env is not injected."""
        mock_run.return_value = subprocess.CompletedProcess([], 0, "ok", "")
        custom_env = {"PATH": "/usr/bin", "CUSTOM": "value"}
        run_cli(["echo", "test"], env=custom_env, capture_output=True, text=True)

        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["env"] is custom_env
        # Provider env vars should NOT be in the explicit env
        assert "ANTHROPIC_BASE_URL" not in call_kwargs["env"]

    @patch("app.cli_exec.subprocess.run")
    @patch.dict("os.environ", {
        "KOAN_CLI_PROVIDER": "ollama-claude",
        "KOAN_OLLAMA_CLAUDE_BASE_URL": "http://localhost:11434",
        "KOAN_OLLAMA_CLAUDE_MODEL": "llama3.3",
    })
    def test_injected_env_includes_parent_env(self, mock_run):
        """Injected env merges provider overrides on top of os.environ."""
        mock_run.return_value = subprocess.CompletedProcess([], 0, "ok", "")
        run_cli(["echo", "test"], capture_output=True, text=True)

        call_kwargs = mock_run.call_args[1]
        env = call_kwargs["env"]
        # Should contain both parent env vars and provider overrides
        assert "PATH" in env  # from os.environ
        assert env["ANTHROPIC_BASE_URL"] == "http://localhost:11434"  # from provider
