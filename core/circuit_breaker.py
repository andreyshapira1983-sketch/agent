"""Circuit breaker for bounded autonomous runtime runs.

States
------
closed    Normal operation. All requests pass through.
open      Tripped. All requests blocked. Counts down via check() calls.
half-open Cooldown expired. ONE probe request passes through.
          Success -> closed. Failure -> open (cooldown resets).

Transition diagram::

    closed ──(budget/consecutive failures)──> open
    open   ──(cooldown_checks exhausted)────> half-open
    half-open ──(record_success)────────────> closed
    half-open ──(record_failure)────────────> open  (cooldown resets)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


CircuitState = Literal["closed", "open", "half-open"]


@dataclass(frozen=True)
class CircuitBreakerConfig:
    max_failures: int = 2
    max_consecutive_failures: int = 2
    max_budget_denials: int = 1
    # Number of check() calls while open before entering half-open.
    # Provides a deterministic, call-count-based cooldown.
    cooldown_checks: int = 3


@dataclass(frozen=True)
class CircuitDecision:
    allowed: bool
    state: CircuitState
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "state": self.state,
            "reason": self.reason,
        }


@dataclass
class CircuitBreaker:
    config: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    state: CircuitState = "closed"
    failures: int = 0
    consecutive_failures: int = 0
    budget_denials: int = 0
    opened_reason: str = ""
    # Counts check() calls received while in the open state.
    # When this reaches config.cooldown_checks the circuit enters half-open.
    _open_check_count: int = 0

    def check(self) -> CircuitDecision:
        if self.state == "closed":
            return CircuitDecision(True, "closed", "closed")
        if self.state == "half-open":
            return CircuitDecision(True, "half-open", "half-open: probe allowed")
        # state == "open"
        self._open_check_count += 1
        if self._open_check_count >= self.config.cooldown_checks:
            self.state = "half-open"
            self._open_check_count = 0
            return CircuitDecision(True, "half-open", "half-open: probe allowed")
        return CircuitDecision(False, "open", self.opened_reason)

    def record_success(self) -> CircuitDecision:
        if self.state == "half-open":
            # Probe succeeded — close the circuit.
            self.state = "closed"
            self.consecutive_failures = 0
            self._open_check_count = 0
            return CircuitDecision(True, "closed", "half-open probe succeeded: circuit closed")
        if self.state == "open":
            # Success signal while open doesn't affect the cooldown countdown.
            return CircuitDecision(False, "open", self.opened_reason)
        # closed
        self.consecutive_failures = 0
        return CircuitDecision(True, "closed", "closed")

    def record_failure(self, reason: str) -> CircuitDecision:
        if self.state == "half-open":
            # Probe failed — reopen and reset the cooldown counter.
            self._open_check_count = 0
            return self._open(f"half-open probe failed: {reason}")
        if self.state == "open":
            # Already open; additional failures don't change state.
            return CircuitDecision(False, "open", self.opened_reason)
        # closed
        self.failures += 1
        self.consecutive_failures += 1
        if self.failures >= self.config.max_failures:
            return self._open(f"failure budget exhausted: {reason}")
        if self.consecutive_failures >= self.config.max_consecutive_failures:
            return self._open(f"consecutive failures exhausted: {reason}")
        return CircuitDecision(True, "closed", "closed")

    def record_budget_denial(self, reason: str) -> CircuitDecision:
        if self.state in ("open", "half-open"):
            return CircuitDecision(False, self.state, self.opened_reason)
        self.budget_denials += 1
        if self.budget_denials >= self.config.max_budget_denials:
            return self._open(f"budget denial threshold reached: {reason}")
        return CircuitDecision(True, "closed", "closed")

    def _open(self, reason: str) -> CircuitDecision:
        self.state = "open"
        self.opened_reason = reason
        self._open_check_count = 0
        return CircuitDecision(False, "open", reason)

    def snapshot(self) -> dict:
        return {
            "state": self.state,
            "failures": self.failures,
            "consecutive_failures": self.consecutive_failures,
            "budget_denials": self.budget_denials,
            "opened_reason": self.opened_reason,
            "open_check_count": self._open_check_count,
            "config": {
                "max_failures": self.config.max_failures,
                "max_consecutive_failures": self.config.max_consecutive_failures,
                "max_budget_denials": self.config.max_budget_denials,
                "cooldown_checks": self.config.cooldown_checks,
            },
        }
