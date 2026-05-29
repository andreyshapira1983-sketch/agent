"""Circuit breaker for bounded autonomous runtime runs."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


CircuitState = Literal["closed", "open"]


@dataclass(frozen=True)
class CircuitBreakerConfig:
    max_failures: int = 2
    max_consecutive_failures: int = 2
    max_budget_denials: int = 1


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

    def check(self) -> CircuitDecision:
        if self.state == "open":
            return CircuitDecision(False, self.state, self.opened_reason)
        return CircuitDecision(True, self.state, "closed")

    def record_success(self) -> CircuitDecision:
        if self.state == "open":
            return self.check()
        self.consecutive_failures = 0
        return self.check()

    def record_failure(self, reason: str) -> CircuitDecision:
        if self.state == "open":
            return self.check()
        self.failures += 1
        self.consecutive_failures += 1
        if self.failures >= self.config.max_failures:
            return self._open(f"failure budget exhausted: {reason}")
        if self.consecutive_failures >= self.config.max_consecutive_failures:
            return self._open(f"consecutive failures exhausted: {reason}")
        return self.check()

    def record_budget_denial(self, reason: str) -> CircuitDecision:
        if self.state == "open":
            return self.check()
        self.budget_denials += 1
        if self.budget_denials >= self.config.max_budget_denials:
            return self._open(f"budget denial threshold reached: {reason}")
        return self.check()

    def _open(self, reason: str) -> CircuitDecision:
        self.state = "open"
        self.opened_reason = reason
        return CircuitDecision(False, self.state, reason)

    def snapshot(self) -> dict:
        return {
            "state": self.state,
            "failures": self.failures,
            "consecutive_failures": self.consecutive_failures,
            "budget_denials": self.budget_denials,
            "opened_reason": self.opened_reason,
            "config": {
                "max_failures": self.config.max_failures,
                "max_consecutive_failures": self.config.max_consecutive_failures,
                "max_budget_denials": self.config.max_budget_denials,
            },
        }
