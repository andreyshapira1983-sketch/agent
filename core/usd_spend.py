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
import logging
import math
import os
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
#: Модель без цены считается по самой дорогой известной ставке, без скидки вне
#: пика. Это оценка, не её настоящая цена: настоящая может быть выше.
FALLBACK_PRICES: tuple[float, float, float] = tuple(  # type: ignore[assignment]
    max(prices[i] for prices in PEAK_PRICES.values()) for i in range(3))
_PEAK_HOURS = frozenset({1, 2, 3, 6, 7, 8, 9})
_TAIL_BYTES = 2_000_000
_LOG = logging.getLogger(__name__)
_WARNED_UNPRICED: set[str] = set()
_WARNED_TURN_LIMIT: set[str] = set()


def is_peak(moment: datetime) -> bool:
    utc = moment.astimezone(timezone.utc)
    return utc.weekday() < 5 and utc.hour in _PEAK_HOURS


def _tokens(row: dict[str, Any]) -> tuple[int, int, int]:
    """Токены вызова: (вход из кэша, вход без кэша, выход)."""
    hit = _count(row, "cache_hit_tokens")
    miss = _count(row, "cache_miss_tokens") or max(0, _count(row, "input_tokens") - hit)
    return hit, miss, _count(row, "output_tokens")


def _count(row: dict[str, Any], name: str) -> int:
    """Счётчик токенов из строки журнала; мусор — ValueError с именем поля."""
    raw = row.get(name) or 0
    try:
        value = -1 if isinstance(raw, bool) else int(raw)
    except (TypeError, ValueError, OverflowError):
        value = -1
    if value < 0:
        raise ValueError(f"field {name!r} is not a token count: {row.get(name)!r}")
    return value


def _cost(tokens: tuple[int, int, int], prices: tuple[float, float, float]) -> float:
    return sum(count * price for count, price in zip(tokens, prices, strict=True)) / 1_000_000


def row_usd(row: dict[str, Any]) -> float | None:
    """Стоимость одного вызова в долларах; None — модель без известной цены."""
    model = ALIASES.get(str(row.get("model") or ""), str(row.get("model") or ""))
    prices = PEAK_PRICES.get(model)
    if prices is None:
        return None
    try:
        moment = datetime.fromisoformat(str(row.get("completed_at") or row.get("started_at")))
    except ValueError:
        moment = datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    factor = 1.0 if is_peak(moment) else 0.5
    return factor * _cost(_tokens(row), prices)


def _usage_path(workspace: Path) -> Path:
    return Path(workspace) / "data" / "model_usage.jsonl"


def _usage_rows(workspace: Path, mark: str) -> list[dict[str, Any]]:
    """Строки журнала с `completed_at` не раньше `mark`; нет журнала — пусто, не читается — ValueError."""
    path = _usage_path(workspace)
    span = _TAIL_BYTES
    while True:
        try:
            with path.open("rb") as fh:
                size = fh.seek(0, 2)
                fh.seek(max(0, size - span))
                lines = fh.read().decode("utf-8", errors="replace").splitlines()
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise ValueError(
                f"model usage journal is not readable, spend is unknown: {path}") from exc
        rows = []
        for line in lines[1:] if span < size else lines:  # первая строка хвоста может быть обрезана
            try:
                raw = json.loads(line)
            except ValueError:
                continue
            if isinstance(raw, dict):
                rows.append(raw.get("payload", raw) if "_integrity" in raw else raw)
        stamps = [str(row.get("completed_at") or "") for row in rows]
        # Хвост обязан дойти до строки старше `mark`, иначе бурный час недосчитан.
        if span >= size or any(stamp and stamp < mark for stamp in stamps):
            return [row for row, stamp in zip(rows, stamps, strict=True) if stamp >= mark]
        span *= 2


def _counted_rows(
        workspace: Path, since: datetime) -> list[tuple[dict[str, Any], tuple[int, int, int]]]:
    """Строки с момента `since` и их токены; мусор в счётчике — ValueError с файлом и полем."""
    counted = []
    for row in _usage_rows(workspace, since.astimezone(timezone.utc).isoformat()):
        try:
            counted.append((row, _tokens(row)))
        except ValueError as exc:
            raise ValueError(f"model usage journal has a bad row, spend is unknown: "
                             f"{_usage_path(workspace)}: {exc}") from exc
    return counted


