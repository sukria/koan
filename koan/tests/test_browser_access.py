"""Tests for browser_access.py — Playwright MCP integration."""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from app.browser_access import (
    get_browser_config,
    is_browser_enabled,
    is_npx_available,
    build_mcp_config,
    write_mcp_config,
    get_browser_mcp_flags,
    get_browser_status,
    format_browser_status,
    get_browser_flags_for_shell,
    get_mcp_config_path,
    _reset_config_cache,
)


@pytest.fixture(autouse=True)
def reset_cache():
    """Reset config cache between tests."""
    _reset_config_cache()
    yield
    _reset_config_cache()


@pytest.fixture
def browser_instance(tmp_path, monkeypatch):
    """Set up a temporary KOAN_ROOT with instance dir."""
    monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
    inst = tmp_path / "instance"
    inst.mkdir()
    return tmp_path


# ---------------------------------------------------------------------------
# Config reading
# ---------------------------------------------------------------------------


class TestGetBrowserConfig:
    """Test config.yaml browser section parsing."""

    def test_defaults_when_no_config(self, browser_instance):
        """Browser disabled by default with headless=True."""
        with patch("app.browser_access._load_config", return_value={}):
            config = get_browser_config()
        assert config["enabled"] is False
        assert config["headless"] is True
        assert config["allowed_domains"] == []

    def test_enabled_browser(self, browser_instance):
        with patch("app.browser_access._load_config", return_value={
            "browser": {"enabled": True}
        }):
            config = get_browser_config()
        assert config["enabled"] is True
        assert config["headless"] is True

    def test_visible_mode(self, browser_instance):
        with patch("app.browser_access._load_config", return_value={
            "browser": {"enabled": True, "headless": False}
        }):
            config = get_browser_config()
        assert config["headless"] is False

    def test_allowed_domains(self, browser_instance):
        with patch("app.browser_access._load_config", return_value={
            "browser": {
                "enabled": True,
                "allowed_domains": ["localhost", "docs.python.org"],
            }
        }):
            config = get_browser_config()
        assert config["allowed_domains"] == ["localhost", "docs.python.org"]

    def test_partial_config(self, browser_instance):
        """Missing keys use defaults."""
        with patch("app.browser_access._load_config", return_value={
            "browser": {"enabled": True}
        }):
            config = get_browser_config()
        assert config["headless"] is True
        assert config["allowed_domains"] == []


class TestIsBrowserEnabled:
    def test_disabled_by_default(self, browser_instance):
        with patch("app.browser_access._load_config", return_value={}):
            assert is_browser_enabled() is False

    def test_enabled(self, browser_instance):
        with patch("app.browser_access._load_config", return_value={
            "browser": {"enabled": True}
        }):
            assert is_browser_enabled() is True


# ---------------------------------------------------------------------------
# MCP config generation
# ---------------------------------------------------------------------------


class TestBuildMcpConfig:
    """Test MCP config dict generation."""

    def test_headless_config(self):
        config = build_mcp_config(headless=True)
        assert "mcpServers" in config
        assert "playwright" in config["mcpServers"]
        server = config["mcpServers"]["playwright"]
        assert server["command"] == "npx"
        assert "-y" in server["args"]
        assert "@playwright/mcp@latest" in server["args"]
        assert "--headless" in server["args"]

    def test_visible_config(self):
        config = build_mcp_config(headless=False)
        server = config["mcpServers"]["playwright"]
        assert "--headless" not in server["args"]

    def test_config_is_valid_json(self):
        """Config can be serialized to JSON."""
        config = build_mcp_config()
        json_str = json.dumps(config)
        parsed = json.loads(json_str)
        assert parsed == config


