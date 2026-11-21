"""Unit tests for CLIRateLimiter (core/rate_limiter.py)."""
from __future__ import annotations

import time

import pytest

from core.rate_limiter import CLIRateLimiter


class TestConstructor:
    def test_invalid_max_requests_raises(self):
        with pytest.raises(ValueError, match="max_requests"):
            CLIRateLimiter(max_requests=0)

    def test_invalid_window_seconds_raises(self):
        with pytest.raises(ValueError, match="window_seconds"):
            CLIRateLimiter(max_requests=5, window_seconds=0.0)


class TestConsumeBlocked:
    def test_denied_result_has_positive_retry_after(self):
        rl = CLIRateLimiter(max_requests=2, window_seconds=60.0)
        rl.consume()
        rl.consume()
        result = rl.consume()
        assert result.allowed is False
        assert result.retry_after_seconds > 0.0


class TestRefill:
    def test_after_refill_request_allowed_again(self):
        """After the bucket refills, a previously-denied request should now be allowed."""
        rl = CLIRateLimiter(max_requests=1, window_seconds=0.2)
        rl.consume()                          # drain
        assert not rl.consume().allowed       # denied
        time.sleep(0.25)                      # wait for refill
        assert rl.consume().allowed           # now allowed


class TestPeek:
    def test_peek_does_not_consume_tokens(self):
        rl = CLIRateLimiter(max_requests=5, window_seconds=60.0)
        before = rl._tokens
        rl.peek()
        # peek() shouldn't change internal state
        assert rl._tokens == before

    def test_peek_returns_at_most_max_requests(self):
        rl = CLIRateLimiter(max_requests=5, window_seconds=0.01)
        time.sleep(0.1)
        projected = rl.peek()
        assert projected <= float(rl.max_requests)
