"""CLI session rate limiter: a token bucket (capacity ``max_requests``, refill ``max_requests / window_seconds`` per second); single-threaded CLI use only, no lock."""
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
    """Token-bucket rate limiter for interactive CLI sessions."""

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


    def consume(self) -> RateLimitResult:
        """Attempt to consume one token."""
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
        return min(self._tokens + elapsed * self._refill_rate, float(self.max_requests))


    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(
            self._tokens + elapsed * self._refill_rate,
            float(self.max_requests),
        )
        self._last_refill = now