class TestWriteMcpConfig:
    """Test MCP config file writing."""

    def test_writes_config_file(self, browser_instance):
        with patch("app.browser_access._load_config", return_value={
            "browser": {"enabled": True, "headless": True}
        }):
            path = write_mcp_config()
        assert path is not None
        assert path.exists()
        content = json.loads(path.read_text())
        assert "mcpServers" in content
        assert "playwright" in content["mcpServers"]

    def test_returns_none_when_disabled(self, browser_instance):
        with patch("app.browser_access._load_config", return_value={}):
            assert write_mcp_config() is None

    def test_config_file_location(self, browser_instance):
        with patch("app.browser_access._load_config", return_value={
            "browser": {"enabled": True}
        }):
            path = write_mcp_config()
        expected = browser_instance / "instance" / ".mcp-playwright.json"
        assert path == expected

    def test_overwrites_existing_config(self, browser_instance):
        config_path = browser_instance / "instance" / ".mcp-playwright.json"
        config_path.write_text('{"old": true}')

        with patch("app.browser_access._load_config", return_value={
            "browser": {"enabled": True}
        }):
            write_mcp_config()

        content = json.loads(config_path.read_text())
        assert "old" not in content
        assert "mcpServers" in content


# ---------------------------------------------------------------------------
# CLI flag generation
# ---------------------------------------------------------------------------


class TestGetBrowserMcpFlags:
    """Test CLI flag generation for Claude invocations."""

    def test_returns_flags_when_enabled(self, browser_instance):
        with patch("app.browser_access._load_config", return_value={
            "browser": {"enabled": True}
        }):
            flags = get_browser_mcp_flags()
        assert len(flags) == 2
        assert flags[0] == "--mcp-config"
        assert ".mcp-playwright.json" in flags[1]

    def test_returns_empty_when_disabled(self, browser_instance):
        with patch("app.browser_access._load_config", return_value={}):
            flags = get_browser_mcp_flags()
        assert flags == []

    def test_creates_config_file(self, browser_instance):
        with patch("app.browser_access._load_config", return_value={
            "browser": {"enabled": True}
        }):
            flags = get_browser_mcp_flags()
        config_path = Path(flags[1])
        assert config_path.exists()


class TestGetBrowserFlagsForShell:
    """Test shell-safe flag string generation."""

    def test_returns_space_separated_flags(self, browser_instance):
        with patch("app.browser_access._load_config", return_value={
            "browser": {"enabled": True}
        }):
            result = get_browser_flags_for_shell()
        assert "--mcp-config" in result
        assert ".mcp-playwright.json" in result

    def test_returns_empty_string_when_disabled(self, browser_instance):
        with patch("app.browser_access._load_config", return_value={}):
            result = get_browser_flags_for_shell()
        assert result == ""


# ---------------------------------------------------------------------------
# Status reporting
# ---------------------------------------------------------------------------


class TestGetBrowserStatus:
    """Test status dict generation."""

    def test_disabled_status(self, browser_instance):
        with patch("app.browser_access._load_config", return_value={}):
            status = get_browser_status()
        assert status["enabled"] is False
        assert status["headless"] is True
        assert status["config_file_exists"] is False

    def test_enabled_status(self, browser_instance):
        with patch("app.browser_access._load_config", return_value={
            "browser": {"enabled": True}
        }):
            # Write config to make config_file_exists true
            write_mcp_config()
            status = get_browser_status()
        assert status["enabled"] is True
        assert status["config_file_exists"] is True

    def test_npx_check(self, browser_instance):
        with patch("app.browser_access._load_config", return_value={}):
            with patch("shutil.which", return_value="/usr/bin/npx"):
                status = get_browser_status()
            assert status["npx_available"] is True

            with patch("shutil.which", return_value=None):
                _reset_config_cache()
                status = get_browser_status()
            assert status["npx_available"] is False


