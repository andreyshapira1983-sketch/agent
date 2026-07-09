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


# ---------------------------------------------------------------------------
# Half-open state tests
# ---------------------------------------------------------------------------

def test_circuit_blocks_before_cooldown_expires():
    """check() calls while open are blocked until cooldown_checks is reached."""
    cfg = CircuitBreakerConfig(max_failures=1, cooldown_checks=3)
    breaker = CircuitBreaker(cfg)
    breaker.record_failure("trip")

    assert breaker.state == "open"
    assert not breaker.check().allowed   # 1st check
    assert not breaker.check().allowed   # 2nd check


def test_circuit_enters_half_open_after_cooldown():
    """After cooldown_checks blocked calls, the next check enters half-open."""
    cfg = CircuitBreakerConfig(max_failures=1, cooldown_checks=3)
    breaker = CircuitBreaker(cfg)
    breaker.record_failure("trip")

    breaker.check()  # 1
    breaker.check()  # 2
    d = breaker.check()  # 3 -> transitions to half-open

    assert d.allowed
    assert d.state == "half-open"
    assert breaker.state == "half-open"


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


def test_snapshot_includes_half_open_fields():
    """snapshot() exposes open_check_count and cooldown_checks."""
    cfg = CircuitBreakerConfig(max_failures=1, cooldown_checks=3)
    breaker = CircuitBreaker(cfg)
    breaker.record_failure("trip")
    breaker.check()

    snap = breaker.snapshot()
    assert snap["open_check_count"] == 1
    assert snap["config"]["cooldown_checks"] == 3

