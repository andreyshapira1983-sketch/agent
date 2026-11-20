"""Campaign value types: configuration, per-action outcome, and the result of a finished campaign."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


#: `kw_only`: field order of a growing config is not a contract, so a positional call must fail loudly.
@dataclass(frozen=True, kw_only=True)
class CampaignConfig:
    goal: str = "project health"
    #: Success criterion of the goal, verbatim as chosen by the charter; empty means 'no criterion named'.
    success_check: str = ""
    max_cycles: int = 24
    max_llm_calls: int = 100
    max_cost_units: int = 0
    max_idle_streak: int = 3
    #: Silences the HANDS, not the instruments: only effect producers consult it (core/campaign_io.py); the run journals
    #: (data/campaign_ledger.jsonl, data/campaign_verdicts.jsonl) are ALWAYS written.
    dry_run: bool = True
    report_every: int = 1
    idle_recheck_seconds: int = 600
    max_wall_clock_seconds: int = 0
    cycle_pause_seconds: int = 0
    max_consecutive_errors: int = 3
    max_unproductive_streak: int = 0
    #: Max goal switches per process; 0 means no ceiling. Default 12 (core/campaign.py).
    max_goal_switches: int = 12
    #: When the action menu offers the goal nothing allowed, run the goal itself (one agent pass) instead of idling. Off by default.
    pursue_goal_when_idle: bool = False
    #: The goal is a task, not a menu filter: each cycle with a goal runs the goal itself (one pass); the menu is asked only
    #: about a real breakage (critical/high). After the pass the goal is done and the next cycle switches it.
    goal_first: bool = False
    #: The agent chose the goal itself (drives, charter), not a human; set where the goal is obtained because only there is it known.
    goal_is_self: bool = False
    #: Action name that carries THIS goal (goal_first mode); empty means an agent pass on the goal (pursue_goal).
    goal_action: str = ""
    #: Cross-run spend ceiling per action signature; 0 = off. Exceeding it is a question to the operator (result="cost_cap").
    #: The counter has no window; the daily and per-run budgets (max_llm_calls, max_cost_units) stay in force.
    max_cost_units_per_signature: int = 0

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
        if self.max_cost_units_per_signature < 0:
            raise ValueError("max_cost_units_per_signature must be >= 0 (0 = off)")


@dataclass(frozen=True)
class CampaignActionOutcome:
    result: str
    llm_calls_spent: int = 0
    cost_units_spent: int = 0
    proposal: str | None = None
    artifact: str | None = None
    #: The producer's word 'work done' for outcomes without a product; a product speaks for itself via did_work.
    work_done: bool = False
    # Step subject: observation fingerprint for the explainer, claim key for the court; empty for subject-less actions.
    subject: str = ""

    @property
    def did_work(self) -> bool:
        """Work is done: a product exists or the producer said so."""
        return self.work_done or self.proposal is not None or self.artifact is not None

    @property
    def ran(self) -> bool:
        """An attempt was made: something was spent or done; a refusal before start is NOT an attempt."""
        return self.did_work or self.llm_calls_spent > 0 or self.cost_units_spent > 0


@dataclass
class CampaignResult:
    status: str
    goal: str
    stop_reason: str
    cycles_run: int
    records: list[Any] = field(default_factory=list)
    totals: dict[str, int] = field(default_factory=dict)
    clarification: dict[str, Any] | None = None
    #: Verdict on the goal's OWN criterion (core/campaign_verdict.py); `None` = not judged.
    success_verdict: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "goal": self.goal,
            "stop_reason": self.stop_reason,
            "cycles_run": self.cycles_run,
            "totals": self.totals,
            "records": [r.to_dict() for r in self.records],
            "clarification": self.clarification,
            "success_verdict": self.success_verdict,
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
                f"artifacts={self.totals.get('artifacts', 0)}  "
                f"goal_drove={self.totals.get('goal_drove_cycles', 0)}  "
                # Cost of a useful cycle is always a visible mirror of spend; '-' at zero useful cycles.
                f"units_per_useful="
                f"{round(self.totals.get('cost_units', 0) / u, 1) if (u := self.totals.get('useful_cycles', 0)) else '-'}"
            ),
        ]
        # The goal verdict ranks ABOVE cycles: `status=completed` for a run that missed its goal would read as success.
        if self.success_verdict:
            from core.campaign_verdict import verdict_summary_line
            lines.append(verdict_summary_line(self.success_verdict))
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