class TestFormatBrowserStatus:
    """Test human-readable status formatting."""

    def test_disabled_message(self, browser_instance):
        with patch("app.browser_access._load_config", return_value={}):
            output = format_browser_status()
        assert "DISABLED" in output
        assert "config.yaml" in output

    def test_enabled_message(self, browser_instance):
        with patch("app.browser_access._load_config", return_value={
            "browser": {"enabled": True}
        }):
            with patch("shutil.which", return_value="/usr/bin/npx"):
                output = format_browser_status()
        assert "ENABLED" in output
        assert "headless" in output
        assert "npx: available" in output

    def test_npx_missing_warning(self, browser_instance):
        with patch("app.browser_access._load_config", return_value={
            "browser": {"enabled": True}
        }):
            with patch("shutil.which", return_value=None):
                output = format_browser_status()
        assert "NOT FOUND" in output

    def test_domain_restrictions_shown(self, browser_instance):
        with patch("app.browser_access._load_config", return_value={
            "browser": {
                "enabled": True,
                "allowed_domains": ["localhost", "example.com"],
            }
        }):
            with patch("shutil.which", return_value="/usr/bin/npx"):
                output = format_browser_status()
        assert "localhost" in output
        assert "example.com" in output

    def test_no_domain_restrictions(self, browser_instance):
        with patch("app.browser_access._load_config", return_value={
            "browser": {"enabled": True}
        }):
            with patch("shutil.which", return_value="/usr/bin/npx"):
                output = format_browser_status()
        assert "all (no restrictions)" in output


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


class TestCLIEntryPoint:
    """Test python3 -m app.browser_access."""

    # cwd must be the koan/ dir (where app/ lives) for module resolution
    _koan_dir = str(Path(__file__).parent.parent)

    def test_flags_command(self, browser_instance):
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "app.browser_access", "flags"],
            capture_output=True, text=True, timeout=10,
            cwd=self._koan_dir,
            env={**os.environ, "KOAN_ROOT": str(browser_instance)},
        )
        # Disabled by default, so empty output
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_status_command(self, browser_instance):
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "app.browser_access", "status"],
            capture_output=True, text=True, timeout=10,
            cwd=self._koan_dir,
            env={**os.environ, "KOAN_ROOT": str(browser_instance)},
        )
        assert result.returncode == 0
        assert "DISABLED" in result.stdout

    def test_unknown_command(self, browser_instance):
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "app.browser_access", "foobar"],
            capture_output=True, text=True, timeout=10,
            cwd=self._koan_dir,
            env={**os.environ, "KOAN_ROOT": str(browser_instance)},
        )
        assert result.returncode != 0


# ---------------------------------------------------------------------------
# Awake.py integration
# ---------------------------------------------------------------------------


class TestAwakeBrowserCommand:
    """Test /browser command in awake.py."""

    @patch("app.awake.send_telegram")
    def test_browser_command_disabled(self, mock_send):
        with patch("app.browser_access._load_config", return_value={}):
            from app.awake import handle_command
            handle_command("/browser")
        mock_send.assert_called_once()
        assert "DISABLED" in mock_send.call_args[0][0]

    @patch("app.awake.send_telegram")
    def test_browser_command_enabled(self, mock_send):
        with patch("app.browser_access._load_config", return_value={
            "browser": {"enabled": True}
        }):
            with patch("shutil.which", return_value="/usr/bin/npx"):
                from app.awake import handle_command
                handle_command("/browser")
        mock_send.assert_called_once()
        assert "ENABLED" in mock_send.call_args[0][0]

    @patch("app.awake.send_telegram")
    def test_help_includes_browser(self, mock_send):
        from app.awake import handle_command
        handle_command("/help")
        mock_send.assert_called_once()
        assert "/browser" in mock_send.call_args[0][0]


# ---------------------------------------------------------------------------
# run.sh integration
# ---------------------------------------------------------------------------


class TestRunShIntegration:
    """Test that run.sh correctly references browser_access."""

    def test_run_sh_has_browser_flags(self):
        """run.sh should call get_browser_flags_for_shell."""
        run_sh = Path(__file__).parent.parent / "run.sh"
        content = run_sh.read_text()
        assert "browser_access" in content
        assert "BROWSER_FLAGS" in content
        assert "get_browser_flags_for_shell" in content

    def test_run_sh_injects_browser_context(self):
        """run.sh should inject browser capability text into prompt."""
        run_sh = Path(__file__).parent.parent / "run.sh"
        content = run_sh.read_text()
        assert "Browser Access" in content
        assert "Playwright MCP" in content
