"""Какая модель на какой роли реально даёт подтверждённые ответы."""
from __future__ import annotations

import json
from collections.abc import Container, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from core.model_catalog import classify_model, offered_models, peer_model_at_same_tier

#: Ниже этого числа прогонов доля — шум, а не свидетельство. Восемь потому, что
#: на живых данных 2026-08-15 столько набирали лишь модели, работавшие не один
#: день; всё, что реже, — это несколько случайных вопросов.
MIN_RUNS = 8


@dataclass(frozen=True)
class ModelOutcome:
    """Итог одной модели на одной роли."""

    role: str
    provider: str
    model: str
    runs: int
    fully_verified: int
    with_defect: int

    @property
    def verified_share(self) -> float:
        return self.fully_verified / self.runs if self.runs else 0.0

    @property
    def defect_share(self) -> float:
        return self.with_defect / self.runs if self.runs else 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "role": self.role,
            "provider": self.provider,
            "model": self.model,
            "runs": self.runs,
            "verified_share": round(self.verified_share, 3),
            "defect_share": round(self.defect_share, 3),
        }


def _episode_verdict(episode: Mapping[str, object]) -> tuple[bool, bool]:
    """(подтверждён целиком, были детекторы) — по одному эпизоду."""
    verified = int(episode.get("verified_chunks") or 0)
    unverified = int(episode.get("unverified_chunks") or 0)
    return (verified > 0 and unverified == 0), bool(episode.get("defect_signals"))


def measure_model_outcomes(
    usage: Iterable[Mapping[str, object]],
    episodes: Iterable[Mapping[str, object]],
) -> tuple[ModelOutcome, ...]:
    """Соединить вызовы моделей с исходами их прогонов. Чистая функция."""
    by_run: dict[str, Mapping[str, object]] = {}
    for episode in episodes:
        run_id = str(episode.get("run_id") or "")
        if run_id:
            by_run[run_id] = episode

    seen: set[tuple[str, str, str, str]] = set()
    tally: dict[tuple[str, str, str], list[int]] = {}
    for call in usage:
        if str(call.get("status") or "") != "success":
            continue
        episode = by_run.get(str(call.get("run_id") or ""))
        if episode is None:
            continue
        key = (
            str(call.get("role") or ""),
            str(call.get("provider") or ""),
            str(call.get("model") or ""),
        )
        run_key = (*key, str(call.get("run_id") or ""))
        if run_key in seen:
            continue
        seen.add(run_key)
        verified, defect = _episode_verdict(episode)
        row = tally.setdefault(key, [0, 0, 0])
        row[0] += 1
        row[1] += int(verified)
        row[2] += int(defect)

    return tuple(
        ModelOutcome(role, provider, model, runs, ver, defect)
        for (role, provider, model), (runs, ver, defect) in sorted(tally.items())
    )


#: Насколько доли подтверждённых считаются «неотличимыми». Меньше шага одной
#: ошибки при MIN_RUNS: на минимуме прогонов равенство означает точное
#: равенство, и вниз по качеству за дешевизну не меняют.
_COST_TOLERANCE = 0.05


#: Мост словарей: классификатор говорит light/standard/deep, таблица цен —
#: low/medium/high. Первая версия оси не переводила, и ОБЕ модели молча падали
#: в «unknown» — те же два словаря одного понятия, что в MIR-174. Полноту моста
#: держит тест: новый уровень не провалится в «unknown» незамеченным.
_TIER_TO_COST: dict[str, str] = {
    "light": "low",
    "standard": "medium",
    "deep": "high",
}


def _cost_units(model: str) -> int:
    """Тариф уровня модели — из ЕДИНОЙ таблицы, не из копии."""
    from core.model_usage import cost_units_per_1k

    return cost_units_per_1k(_TIER_TO_COST.get(classify_model(model).value, "unknown"))


def preferred_model(
    outcomes: Iterable[ModelOutcome],
    *,
    role: str,
    provider: str,
    min_runs: int = MIN_RUNS,
    offered: Container[str] | None = None,
) -> str | None:
    """Лучшая ИЗМЕРЕННАЯ модель этой роли у этого провайдера, или None."""
    ranked = [
        outcome for outcome in outcomes
        if outcome.role == role and outcome.provider == provider
        and outcome.runs >= min_runs
        and (offered is None or outcome.model in offered)
    ]
    if not ranked:
        return None
    # Ось стоимости (MIR-176; дайджест, записи 6–7). Среди моделей, чьё
    # качество НЕОТЛИЧИМО от лучшего, побеждает дешёвый тариф. Допуск меньше
    # шага одной ошибки при MIN_RUNS=8 (1/8 = 0.125), поэтому измеримо худшее
    # дешевизной не покупается никогда — болезнь спуска, уже чиненная у
    # failover (73 спуска за сутки, 2026-08-15), сюда не возвращается.
    best_share = max(o.verified_share for o in ranked)
    bar = [o for o in ranked if best_share - o.verified_share <= _COST_TOLERANCE]
    bar.sort(key=lambda o: (
        _cost_units(o.model), -o.verified_share, o.defect_share, -o.runs,
    ))
    return bar[0].model


