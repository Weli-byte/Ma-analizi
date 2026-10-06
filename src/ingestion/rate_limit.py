"""S12: rate-limit awareness + exponential backoff for provider calls."""

import time
from collections.abc import Callable

from .provider import RateLimitedError


class RateLimiter:
    """Simple token bucket: `max_calls` per `period_seconds`, refilled continuously. `sleep_fn`
    is injectable so tests never actually sleep."""

    def __init__(self, max_calls: int, period_seconds: float, sleep_fn: Callable[[float], None] = time.sleep):
        if max_calls <= 0 or period_seconds <= 0:
            raise ValueError("max_calls and period_seconds must be positive")
        self.max_calls = max_calls
        self.period_seconds = period_seconds
        self._sleep = sleep_fn
        self._tokens = float(max_calls)
        self._last = time.monotonic()

    def acquire(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last
        self._last = now
        self._tokens = min(self.max_calls, self._tokens + elapsed * (self.max_calls / self.period_seconds))
        if self._tokens < 1.0:
            wait = (1.0 - self._tokens) * (self.period_seconds / self.max_calls)
            self._sleep(wait)
            self._tokens = 0.0
            self._last = time.monotonic()
        else:
            self._tokens -= 1.0


def with_backoff[T](
    fn: Callable[[], T],
    max_retries: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> T:
    """Retries `fn()` on `RateLimitedError` with exponential backoff (`base_delay * 2**attempt`,
    capped at `max_delay`). A plain `ProviderError` (not rate-limit-specific) is NOT assumed
    transient and propagates immediately -- retrying an auth failure or a malformed request
    forever would hide a real bug, not resilience."""
    attempt = 0
    while True:
        try:
            return fn()
        except RateLimitedError:
            if attempt >= max_retries:
                raise
            delay = min(base_delay * (2**attempt), max_delay)
            sleep_fn(delay)
            attempt += 1
        # a plain ProviderError (not RateLimitedError) is not caught above and propagates as-is
