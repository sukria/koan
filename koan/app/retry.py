"""Retry-with-backoff utility for transient network failures.

Provides a generic retry wrapper used by send_telegram() and run_gh()
to handle transient errors (connection resets, DNS failures, timeouts)
instead of failing silently on the first attempt.
"""

import sys
import time
from typing import Callable, Optional, Sequence, Tuple, Type


DEFAULT_BACKOFF = (1, 2, 4)
DEFAULT_MAX_ATTEMPTS = 3


def retry_with_backoff(
    fn: Callable,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff: Sequence[float] = DEFAULT_BACKOFF,
    retryable: Tuple[Type[BaseException], ...] = (),
    is_transient: Optional[Callable[[BaseException], bool]] = None,
    label: str = "",
):
    """Call fn() with exponential backoff on transient failures.

    Args:
        fn: Zero-argument callable to invoke.
        max_attempts: Maximum number of attempts (default 3).
        backoff: Sleep durations between retries (seconds).
        retryable: Exception types that trigger a retry.
        is_transient: Optional predicate for finer filtering of retryable
            exceptions. If provided and returns False, the exception is
            re-raised immediately without retry.
        label: Label for log messages.

    Returns:
        The return value of fn().

    Raises:
        The last exception if all attempts fail.
    """
    last_exc = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except retryable as exc:
            if is_transient and not is_transient(exc):
                raise
            last_exc = exc
            if attempt < max_attempts - 1:
                delay = backoff[min(attempt, len(backoff) - 1)]
                print(
                    f"[retry] {label or 'call'} failed "
                    f"(attempt {attempt + 1}/{max_attempts}): {exc} "
                    f"— retrying in {delay}s",
                    file=sys.stderr,
                )
                time.sleep(delay)
    raise last_exc


# -- Transient error detection ------------------------------------------------

# Keywords in stderr/error messages that suggest transient network issues.
_TRANSIENT_KEYWORDS = (
    "connection reset",
    "connection refused",
    "connection timed out",
    "network is unreachable",
    "name resolution",
    "dns",
    "temporary failure",
    "timed out",
    "timeout",
    "eof",
    "broken pipe",
    "ssl",
    "503",
    "502",
    "429",
)


def is_gh_transient(exc: BaseException) -> bool:
    """Return True if a RuntimeError from run_gh looks like a transient failure."""
    msg = str(exc).lower()
    return any(kw in msg for kw in _TRANSIENT_KEYWORDS)
