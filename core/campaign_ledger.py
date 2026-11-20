"""Append-only campaign ledger: one record per cycle, plus loading and summarising the rows."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
    proposal: str | None = None
    artifact: str | None = None
    next_check_at: str | None = None
    #: What grounded the decision and how it was chosen; `unrecorded` means not recorded, not "no grounds".
    grounds: str = "unrecorded"
    decided_by: str = "unrecorded"
    #: Outcome word "work was done"; read by `charter_goal._recent_goals`; `None` = old-format row.
    work_done: bool | None = None
    #: Success criterion of the cycle's goal, verbatim as the charter chose it; empty = not named.
    success_check: str = ""
    #: Dollars spent on model calls in the last hour at write time (core/usd_spend.py); None = not computed.
    usd_last_hour: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle": self.cycle,
            "ts": self.ts,
            "goal": self.goal,
            "success_check": self.success_check,
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
            "grounds": self.grounds,
            "decided_by": self.decided_by,
            "work_done": self.work_done,
            "usd_last_hour": self.usd_last_hour,
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
    def __init__(self, path: Path | None = None):
        self.path = path
        self.records: list[CampaignCycleRecord] = []

    def append(self, record: CampaignCycleRecord) -> None:
        if self.path is not None and record.usd_last_hour is None:
            from dataclasses import replace

            from core.usd_spend import usd_last_hour
            try:
                record = replace(record, usd_last_hour=usd_last_hour(self.path.parent.parent))
            except ValueError:  # spend journal unreadable: the row is still written, the dollars stay unknown
                pass
        self.records.append(record)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")


def load_ledger_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    # Bytes split on "\n" only: U+2028 in a value is no line end, a cut character costs one line.
    for raw in path.read_bytes().split(b"\n"):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw.decode("utf-8"))
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def spent_units_by_action(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Spend per action signature over ALL recorded runs; the basis of the cross-run ceiling.

    The repeat guard's memory lives one run, the ledger survives runs. Zero and broken rows do not count.
    """
    spent: dict[str, int] = {}
    for row in rows:
        action = row.get("action")
        try:
            units = int(row.get("cost_units_spent") or 0)
        except (TypeError, ValueError):
            continue
        if isinstance(action, str) and action and units > 0:
            spent[action] = spent.get(action, 0) + units
    return spent


def _row_was_useful(row: dict[str, Any]) -> bool:
    """A cycle that DID work is useful, by the same measure the campaign judges with.

    Rows older than the `work_done` field are judged by their product; their history is not rewritten.
    """
    recorded = row.get("work_done")
    if isinstance(recorded, bool):
        return recorded
    return bool(row.get("proposal") or row.get("artifact"))


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
    failed = sum(1 for r in rows if r.get("result") == "failed")
    artifacts = sum(1 for r in rows if r.get("artifact"))
    proposals = sum(1 for r in rows if r.get("proposal"))
    useful = sum(1 for r in rows if _row_was_useful(r))
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
            f"failed={failed}  llm_calls={llm_calls}  cost_units={cost_units}  proposals={proposals}  artifacts={artifacts}"
        ),
        f"by_result: {by_result}",
        f"goals_seen ({len(goals)}): " + "; ".join(goals[:5]) + (" …" if len(goals) > 5 else ""),
    ]
    tail = rows[-recent:] if recent > 0 else rows
    lines.append(f"recent {len(tail)} cycle(s):")
    for r in tail:
        lines.append(f"  {_format_ledger_row(r)}")
    return "\n".join(lines)
