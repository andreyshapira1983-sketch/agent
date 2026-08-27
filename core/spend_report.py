"""Зеркало трат: что агент потратил и что за это получил.

Орган НЕ говорит «экономь» — он кладёт перед читателем (оператором и самим
агентом) пары «потратил → получил» из его же журналов. Вывод должен родиться у
читателя; записанный нами вывод был бы нашим, а не его.

Дисциплина из тридцатилетнего дайджеста (Snell 2024, запись 7): каждая денежная
строка несёт знаменатель — «на 100 единиц», «единиц на завершение», — потому
что отчёт голых сумм учит «тратить больше = становиться лучше».

Замер и границы: MIR-177.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelSpend:
    """Одна модель на одной роли: сколько стоила и что вернула."""

    role: str
    provider: str
    model: str
    calls: int
    ok_calls: int
    cost_units: int

    @property
    def ok_per_100_units(self) -> float:
        """Успешные вызовы на сотню единиц. 0 при нулевом расходе: бесплатное
        не награждается бесконечностью — его не с чем сравнивать."""
        if self.cost_units <= 0:
            return 0.0
        return round(100.0 * self.ok_calls / self.cost_units, 1)


@dataclass(frozen=True)
class ActionSpend:
    """Одно действие кампании: цена его завершений."""

    action: str
    cycles: int
    completed: int
    cost_units: int

    @property
    def units_per_completed(self) -> float | None:
        """Единиц на одно завершение; None, когда завершений не было —
        неопределённость не выдаётся ни за ноль, ни за бесконечность."""
        if self.completed <= 0:
            return None
        return round(self.cost_units / self.completed, 1)


def model_spend(rows: Iterable[Mapping[str, Any]]) -> list[ModelSpend]:
    """Свод по (роль, провайдер, модель) из строк журнала использования."""
    acc: dict[tuple[str, str, str], list[int]] = {}
    for row in rows:
        key = (
            str(row.get("role") or "?"),
            str(row.get("provider") or "?"),
            str(row.get("model") or "?"),
        )
        calls, ok, units = acc.setdefault(key, [0, 0, 0])
        acc[key][0] = calls + 1
        # Словарь ПРОИЗВОДИТЕЛЯ: журнал пишет success/error (замерено на 1392
        # живых строках: 997/395). Первая версия считала выдуманное «ok» и
        # показала 0 успехов у всех моделей — зеркало врало с первого вдоха.
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
    """Свод по действию из строк ленты кампаний."""
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
    """Строки зеркала для человека и для подсказок. Пусто — значит пусто."""
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
