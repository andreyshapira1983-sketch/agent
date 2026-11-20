"""Per-order profit ledger for Agent Market: payment minus platform fee, model cost and other costs, plus manual intervention and minutes columns."""
# Payment: escrowAmount/escrowToken of the assignment; fee 5%; model cost from data/model_usage.jsonl by work time (core/usd_spend.py);
# intervention and human minutes are set by hand (mark).
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.state_integrity import backup_state_file

logger = logging.getLogger(__name__)

FEE_RATE = 0.05
FILE_NAME = "ledger.json"


@dataclass
class OrderLine:
    assignment_id: str
    job_id: str
    title: str = ""
    escrow_amount: float | None = None
    escrow_token: str = ""
    status: str = ""
    runs: int = 0
    work_seconds: float = 0.0
    model_usd: float = 0.0
    other_usd: float = 0.0
    claude_intervention: bool | None = None
    claude_minutes: float | None = None
    operator_minutes: float | None = None
    accepted_at: str | None = None
    #: Row fields this code does not know; written back unchanged.
    extra: dict[str, Any] = field(default_factory=dict)

    def net(self) -> float | None:
        """Net profit; before `accepted` there is no payment, so nothing to count."""
        if self.status != "accepted" or self.escrow_amount is None:
            return None
        return round(self.escrow_amount * (1 - FEE_RATE) - self.model_usd - self.other_usd, 4)


class LedgerUnreadable(ValueError):
    """The ledger file exists but does not read as a ledger."""


_KNOWN = frozenset(f.name for f in fields(OrderLine)) - {"extra"}


def _line_from(aid: str, row: dict[str, Any]) -> OrderLine:
    known = {k: v for k, v in row.items() if k in _KNOWN}
    extra = {k: v for k, v in row.items() if k not in _KNOWN}
    return OrderLine(**{"assignment_id": aid, "job_id": "", **known}, extra=extra)


def _row_of(line: OrderLine) -> dict[str, Any]:
    row = asdict(line)
    return {**row.pop("extra"), **row}


def _amount(raw: Any) -> float | None:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


class MarketLedger:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def lines(self) -> dict[str, OrderLine]:
        """Orders by assignment id; an unreadable file raises LedgerUnreadable, not an empty ledger."""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            raise LedgerUnreadable(f"market ledger {self.path} is unreadable: {exc}") from exc
        if not isinstance(raw, dict):
            raise LedgerUnreadable(f"market ledger {self.path} is unreadable: not a json object")
        for aid, row in raw.items():
            if not isinstance(row, dict):
                raise LedgerUnreadable(f"market ledger {self.path} is unreadable: row {aid!r} is not an object")
        return {aid: _line_from(aid, row) for aid, row in raw.items()}

    def _lines_or_empty(self) -> dict[str, OrderLine]:
        try:
            return self.lines()
        except LedgerUnreadable as exc:
            logger.warning("%s; treated as empty", exc)
            return {}

    def _save(self, lines: dict[str, OrderLine]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.lines()
        except LedgerUnreadable as exc:
            copy = backup_state_file(self.path)
            logger.warning("%s; damaged copy kept at %s before the rewrite", exc, copy)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({k: _row_of(v) for k, v in lines.items()}, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(self.path)

    def _line(self, lines: dict[str, OrderLine], row: dict[str, Any]) -> OrderLine:
        a, job = row.get("assignment") or {}, row.get("job") or {}
        aid = str(a.get("assignmentId"))
        line = lines.setdefault(aid, OrderLine(aid, str(a.get("jobId"))))
        line.title = str(job.get("title") or line.title)[:120]
        line.escrow_amount = _amount(a.get("escrowAmount")) if a.get("escrowAmount") is not None else line.escrow_amount
        line.escrow_token = str(a.get("escrowToken") or line.escrow_token)
        line.status = str(a.get("status") or line.status)
        if line.status == "accepted" and not line.accepted_at:
            line.accepted_at = str(a.get("finalizedAt") or datetime.now(timezone.utc).isoformat(timespec="seconds"))
        return line

    def add_run(self, row: dict[str, Any], seconds: float, model_usd: float | None) -> None:
        """One agent run on an order (first work, rework, reply in correspondence)."""
        lines = self._lines_or_empty()
        line = self._line(lines, row)
        line.runs += 1
        line.work_seconds = round(line.work_seconds + seconds, 1)
        line.model_usd = round(line.model_usd + (model_usd or 0.0), 4)
        self._save(lines)

    def observe(self, row: dict[str, Any]) -> None:
        """Status and payment from a poll, only for orders we worked on."""
        aid = str((row.get("assignment") or {}).get("assignmentId"))
        lines = self._lines_or_empty()
        if aid in lines:
            self._line(lines, row)
            self._save(lines)

    def mark(self, assignment_id: str, **fields: Any) -> OrderLine:
        """Manual columns: claude_intervention, claude_minutes, operator_minutes, other_usd."""
        allowed = {"claude_intervention", "claude_minutes", "operator_minutes", "other_usd"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"not a manual column: {sorted(unknown)}")
        lines = self.lines()
        if assignment_id not in lines:
            raise KeyError(f"no order {assignment_id} in the ledger")
        for key, value in fields.items():
            setattr(lines[assignment_id], key, value)
        self._save(lines)
        return lines[assignment_id]


def summary(lines: dict[str, OrderLine]) -> dict[str, Any]:
    """Figures for stage transitions: how many orders, how they ended, how many people were involved."""
    rows = list(lines.values())
    accepted = [r for r in rows if r.status == "accepted"]
    marked = [r for r in rows if r.claude_intervention is not None]
    human_min = sum((r.claude_minutes or 0) + (r.operator_minutes or 0) for r in rows)
    net = sum(r.net() or 0.0 for r in accepted)
    return {
        "orders": len(rows),
        "accepted": len(accepted),
        "reworked": sum(1 for r in rows if r.runs > 1),
        "claude_intervention_share": (round(sum(1 for r in marked if r.claude_intervention) / len(marked), 3)
                                      if marked else None),
        "unmarked": len(rows) - len(marked),
        "model_usd_mean": round(sum(r.model_usd for r in rows) / len(rows), 4) if rows else None,
        "net_usd": round(net, 4),
        "net_usd_per_human_hour": round(net / (human_min / 60), 2) if human_min else None,
    }