def usd_since(workspace: Path, since: datetime) -> float:
    """Сколько долларов ушло на вызовы модели с момента `since` (UTC)."""
    total = 0.0
    for row, tokens in _counted_rows(workspace, since):
        usd = row_usd(row)
        if usd is None:
            # Модель без цены не бесплатна: оценка сверху, иначе предел её не видит.
            usd = _cost(tokens, FALLBACK_PRICES)
            if usd:
                _warn_unpriced(_model_label(row))
        total += usd
    return round(total, 4)


def unpriced_tokens_since(workspace: Path, since: datetime) -> dict[str, int]:
    """Токены моделей без известной цены с момента `since`: модель -> токены."""
    found: dict[str, int] = {}
    for row, tokens in _counted_rows(workspace, since):
        if row_usd(row) is None and sum(tokens):
            found[_model_label(row)] = found.get(_model_label(row), 0) + sum(tokens)
    return found


def unpriced_note(workspace: Path, since: datetime) -> str:
    """Приписка к сообщению о расходе: токены, посчитанные оценкой, а не ценой; нет таких — пусто."""
    found = unpriced_tokens_since(workspace, since)
    if not found:
        return ""
    return ("; includes an upper estimate for models without a known price: "
            + ", ".join(f"{model} {tokens} tokens" for model, tokens in sorted(found.items())))


def _model_label(row: dict[str, Any]) -> str:
    return str(row.get("model") or "") or "<no model>"


def _warn_unpriced(model: str) -> None:
    """Один раз за процесс на модель: её расход — оценка, не цена."""
    if model in _WARNED_UNPRICED:
        return
    _WARNED_UNPRICED.add(model)
    _LOG.warning("usd_spend: no price for model %r; counted at the dearest known rate "
                 "(an estimate, not its real price)", model)


def usd_last_hour(workspace: Path, now: datetime | None = None) -> float:
    """Сколько долларов ушло на вызовы модели за последний час."""
    return usd_since(workspace, (now or datetime.now(timezone.utc)) - timedelta(hours=1))


#: Предел одного хода, $: у предела ход не падает, а сдаёт сделанное. Замер 24.09
#: по 105 ходам: медиана $0.04, 95-й процентиль $0.41, самый дорогой $1.08 — предел
#: ловит только разгон. AGENT_TURN_MAX_USD; 0 — без предела.
TURN_MAX_USD = 1.0


def turn_usd_limit() -> float | None:
    """Предел $ на ход; None — без предела (0). Отрицательное — ValueError, мусор — умолчание."""
    raw = (os.environ.get("AGENT_TURN_MAX_USD") or "").strip()
    if not raw:
        return TURN_MAX_USD
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value):
        if raw not in _WARNED_TURN_LIMIT:
            _WARNED_TURN_LIMIT.add(raw)
            _LOG.warning("usd_spend: AGENT_TURN_MAX_USD=%r is not a number; "
                         "the default turn limit $%s applies", raw, TURN_MAX_USD)
        return TURN_MAX_USD
    if value < 0:
        raise ValueError("AGENT_TURN_MAX_USD must be a non-negative number of dollars")
    return value if value > 0 else None


def _money(value: float) -> str:
    return "$" + (f"{value:.4f}".rstrip("0").rstrip(".") or "0")


def usd_hour_refusal(workspace: Path, now: datetime | None = None) -> str | None:
    """Отказ вызову модели: расход за час достиг `usd_per_hour`; None — можно, не читается — ValueError."""
    limit = usd_hour_limit(workspace)
    if limit is None:
        return None
    since = (now or datetime.now(timezone.utc)) - timedelta(hours=1)
    spent = usd_since(workspace, since)
    if spent < limit:
        return None
    return (f"hourly dollar limit reached: spent {_money(spent)} of {_money(limit)} "
            f"in the last hour{unpriced_note(workspace, since)}")


def usd_hour_limit(workspace: Path) -> float | None:
    """Предел $ в час из config/budget_limits.json; нет файла, ключа или 0 — None, мусор — ValueError."""
    path = Path(workspace) / "config" / "budget_limits.json"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ValueError(f"budget config is not readable: {path}") from exc
    # Испорченный файл — не «без предела»: так же отказывает core/budget_ledger.
    try:
        raw = json.loads(text).get("usd_per_hour")
    except ValueError as exc:
        raise ValueError(f"budget config is not valid JSON: {path}") from exc
    except AttributeError as exc:  # valid JSON, but a list or a scalar
        raise ValueError(f"budget config must be a JSON object: {path}") from exc
    if raw is None:
        return None
    message = f"budget config usd_per_hour must be a non-negative number: {path}"
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(message) from exc
    if raw is True or raw is False or math.isnan(value) or value < 0:
        raise ValueError(message)
    return value if value > 0 else None