#: Каждый четвёртый прогон таблицы отдаёт следующее решение разведчику: хватает,
#: чтобы незамеренный ровесник набирал MIN_RUNS за дни, а не никогда; мало
#: настолько, чтобы измеренный победитель оставался рабочей лошадью.
SCOUT_PERIOD = 4


def scout_turn(decisions: int, *, period: int = SCOUT_PERIOD) -> bool:
    """Чей ход — разведчика или победителя. По МОНОТОННОМУ счёту решений."""
    return decisions % period == 0


def failover_decisions(
    workspace: Path | None = None, *, role: str, provider: str,
) -> int:
    """Сколько failover-решений уже было у пары роль+провайдер. Монотонно.

    Один прогон — одно решение, сколько бы вызовов подменённая модель в нём
    ни сделала; строки без run_id считаются поштучно.
    """
    root = Path(workspace or ".")
    runs: set[str] = set()
    loose = 0
    for row in _load_rows(root / "data" / "model_usage.jsonl"):
        if (str(row.get("role") or "") != role
                or str(row.get("provider") or "") != provider):
            continue
        if not str(row.get("route_reason") or "").startswith("provider_failover"):
            continue
        rid = str(row.get("run_id") or "")
        if rid:
            runs.add(rid)
        else:
            loose += 1
    return len(runs) + loose


def scout_model(
    outcomes: Iterable[ModelOutcome],
    *,
    role: str,
    provider: str,
    current_model: str | None,
    min_runs: int = MIN_RUNS,
) -> str | None:
    """Незамеренный ровесник по уровню — кандидат на разведку, или None."""
    peer = peer_model_at_same_tier(current_model, provider)
    if not peer:
        return None
    for outcome in outcomes:
        if (outcome.role == role and outcome.provider == provider
                and outcome.model == peer and outcome.runs >= min_runs):
            return None
    return peer


# ── Подключение к выбору замены ──────────────────────────────────────────────
# Ниже — единственное место, где замер встречается с картой уровней. Порядок
# важен и он весь смысл: измеренное предпочтение ПЕРВЫМ, карта имён — полом на
# случай, когда мерить ещё нечего.

#: Считанные исходы на процесс. Отказ провайдера — событие редкое, а файлы
#: растут; читать их на каждый вызов незачем, а держать вечно нельзя — поэтому
#: сбрасывается по времени изменения источников.
_CACHE: dict[str, object] = {}


def _load_rows(path: Path) -> list[dict]:
    out: list[dict] = []
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        out.append(row.get("payload") or row)
    return out


def measured_outcomes(workspace: Path | None = None) -> tuple[ModelOutcome, ...]:
    """Исходы по данным рабочей папки, с кэшем по времени изменения файлов."""
    root = Path(workspace or ".")
    usage_path = root / "data" / "model_usage.jsonl"
    episodes_path = root / "data" / "episodic_memory.jsonl"
    stamp = tuple(
        p.stat().st_mtime_ns if p.is_file() else 0
        for p in (usage_path, episodes_path)
    )
    if _CACHE.get("stamp") == stamp:
        return _CACHE["outcomes"]  # type: ignore[return-value]
    outcomes = measure_model_outcomes(
        _load_rows(usage_path), _load_rows(episodes_path)
    )
    _CACHE["stamp"] = stamp
    _CACHE["outcomes"] = outcomes
    return outcomes


def substitute_model(
    *, role: str, provider: str, current_model: str | None,
    workspace: Path | None = None,
) -> str | None:
    """Кем заменить `current_model` у нового провайдера."""
    return substitute_model_with_reason(
        role=role, provider=provider, current_model=current_model,
        workspace=workspace,
    )[0]


def substitute_model_with_reason(
    *, role: str, provider: str, current_model: str | None,
    workspace: Path | None = None,
) -> tuple[str | None, str]:
    """(модель, причина) — решение обязано рассказывать себя."""
    try:
        outcomes = measured_outcomes(workspace)
        measured = preferred_model(
            outcomes, role=role, provider=provider,
            offered=offered_models(provider) or None,
        )
    except OSError:
        outcomes, measured = (), None
    material = sum(
        o.runs for o in outcomes
        if o.role == role and o.provider == provider
    )
    if measured:
        scout = scout_model(
            outcomes, role=role, provider=provider, current_model=current_model,
        )
        decisions = failover_decisions(workspace, role=role, provider=provider)
        if scout and scout != measured and scout_turn(decisions):
            return scout, f"scout:{scout}(mat={material},dec={decisions})"
        why = (
            "off-turn" if scout and scout != measured
            else ("no-scout-candidate" if not scout else "scout==measured")
        )
        return measured, f"measured:{measured}(mat={material},dec={decisions},{why})"
    peer = peer_model_at_same_tier(current_model, provider)
    return peer, f"floor:{peer}(mat={material})"
