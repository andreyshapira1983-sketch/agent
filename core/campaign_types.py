from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class CampaignConfig:
    goal: str = "project health"
    max_cycles: int = 24
    max_llm_calls: int = 100
    max_cost_units: int = 0
    max_idle_streak: int = 3
    dry_run: bool = True
    report_every: int = 1
    idle_recheck_seconds: int = 600
    max_wall_clock_seconds: int = 0
    cycle_pause_seconds: int = 0
    max_consecutive_errors: int = 3
    max_unproductive_streak: int = 0

    def __post_init__(self) -> None:
        if self.max_cycles < 1:
            raise ValueError("max_cycles must be >= 1")
        if self.max_idle_streak < 1:
            raise ValueError("max_idle_streak must be >= 1")
        if self.max_llm_calls < 0:
            raise ValueError("max_llm_calls must be >= 0 (0 = unlimited)")
        if self.max_cost_units < 0:
            raise ValueError("max_cost_units must be >= 0 (0 = unlimited)")
        if self.report_every < 1:
            raise ValueError("report_every must be >= 1")
        if self.idle_recheck_seconds < 0:
            raise ValueError("idle_recheck_seconds must be >= 0")
        if self.max_wall_clock_seconds < 0:
            raise ValueError("max_wall_clock_seconds must be >= 0 (0 = unlimited)")
        if self.cycle_pause_seconds < 0:
            raise ValueError("cycle_pause_seconds must be >= 0 (0 = no pause)")
        if self.max_consecutive_errors < 1:
            raise ValueError("max_consecutive_errors must be >= 1")
        if self.max_unproductive_streak < 0:
            raise ValueError("max_unproductive_streak must be >= 0 (0 = off)")


@dataclass(frozen=True)
class CampaignActionOutcome:
    result: str
    llm_calls_spent: int = 0
    cost_units_spent: int = 0
    proposal: Optional[str] = None
    artifact: Optional[str] = None


@dataclass
class CampaignResult:
    status: str
    goal: str
    stop_reason: str
    cycles_run: int
    records: list[Any] = field(default_factory=list)
    totals: dict[str, int] = field(default_factory=dict)
    clarification: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "goal": self.goal,
            "stop_reason": self.stop_reason,
            "cycles_run": self.cycles_run,
            "totals": self.totals,
            "records": [r.to_dict() for r in self.records],
            "clarification": self.clarification,
        }

    def user_summary(self) -> str:
        lines = [
            "=== autonomous campaign ===",
            f"status={self.status}  goal={self.goal!r}  stop_reason={self.stop_reason or '-'}",
            (
                f"cycles={self.cycles_run}  "
                f"useful={self.totals.get('useful_cycles', 0)}  "
                f"idle={self.totals.get('idle_cycles', 0)}  "
                f"repeats={self.totals.get('repeat_cycles', 0)}  "
                f"errors={self.totals.get('error_cycles', 0)}  "
                f"llm_calls={self.totals.get('llm_calls', 0)}  "
                f"cost_units={self.totals.get('cost_units', 0)}  "
                f"proposals={self.totals.get('proposals', 0)}  "
                f"artifacts={self.totals.get('artifacts', 0)}"
            ),
        ]
        for record in self.records:
            lines.append(f"  {record.user_summary()}")
        if self.clarification and self.clarification.get("questions"):
            lines.append("--- нужно уточнение (режим вопроса) ---")
            lines.append("Я не могу безопасно продолжить. Уточни:")
            for question in self.clarification["questions"]:
                lines.append(f"  - {question}")
            forbidden = self.clarification.get("forbidden_actions") or []
            if forbidden:
                lines.append(f"  (пока запрещено: {', '.join(forbidden)})")
        return "\n".join(lines)
