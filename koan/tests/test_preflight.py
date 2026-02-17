"""Tests for preflight module — pre-mission quota checks."""

from unittest.mock import patch, MagicMock

import pytest

from app.preflight import preflight_quota_check


# ---------------------------------------------------------------------------
# Budget mode disabled
# ---------------------------------------------------------------------------


class TestBudgetModeDisabled:
    """When budget_mode is 'disabled', skip the check entirely."""

    def test_returns_ok_when_budget_disabled(self):
        with patch("app.usage_tracker._get_budget_mode", return_value="disabled"):
            ok, error = preflight_quota_check("/fake/path", "/fake/instance")

        assert ok is True
        assert error is None

    def test_proceeds_when_budget_mode_full(self):
        """When budget mode is 'full', check should proceed to provider."""
        mock_provider = MagicMock()
        mock_provider.check_quota_available.return_value = (True, "")

        with patch("app.usage_tracker._get_budget_mode", return_value="full"), \
             patch("app.provider.get_provider", return_value=mock_provider):
            ok, error = preflight_quota_check("/fake/path", "/fake/instance")

        assert ok is True
        assert error is None
        mock_provider.check_quota_available.assert_called_once_with("/fake/path")

    def test_proceeds_when_budget_mode_session_only(self):
        """session_only mode should still check quota."""
        mock_provider = MagicMock()
        mock_provider.check_quota_available.return_value = (True, "")

        with patch("app.usage_tracker._get_budget_mode",
                   return_value="session_only"), \
             patch("app.provider.get_provider", return_value=mock_provider):
            ok, error = preflight_quota_check("/fake/path", "/fake/instance")

        assert ok is True
        mock_provider.check_quota_available.assert_called_once()


# ---------------------------------------------------------------------------
# Budget mode import error
# ---------------------------------------------------------------------------


class TestBudgetModeImportError:
    """If budget mode check fails, proceed optimistically to provider."""

    def test_proceeds_on_import_error(self):
        """Import failure of usage_tracker should not block missions."""
        mock_provider = MagicMock()
        mock_provider.check_quota_available.return_value = (True, "")

        with patch("app.usage_tracker._get_budget_mode",
                   side_effect=ImportError("no module")), \
             patch("app.provider.get_provider", return_value=mock_provider):
            ok, error = preflight_quota_check("/fake/path", "/fake/instance")

        assert ok is True
        assert error is None

    def test_proceeds_on_runtime_error(self):
        """Runtime error in _get_budget_mode should not block."""
        mock_provider = MagicMock()
        mock_provider.check_quota_available.return_value = (True, "")

        with patch("app.usage_tracker._get_budget_mode",
                   side_effect=RuntimeError("broken")), \
             patch("app.provider.get_provider", return_value=mock_provider):
            ok, error = preflight_quota_check("/fake/path", "/fake/instance")

        assert ok is True


# ---------------------------------------------------------------------------
# Provider resolution failure
# ---------------------------------------------------------------------------


class TestProviderResolutionFailure:
    """If provider can't be resolved, proceed optimistically."""

    def test_returns_ok_when_provider_import_fails(self):
        with patch("app.usage_tracker._get_budget_mode",
                   return_value="session_only"), \
             patch("app.provider.get_provider",
                   side_effect=ImportError("no provider")):
            ok, error = preflight_quota_check("/fake/path", "/fake/instance")

        assert ok is True
        assert error is None

    def test_returns_ok_when_provider_raises_runtime_error(self):
        with patch("app.usage_tracker._get_budget_mode",
                   return_value="session_only"), \
             patch("app.provider.get_provider",
                   side_effect=RuntimeError("misconfigured")):
            ok, error = preflight_quota_check("/fake/path", "/fake/instance")

        assert ok is True
        assert error is None


# ---------------------------------------------------------------------------
# Quota available
# ---------------------------------------------------------------------------


class TestQuotaAvailable:
    """When quota is available, return (True, None)."""

    def test_ok_when_provider_reports_available(self):
        mock_provider = MagicMock()
        mock_provider.check_quota_available.return_value = (True, "")

        with patch("app.usage_tracker._get_budget_mode",
                   return_value="session_only"), \
             patch("app.provider.get_provider", return_value=mock_provider):
            ok, error = preflight_quota_check(
                "/fake/path", "/fake/instance", "myproject")

        assert ok is True
        assert error is None
        mock_provider.check_quota_available.assert_called_once_with("/fake/path")

    def test_passes_project_path_to_provider(self):
        """The project_path argument should reach the provider."""
        mock_provider = MagicMock()
        mock_provider.check_quota_available.return_value = (True, "")

        with patch("app.usage_tracker._get_budget_mode",
                   return_value="full"), \
             patch("app.provider.get_provider", return_value=mock_provider):
            preflight_quota_check("/specific/project", "/inst")

        mock_provider.check_quota_available.assert_called_once_with(
            "/specific/project")


# ---------------------------------------------------------------------------
# Quota exhausted
# ---------------------------------------------------------------------------


class TestQuotaExhausted:
    """When quota is exhausted, return (False, error_detail)."""

    def test_returns_false_with_error_detail(self):
        mock_provider = MagicMock()
        mock_provider.check_quota_available.return_value = (
            False, "Rate limit exceeded. Resets at 2026-02-16T08:00:00Z")

        with patch("app.usage_tracker._get_budget_mode",
                   return_value="session_only"), \
             patch("app.provider.get_provider", return_value=mock_provider):
            ok, error = preflight_quota_check("/fake/path", "/fake/instance")

        assert ok is False
        assert "Rate limit exceeded" in error

    def test_error_detail_is_string(self):
        """Error detail should be a non-empty string."""
        mock_provider = MagicMock()
        mock_provider.check_quota_available.return_value = (
            False, "quota exhausted")

        with patch("app.usage_tracker._get_budget_mode",
                   return_value="full"), \
             patch("app.provider.get_provider", return_value=mock_provider):
            ok, error = preflight_quota_check("/fake/path", "/fake/instance")

        assert isinstance(error, str)
        assert len(error) > 0

    def test_returns_provider_error_verbatim(self):
        """The error message from the provider should be passed through."""
        detail = "Exceeded daily token limit. Try again after 08:00 UTC."
        mock_provider = MagicMock()
        mock_provider.check_quota_available.return_value = (False, detail)

        with patch("app.usage_tracker._get_budget_mode",
                   return_value="session_only"), \
             patch("app.provider.get_provider", return_value=mock_provider):
            ok, error = preflight_quota_check("/fake/path", "/fake/instance")

        assert error == detail
