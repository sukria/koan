"""Tests for app.retry — retry-with-backoff utility."""

import subprocess
from unittest.mock import patch

import pytest

from app.retry import retry_with_backoff, is_gh_transient


class TestRetryWithBackoff:
    """Core retry_with_backoff() behaviour."""

    @patch("app.retry.time.sleep")
    def test_succeeds_first_try(self, mock_sleep):
        result = retry_with_backoff(
            lambda: "ok",
            retryable=(RuntimeError,),
        )
        assert result == "ok"
        mock_sleep.assert_not_called()

    @patch("app.retry.time.sleep")
    def test_retries_on_retryable_exception(self, mock_sleep):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("transient")
            return "recovered"

        result = retry_with_backoff(
            flaky,
            retryable=(RuntimeError,),
            label="test",
        )
        assert result == "recovered"
        assert calls["n"] == 3
        assert mock_sleep.call_count == 2

    @patch("app.retry.time.sleep")
    def test_uses_backoff_delays(self, mock_sleep):
        calls = {"n": 0}

        def always_fail():
            calls["n"] += 1
            raise OSError("down")

        with pytest.raises(OSError, match="down"):
            retry_with_backoff(
                always_fail,
                max_attempts=3,
                backoff=(1, 2, 4),
                retryable=(OSError,),
            )

        assert mock_sleep.call_args_list[0][0] == (1,)
        assert mock_sleep.call_args_list[1][0] == (2,)

    @patch("app.retry.time.sleep")
    def test_raises_last_exception_on_exhaustion(self, mock_sleep):
        def always_fail():
            raise RuntimeError("persistent")

        with pytest.raises(RuntimeError, match="persistent"):
            retry_with_backoff(
                always_fail,
                max_attempts=2,
                retryable=(RuntimeError,),
            )

    @patch("app.retry.time.sleep")
    def test_non_retryable_exception_propagates_immediately(self, mock_sleep):
        def bad():
            raise ValueError("not retryable")

        with pytest.raises(ValueError, match="not retryable"):
            retry_with_backoff(
                bad,
                retryable=(RuntimeError,),
            )
        mock_sleep.assert_not_called()

    @patch("app.retry.time.sleep")
    def test_is_transient_filter(self, mock_sleep):
        """When is_transient returns False, exception is re-raised immediately."""
        def fail():
            raise RuntimeError("not found")

        with pytest.raises(RuntimeError, match="not found"):
            retry_with_backoff(
                fail,
                retryable=(RuntimeError,),
                is_transient=lambda e: "timeout" in str(e),
            )
        mock_sleep.assert_not_called()

    @patch("app.retry.time.sleep")
    def test_is_transient_allows_retry(self, mock_sleep):
        """When is_transient returns True, retry proceeds."""
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 2:
                raise RuntimeError("connection timeout")
            return "ok"

        result = retry_with_backoff(
            flaky,
            retryable=(RuntimeError,),
            is_transient=lambda e: "timeout" in str(e),
        )
        assert result == "ok"
        assert mock_sleep.call_count == 1

    @patch("app.retry.time.sleep")
    def test_single_attempt_no_retry(self, mock_sleep):
        def fail():
            raise RuntimeError("once")

        with pytest.raises(RuntimeError):
            retry_with_backoff(
                fail,
                max_attempts=1,
                retryable=(RuntimeError,),
            )
        mock_sleep.assert_not_called()


class TestIsGhTransient:
    """Tests for is_gh_transient() keyword detection."""

    @pytest.mark.parametrize("msg", [
        "gh failed: gh pr view... — connection reset by peer",
        "gh failed: gh api... — connection timed out",
        "gh failed: gh pr list... — timeout waiting for response",
        "gh failed: gh api... — 502 Bad Gateway",
        "gh failed: gh api... — 503 Service Unavailable",
        "gh failed: gh api... — 429 rate limit exceeded",
        "gh failed: gh pr... — SSL handshake error",
        "gh failed: gh api... — dns resolution failed",
    ])
    def test_transient_errors(self, msg):
        assert is_gh_transient(RuntimeError(msg)) is True

    @pytest.mark.parametrize("msg", [
        "gh failed: gh pr view... — not found",
        "gh failed: gh api... — permission denied",
        "gh failed: gh pr... — authentication required",
        "gh failed: gh issue... — repository not found",
    ])
    def test_permanent_errors(self, msg):
        assert is_gh_transient(RuntimeError(msg)) is False
