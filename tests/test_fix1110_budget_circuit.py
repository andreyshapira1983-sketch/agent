"""A budget denial during the half-open probe re-opens the breaker."""
from __future__ import annotations

from core.circuit_breaker import CircuitBreaker, CircuitBreakerConfig


def _half_open() -> CircuitBreaker:
    breaker = CircuitBreaker(CircuitBreakerConfig(max_budget_denials=1, cooldown_checks=2))
    breaker.record_budget_denial("day budget exhausted")
    breaker.check()
    assert breaker.check().state == "half-open"
    return breaker


def test_budget_denial_in_half_open_reopens() -> None:
    breaker = _half_open()

    decision = breaker.record_budget_denial("still exhausted")

    assert not decision.allowed
    assert decision.state == "open"
    assert "still exhausted" in decision.reason
    assert breaker.state == "open"
    assert not breaker.check().allowed, "the cooldown starts over"
    assert breaker.snapshot()["budget_denials"] == 2


def test_budget_denial_while_open_keeps_it_open() -> None:
    breaker = CircuitBreaker(CircuitBreakerConfig(max_budget_denials=1, cooldown_checks=5))
    breaker.record_budget_denial("first")

    decision = breaker.record_budget_denial("second")

    assert not decision.allowed
    assert breaker.state == "open"
    assert "first" in breaker.opened_reason
