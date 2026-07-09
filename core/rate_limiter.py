"""CLI session rate limiter — token bucket (T8 / §6 Security).

Prevents runaway or abusive request bursts from a CLI session.
One :class:`CLIRateLimiter` instance is created per interactive session
and checked before each agent invocation.

Token-bucket contract
---------------------
* Bucket capacity = ``max_requests`` tokens.
* Tokens refill continuously at ``max_requests / window_seconds`` per second.
* Each request consumes one token.
* If the bucket is empty the request is denied and the caller is told how
  many seconds to wait before the next token becomes available.

Thread safety: single-threaded CLI use only.  No lock is needed.
"""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitResult:
    """Outcome of a :meth:`CLIRateLimiter.consume` call."""

    allowed: bool
    tokens_remaining: float
    # 0.0 when *allowed* is True; positive when the bucket was empty.
    retry_after_seconds: float


class CLIRateLimiter:
    """Token-bucket rate limiter for interactive CLI sessions.

    Parameters
    ----------
    max_requests:
        Bucket capacity and refill ceiling.  Also the burst allowance at
        the start of a fresh session.
    window_seconds:
        Time (in seconds) it takes to fully refill an empty bucket.
        ``refill_rate = max_requests / window_seconds`` tokens/second.

    Examples
    --------
    >>> rl = CLIRateLimiter(max_requests=5, window_seconds=10.0)
    >>> rl.consume().allowed
    True
    """

    def __init__(
        self,
        max_requests: int = 30,
        window_seconds: float = 60.0,
    ) -> None:
        if max_requests < 1:
            raise ValueError(f"max_requests must be >= 1, got {max_requests}")
        if window_seconds <= 0:
            raise ValueError(f"window_seconds must be > 0, got {window_seconds}")
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._refill_rate: float = max_requests / window_seconds  # tokens/second
        self._tokens: float = float(max_requests)  # start full
        self._last_refill: float = time.monotonic()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def consume(self) -> RateLimitResult:
        """Attempt to consume one token.

        Returns a :class:`RateLimitResult`.  When ``allowed=True`` the
        caller may proceed.  When ``allowed=False`` the caller should
        surface ``retry_after_seconds`` to the user and skip the request.
        """
        self._refill()
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return RateLimitResult(
                allowed=True,
                tokens_remaining=self._tokens,
                retry_after_seconds=0.0,
            )
        # Bucket empty — compute how long until the next token arrives.
        wait = (1.0 - self._tokens) / self._refill_rate
        return RateLimitResult(
            allowed=False,
            tokens_remaining=self._tokens,
            retry_after_seconds=round(wait, 2),
        )

    def peek(self) -> float:
        """Return current token count without consuming or advancing time."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        projected = min(self._tokens + elapsed * self._refill_rate, float(self.max_requests))
        return projected

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(
            self._tokens + elapsed * self._refill_rate,
            float(self.max_requests),
        )
        self._last_refill = now
