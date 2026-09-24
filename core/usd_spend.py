"""Расход в долларах по журналу вызовов модели — и предел в час (план субботы, пункт з).

До 24.09 стоимость писалась только в условных единицах (`cost_units`), и
вопрос «сколько кампания тратит в час» требовал ручного пересчёта. Цены —
официальная страница DeepSeek (api-docs.deepseek.com/quick_start/pricing),
снятые 24.09.2026: доллары за 1 млн токенов в пиковые часы; вне пика — вдвое
дешевле. Пик — 01:00–04:00 и 06:00–10:00 UTC в будни.

Предел — необязательное `usd_per_hour` в `config/budget_limits.json`. Нет
предела — поведение прежнее: только запись расхода в журнал кампании.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

#: $ за 1 млн токенов В ПИК: (вход из кэша, вход без кэша, выход).
PEAK_PRICES: dict[str, tuple[float, float, float]] = {
    "deepseek-flash": (0.006, 0.30, 1.20),
    "deepseek-v4-pro": (0.044, 1.32, 3.96),
}
#: Старые имена, которые DeepSeek направляет на Flash.
ALIASES = {"deepseek-chat": "deepseek-flash", "deepseek-reasoner": "deepseek-flash",
           "deepseek-v4-flash": "deepseek-flash"}
_PEAK_HOURS = frozenset({1, 2, 3, 6, 7, 8, 9})
_TAIL_BYTES = 2_000_000


def is_peak(moment: datetime) -> bool:
    utc = moment.astimezone(timezone.utc)
    return utc.weekday() < 5 and utc.hour in _PEAK_HOURS


def row_usd(row: dict[str, Any]) -> float | None:
    """Стоимость одного вызова в долларах; None — модель без известной цены."""
    model = ALIASES.get(str(row.get("model") or ""), str(row.get("model") or ""))
    prices = PEAK_PRICES.get(model)
    if prices is None:
        return None
    hit = int(row.get("cache_hit_tokens") or 0)
    miss = int(row.get("cache_miss_tokens") or max(0, int(row.get("input_tokens") or 0) - hit))
    out = int(row.get("output_tokens") or 0)
    try:
        moment = datetime.fromisoformat(str(row.get("completed_at") or row.get("started_at")))
    except ValueError:
        moment = datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    factor = 1.0 if is_peak(moment) else 0.5
    return factor * (hit * prices[0] + miss * prices[1] + out * prices[2]) / 1_000_000


def _usage_rows(workspace: Path) -> list[dict[str, Any]]:
    path = Path(workspace) / "data" / "model_usage.jsonl"
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - _TAIL_BYTES))
            lines = fh.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            raw = json.loads(line)
        except ValueError:
            continue
        if isinstance(raw, dict):
            rows.append(raw.get("payload", raw) if "_integrity" in raw else raw)
    return rows


def usd_last_hour(workspace: Path, now: datetime | None = None) -> float:
    """Сколько долларов ушло на вызовы модели за последний час."""
    since = ((now or datetime.now(timezone.utc)) - timedelta(hours=1)).isoformat()
    total = 0.0
    for row in _usage_rows(workspace):
        if str(row.get("completed_at") or "") >= since:
            total += row_usd(row) or 0.0
    return round(total, 4)


def usd_hour_limit(workspace: Path) -> float | None:
    """Предел $ в час из config/budget_limits.json; нет или мусор — None."""
    try:
        data = json.loads((Path(workspace) / "config" / "budget_limits.json").read_text(encoding="utf-8"))
        value = float(data.get("usd_per_hour"))
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    return value if value > 0 else None
