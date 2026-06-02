"""Unit tests for CLIRateLimiter (core/rate_limiter.py)."""
from __future__ import annotations

import time

import pytest

from core.rate_limiter import CLIRateLimiter, RateLimitResult


class TestConstructor:
    def test_default_params(self):
        rl = CLIRateLimiter()
        assert rl.max_requests == 30
        assert rl.window_seconds == 60.0

    def test_custom_params(self):
        rl = CLIRateLimiter(max_requests=10, window_seconds=5.0)
        assert rl.max_requests == 10
        assert rl.window_seconds == 5.0

    def test_invalid_max_requests_raises(self):
        with pytest.raises(ValueError, match="max_requests"):
            CLIRateLimiter(max_requests=0)

    def test_invalid_window_seconds_raises(self):
        with pytest.raises(ValueError, match="window_seconds"):
            CLIRateLimiter(max_requests=5, window_seconds=0.0)


class TestConsumeAllowed:
    def test_fresh_bucket_allows_first_request(self):
        rl = CLIRateLimiter(max_requests=5, window_seconds=60.0)
        result = rl.consume()
        assert isinstance(result, RateLimitResult)
        assert result.allowed is True
        assert result.retry_after_seconds == 0.0

    def test_tokens_decrease_after_consume(self):
        rl = CLIRateLimiter(max_requests=5, window_seconds=60.0)
        rl.consume()
        assert rl._tokens < 5.0

    def test_burst_up_to_capacity_is_allowed(self):
        rl = CLIRateLimiter(max_requests=5, window_seconds=60.0)
        results = [rl.consume() for _ in range(5)]
        assert all(r.allowed for r in results)

    def test_tokens_remaining_decrements(self):
        rl = CLIRateLimiter(max_requests=5, window_seconds=60.0)
        r1 = rl.consume()
        r2 = rl.consume()
        assert r1.tokens_remaining > r2.tokens_remaining


class TestConsumeBlocked:
    def test_request_beyond_capacity_is_denied(self):
        rl = CLIRateLimiter(max_requests=3, window_seconds=60.0)
        for _ in range(3):
            rl.consume()
        result = rl.consume()
        assert result.allowed is False

    def test_denied_result_has_positive_retry_after(self):
        rl = CLIRateLimiter(max_requests=2, window_seconds=60.0)
        rl.consume()
        rl.consume()
        result = rl.consume()
        assert result.allowed is False
        assert result.retry_after_seconds > 0.0

    def test_denied_result_tokens_remaining_is_below_one(self):
        rl = CLIRateLimiter(max_requests=1, window_seconds=60.0)
        rl.consume()
        result = rl.consume()
        assert result.tokens_remaining < 1.0

    def test_retry_after_is_bounded(self):
        """retry_after must be <= window_seconds (worst-case empty bucket)."""
        rl = CLIRateLimiter(max_requests=5, window_seconds=10.0)
        # Drain the bucket completely
        for _ in range(5):
            rl.consume()
        result = rl.consume()
        assert result.retry_after_seconds <= rl.window_seconds


class TestRefill:
    def test_tokens_refill_after_wait(self):
        """After sleeping, the bucket should have more tokens."""
        rl = CLIRateLimiter(max_requests=10, window_seconds=1.0)
        # Drain 5 tokens
        for _ in range(5):
            rl.consume()
        snapshot_before = rl._tokens
        time.sleep(0.3)  # 30% of the 1-second window → ~3 tokens refilled
        rl._refill()
        assert rl._tokens > snapshot_before

    def test_tokens_never_exceed_capacity(self):
        """Waiting longer than window_seconds must not overshoot max_requests."""
        rl = CLIRateLimiter(max_requests=5, window_seconds=0.1)
        time.sleep(0.5)  # 5× the window
        rl._refill()
        assert rl._tokens <= float(rl.max_requests)

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
