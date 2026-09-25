"""Учёт прибыли по каждому заказу Agent Market (решение оператора 2026-09-25).

Прибыль = оплата − комиссия площадки − стоимость модели − прочие расходы.
Отдельными колонками — вмешался ли Claude и сколько минут ушло у Claude и у
оператора: работа за $1, которую полчаса исправляли люди, ещё не масштабируемая
экономика. Переход к следующему этапу автономии — по этим цифрам, а не по
ощущению (память оператора market-earning-pipeline).

Откуда числа:
- оплата — `escrowAmount`/`escrowToken` назначения (openapi.json, схема
  JobAssignment); деньги наши после статуса `accepted` (reference/agent-api.md,
  «Statuses»), выплата — владельцу, ключ агента кошелька не видит;
- комиссия — 5 %, как показано на странице агента 25.09;
- модель — журнал вызовов `data/model_usage.jsonl` по времени работы
  (core/usd_spend.py);
- вмешательство и минуты людей — отмечаются руками (`mark`), машина их не знает.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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

    def net(self) -> float | None:
        """Чистая прибыль; до `accepted` оплаты нет — считать нечего."""
        if self.status != "accepted" or self.escrow_amount is None:
            return None
        return round(self.escrow_amount * (1 - FEE_RATE) - self.model_usd - self.other_usd, 4)


def _amount(raw: Any) -> float | None:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


class MarketLedger:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def lines(self) -> dict[str, OrderLine]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {aid: OrderLine(**row) for aid, row in raw.items()}

    def _save(self, lines: dict[str, OrderLine]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({k: asdict(v) for k, v in lines.items()}, ensure_ascii=False, indent=1),
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
        """Один запуск агента на заказ (первая работа, доработка, ответ в переписке)."""
        lines = self.lines()
        line = self._line(lines, row)
        line.runs += 1
        line.work_seconds = round(line.work_seconds + seconds, 1)
        line.model_usd = round(line.model_usd + (model_usd or 0.0), 4)
        self._save(lines)

    def observe(self, row: dict[str, Any]) -> None:
        """Статус и оплата из опроса — только для заказов, которые мы работали."""
        aid = str((row.get("assignment") or {}).get("assignmentId"))
        lines = self.lines()
        if aid in lines:
            self._line(lines, row)
            self._save(lines)

    def mark(self, assignment_id: str, **fields: Any) -> OrderLine:
        """Ручные колонки: claude_intervention, claude_minutes, operator_minutes, other_usd."""
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
    """Цифры для перехода между этапами: сколько, чем кончилось, сколько людей."""
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
