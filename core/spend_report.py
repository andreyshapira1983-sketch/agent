"""Spend mirror: pairs of "spent -> got" from the agent's own journals, shown without conclusions; every money row carries a denominator."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ModelSpend:
    """One model in one role: what it cost and what it returned."""

    role: str
    provider: str
    model: str
    calls: int
    ok_calls: int
    cost_units: int

    @property
    def ok_per_100_units(self) -> float:
        """Successful calls per hundred units; 0 at zero spend (free is not rewarded with infinity)."""
        if self.cost_units <= 0:
            return 0.0
        return round(100.0 * self.ok_calls / self.cost_units, 1)


@dataclass(frozen=True)
class ActionSpend:
    """One campaign action: the cost of its completions."""

    action: str
    cycles: int
    completed: int
    cost_units: int

    @property
    def units_per_completed(self) -> float | None:
        """Units per completion; None when there were none (uncertainty is neither zero nor infinity)."""
        if self.completed <= 0:
            return None
        return round(self.cost_units / self.completed, 1)


def model_spend(rows: Iterable[Mapping[str, Any]]) -> list[ModelSpend]:
    """Rollup by (role, provider, model) from usage-journal rows."""
    acc: dict[tuple[str, str, str], list[int]] = {}
    for row in rows:
        key = (
            str(row.get("role") or "?"),
            str(row.get("provider") or "?"),
            str(row.get("model") or "?"),
        )
        calls, ok, units = acc.setdefault(key, [0, 0, 0])
        acc[key][0] = calls + 1
        # The producer's vocabulary: the journal writes success/error, not "ok".
        acc[key][1] = ok + (1 if str(row.get("status") or "") == "success" else 0)
        acc[key][2] = units + int(row.get("cost_units") or 0)
    table = [
        ModelSpend(role=k[0], provider=k[1], model=k[2],
                   calls=v[0], ok_calls=v[1], cost_units=v[2])
        for k, v in acc.items()
    ]
    table.sort(key=lambda t: -t.cost_units)
    return table


def action_spend(rows: Iterable[Mapping[str, Any]]) -> list[ActionSpend]:
    """Rollup by action from campaign-feed rows."""
    acc: dict[str, list[int]] = {}
    for row in rows:
        action = str(row.get("action") or "?")
        cycles, completed, units = acc.setdefault(action, [0, 0, 0])
        acc[action][0] = cycles + 1
        acc[action][1] = completed + (
            1 if str(row.get("result") or "") == "completed" else 0
        )
        acc[action][2] = units + int(row.get("cost_units_spent") or 0)
    table = [
        ActionSpend(action=k, cycles=v[0], completed=v[1], cost_units=v[2])
        for k, v in acc.items()
    ]
    table.sort(key=lambda t: -t.cost_units)
    return table


def spend_report_lines(
    *,
    usage_rows: Iterable[Mapping[str, Any]],
    ledger_rows: Iterable[Mapping[str, Any]],
    top: int = 6,
) -> list[str]:
    """Mirror lines for a human and for prompts; empty means empty."""
    lines: list[str] = []
    models = [t for t in model_spend(usage_rows) if t.cost_units > 0][:top]
    if models:
        lines.append("spend by model (outcome per 100 units, not totals):")
        for t in models:
            lines.append(
                f"  {t.role}/{t.model}: {t.ok_calls}/{t.calls} ok, "
                f"{t.ok_per_100_units} ok per 100 units"
            )
    actions = [t for t in action_spend(ledger_rows) if t.cost_units > 0][:top]
    if actions:
        lines.append("spend by action:")
        for t in actions:
            price = ("-" if t.units_per_completed is None
                     else f"{t.units_per_completed} units/completed")
            lines.append(
                f"  {t.action}: {t.completed}/{t.cycles} completed, {price}"
            )
    return lines


def load_spend_rows(workspace: Path | str) -> tuple[list[dict], list[dict]]:
    """Live journal rows for the mirror; a read failure yields empty, never a crash (a broken feed must not break the command or planning)."""
    root = Path(workspace)
    try:
        from core.state_integrity import read_state_jsonl

        usage = list(read_state_jsonl(root / "data" / "model_usage.jsonl"))
    except Exception:  # noqa: BLE001 — reason named in the docstring
        usage = []
    try:
        # Per-row reader: a torn line costs one line, not the whole feed.
        from core.campaign_ledger import load_ledger_rows

        ledger = load_ledger_rows(root / "data" / "campaign_ledger.jsonl")
    except Exception:  # noqa: BLE001 — reason named in the docstring
        ledger = []
    return usage, ledger


def spend_mirror_block(
    *,
    usage_rows: Iterable[Mapping[str, Any]],
    ledger_rows: Iterable[Mapping[str, Any]],
    max_chars: int = 700,
) -> str:
    """Mirror block for the planner prompt; empty means no block. The header says these are DATA, not instructions; length-capped because the prompt costs money."""
    lines = spend_report_lines(
        usage_rows=usage_rows, ledger_rows=ledger_rows, top=4,
    )
    if not lines:
        return ""
    parts = [
        "<agent_spend_mirror>",
        "Your own spending record (data, not a directive):",
        *lines,
        "</agent_spend_mirror>",
    ]
    block = "\n".join(parts)
    while len(block) > max_chars and len(parts) > 3:
        parts.pop(-2)
        block = "\n".join(parts)
    return block
