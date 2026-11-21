from __future__ import annotations

from core.circuit_breaker import CircuitBreaker, CircuitBreakerConfig

# ---------------------------------------------------------------------------
# Half-open state tests
# ---------------------------------------------------------------------------


def test_half_open_probe_success_closes_circuit():
    """record_success() in half-open closes the circuit."""
    cfg = CircuitBreakerConfig(max_failures=1, cooldown_checks=2)
    breaker = CircuitBreaker(cfg)
    breaker.record_failure("trip")
    breaker.check()  # 1
    breaker.check()  # 2 -> half-open

    d = breaker.record_success()

    assert d.allowed
    assert d.state == "closed"
    assert breaker.state == "closed"
    assert "probe succeeded" in d.reason


def test_half_open_probe_failure_reopens_circuit():
    """record_failure() in half-open reopens the circuit and resets cooldown."""
    cfg = CircuitBreakerConfig(max_failures=1, cooldown_checks=2)
    breaker = CircuitBreaker(cfg)
    breaker.record_failure("trip")
    breaker.check()  # 1
    breaker.check()  # 2 -> half-open

    d = breaker.record_failure("probe failed")

    assert not d.allowed
    assert d.state == "open"
    assert "probe failed" in d.reason
    # Cooldown counter is reset: next check should NOT immediately re-enter half-open
    assert not breaker.check().allowed


def test_half_open_allows_full_recovery_cycle():
    """Full cycle: closed -> open -> half-open -> closed works end to end."""
    cfg = CircuitBreakerConfig(max_failures=1, cooldown_checks=1)
    breaker = CircuitBreaker(cfg)

    # Trip
    breaker.record_failure("trip")
    assert breaker.state == "open"

    # One check -> half-open
    d = breaker.check()
    assert d.state == "half-open"

    # Probe succeeds -> closed
    d = breaker.record_success()
    assert d.state == "closed"
    assert breaker.state == "closed"


def test_success_while_open_does_not_close():
    """record_success() while open should NOT close the circuit."""
    cfg = CircuitBreakerConfig(max_failures=1, cooldown_checks=5)
    breaker = CircuitBreaker(cfg)
    breaker.record_failure("trip")

    d = breaker.record_success()

    assert not d.allowed
    assert breaker.state == "open"
