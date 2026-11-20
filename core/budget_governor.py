"""Outer runtime budget for autonomous loops: cycles, test runs, learning passes and approval requests.

    Limit semantics: limit > 0 is a hard cap; limit == 0 is unlimited (usage is
    tracked but never denied)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

BudgetCounter = Literal[
    "cycles",
    "agent_runs",
    "learning_runs",
    "test_runs",
    "approval_requests",
    "llm_calls",
    "web_fetches",
    "proposals_per_run",
]


def budget_limit_label(limit: int) -> str:
    return str(limit) if int(limit) > 0 else "unlimited"


@dataclass(frozen=True)
class BudgetLimits:
    max_cycles: int = 5
    max_agent_runs: int = 0
    max_learning_runs: int = 1
    max_test_runs: int = 1
    max_approval_requests: int = 20
    max_llm_calls: int = 0
    max_web_fetches: int = 0
    max_proposals_per_run: int = 3

    def limit_for(self, counter: BudgetCounter) -> int:
        return {
            "cycles": self.max_cycles,
            "agent_runs": self.max_agent_runs,
            "learning_runs": self.max_learning_runs,
            "test_runs": self.max_test_runs,
            "approval_requests": self.max_approval_requests,
            "llm_calls": self.max_llm_calls,
            "web_fetches": self.max_web_fetches,
            "proposals_per_run": self.max_proposals_per_run,
        }[counter]


@dataclass(frozen=True)
class BudgetDecision:
    counter: BudgetCounter
    allowed: bool
    used: int
    limit: int
    amount: int = 1
    reason: str = ""

    def to_dict(self) -> dict:
        limit_label = budget_limit_label(self.limit)
        return {
            "counter": self.counter,
            "allowed": self.allowed,
            "used": self.used,
            "limit": self.limit,
            "limit_label": limit_label,
            "limit_enforced": self.limit > 0,
            "amount": self.amount,
            "reason": self.reason,
        }


@dataclass
class BudgetGovernor:
    limits: BudgetLimits = field(default_factory=BudgetLimits)
    used: dict[BudgetCounter, int] = field(default_factory=dict)
    denials: list[BudgetDecision] = field(default_factory=list)

    def reserve(
        self,
        counter: BudgetCounter,
        *,
        amount: int = 1,
        reason: str = "",
    ) -> BudgetDecision:
        if amount < 1:
            raise ValueError("amount must be >= 1")
        limit = self.limits.limit_for(counter)
        current = self.used.get(counter, 0)
        # limit == 0 means unlimited — track usage but never deny.
        if limit > 0 and current + amount > limit:
            decision = BudgetDecision(
                counter=counter,
                allowed=False,
                used=current,
                limit=limit,
                amount=amount,
                reason=reason or f"{counter} budget exhausted",
            )
            self.denials.append(decision)
            return decision
        self.used[counter] = current + amount
        return BudgetDecision(
            counter=counter,
            allowed=True,
            used=self.used[counter],
            limit=limit,
            amount=amount,
            reason=reason,
        )

    def snapshot(self) -> dict:
        counters = (
            "cycles",
            "agent_runs",
            "learning_runs",
            "test_runs",
            "approval_requests",
            "llm_calls",
            "web_fetches",
            "proposals_per_run",
        )
        return {
            "limits": {counter: self.limits.limit_for(counter) for counter in counters},
            "used": {counter: self.used.get(counter, 0) for counter in counters},
            "denials": [d.to_dict() for d in self.denials],
        }
