from __future__ import annotations

from core.circuit_breaker import CircuitBreaker, CircuitBreakerConfig


def test_circuit_starts_closed():
    breaker = CircuitBreaker()

    decision = breaker.check()

    assert decision.allowed
    assert decision.state == "closed"


def test_circuit_opens_after_failure_budget():
    breaker = CircuitBreaker(CircuitBreakerConfig(max_failures=2, max_consecutive_failures=3))

    assert breaker.record_failure("first").allowed
    decision = breaker.record_failure("second")

    assert not decision.allowed
    assert decision.state == "open"
    assert "failure budget" in decision.reason


def test_circuit_opens_after_budget_denial():
    breaker = CircuitBreaker(CircuitBreakerConfig(max_budget_denials=1))

    decision = breaker.record_budget_denial("too expensive")

    assert not decision.allowed
    assert breaker.check().state == "open"
    assert "budget denial" in decision.reason


def test_success_resets_consecutive_failures():
    breaker = CircuitBreaker(CircuitBreakerConfig(max_failures=3, max_consecutive_failures=2))

    assert breaker.record_failure("first").allowed
    assert breaker.record_success().allowed
    assert breaker.record_failure("second").allowed

    assert breaker.snapshot()["consecutive_failures"] == 1
