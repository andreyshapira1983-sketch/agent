from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class CampaignCycleRecord:
    cycle: int
    ts: str
    goal: str
    action: str
    action_title: str
    severity: str
    priority: int
    risk: str
    idle: bool
    llm_calls_spent: int
    cost_units_spent: int
    result: str
    reason: str
    proposal: Optional[str] = None
    artifact: Optional[str] = None
    next_check_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle": self.cycle,
            "ts": self.ts,
            "goal": self.goal,
            "action": self.action,
            "action_title": self.action_title,
            "severity": self.severity,
            "priority": self.priority,
            "risk": self.risk,
            "idle": self.idle,
            "llm_calls_spent": self.llm_calls_spent,
            "cost_units_spent": self.cost_units_spent,
            "result": self.result,
            "reason": self.reason,
            "proposal": self.proposal,
            "artifact": self.artifact,
            "next_check_at": self.next_check_at,
        }

    def user_summary(self) -> str:
        if self.idle:
            return f"[cycle {self.cycle}] IDLE (no LLM) action={self.action} reason={self.reason}"
        if self.result == "repeat":
            return f"[cycle {self.cycle}] REPEAT (no LLM, skipped) action={self.action} reason={self.reason}"
        if self.result == "error":
            return f"[cycle {self.cycle}] ERROR (no LLM, recovered) reason={self.reason}"
        return (
            f"[cycle {self.cycle}] {self.result} action={self.action} llm={self.llm_calls_spent} "
            f"cost={self.cost_units_spent}"
            + (f" proposal={self.proposal}" if self.proposal else "")
            + (f" artifact={self.artifact}" if self.artifact else "")
        )


class CampaignLedger:
    def __init__(self, path: Optional[Path] = None):
        self.path = path
        self.records: list[CampaignCycleRecord] = []

    def append(self, record: CampaignCycleRecord) -> None:
        self.records.append(record)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")


def load_ledger_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _format_ledger_row(row: dict[str, Any]) -> str:
    cycle = row.get("cycle", "?")
    action = row.get("action", "?")
    reason = row.get("reason", "")
    if row.get("idle"):
        return f"[cycle {cycle}] IDLE (no LLM) action={action} reason={reason}"
    if row.get("result") == "repeat":
        return f"[cycle {cycle}] REPEAT (no LLM, skipped) action={action} reason={reason}"
    if row.get("result") == "error":
        return f"[cycle {cycle}] ERROR (no LLM, recovered) reason={reason}"
    proposal = row.get("proposal")
    artifact = row.get("artifact")
    return (
        f"[cycle {cycle}] {row.get('result', '?')} action={action} "
        f"llm={row.get('llm_calls_spent', 0)} cost={row.get('cost_units_spent', 0)}"
        + (f" proposal={proposal}" if proposal else "")
        + (f" artifact={artifact}" if artifact else "")
    )


def summarise_ledger(rows: list[dict[str, Any]], *, recent: int = 10) -> str:
    if not rows:
        return "=== campaign ledger ===\n(empty — no campaign cycles have been recorded yet)"

    def _as_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    total = len(rows)
    llm_calls = sum(_as_int(r.get("llm_calls_spent")) for r in rows)
    cost_units = sum(_as_int(r.get("cost_units_spent")) for r in rows)
    idle = sum(1 for r in rows if r.get("idle"))
    repeats = sum(1 for r in rows if r.get("result") == "repeat")
    errors = sum(1 for r in rows if r.get("result") == "error")
    artifacts = sum(1 for r in rows if r.get("artifact"))
    proposals = sum(1 for r in rows if r.get("proposal"))
    useful = total - idle - repeats - errors
    result_counts: dict[str, int] = {}
    for r in rows:
        key = "idle" if r.get("idle") else str(r.get("result", "?"))
        result_counts[key] = result_counts.get(key, 0) + 1
    by_result = ", ".join(f"{k}={v}" for k, v in sorted(result_counts.items()))
    goals: list[str] = []
    for r in rows:
        goal = str(r.get("goal", ""))
        if goal and goal not in goals:
            goals.append(goal)
    lines = [
        "=== campaign ledger ===",
        (
            f"cycles_logged={total}  useful={useful}  idle={idle}  repeats={repeats}  errors={errors}  "
            f"llm_calls={llm_calls}  cost_units={cost_units}  proposals={proposals}  artifacts={artifacts}"
        ),
        f"by_result: {by_result}",
        f"goals_seen ({len(goals)}): " + "; ".join(goals[:5]) + (" …" if len(goals) > 5 else ""),
    ]
    tail = rows[-recent:] if recent > 0 else rows
    lines.append(f"recent {len(tail)} cycle(s):")
    for r in tail:
        lines.append(f"  {_format_ledger_row(r)}")
    return "\n".join(lines)
