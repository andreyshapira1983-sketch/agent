"""Какая модель на какой роли реально даёт подтверждённые ответы.

Выбор модели до сих пор опирался на ИМЯ: карта уровней в каталоге считает
`claude-sonnet-5` и `gpt-5.6-terra` равными, потому что оба названы «standard».
Это совпадение по семейству, а не факт о работе ЭТОГО агента.

Факт лежал рядом и не был соединён. `data/model_usage.jsonl` пишет роль,
провайдера и модель на каждый вызов, эпизод пишет исход того же прогона:
сколько утверждений подтвердилось и какие детекторы сработали. Общий ключ —
`run_id`. Замер 2026-08-15 по 150 общим прогонам:

    claude-sonnet-4-5   67% ответов подтверждены целиком
    gpt-5.4-nano        45%
    claude-sonnet-5     36%
    gpt-4o-mini         15%

Разрыв в четыре с половиной раза — и `gpt-5.4-nano` уровня «light» обходит
`claude-sonnet-5` уровня «standard». По карте имён вышло бы наоборот.

ЧЕГО ЭТОТ ЗАМЕР НЕ ЗНАЧИТ. Это наблюдение, а не опыт: модели работали в разное
время над разными вопросами, и часть разрыва принадлежит задачам, а не моделям.
Поэтому здесь считается ПРЕДПОЧТЕНИЕ, а не доказательство, и оно молчит, пока
наблюдений мало. Причинную проверку (та же задача двум моделям) умеет
`core/causal_climb.py`, и она к этому модулю не относится.

Зачем и чем мерялось: docs/CODE_NOTES.md, «Which model earns the role».
"""
from __future__ import annotations

import json
from collections.abc import Container, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from core.model_catalog import offered_models, peer_model_at_same_tier

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
    """(подтверждён целиком, были детекторы) — по одному эпизоду.

    «Подтверждён целиком» намеренно строже, чем «хоть что-то подтвердилось»:
    частично подтверждённый ответ бывает и у хорошей модели на трудном вопросе,
    а вот доля полностью сошедшихся отделяет модели друг от друга.
    """
    verified = int(episode.get("verified_chunks") or 0)
    unverified = int(episode.get("unverified_chunks") or 0)
    return (verified > 0 and unverified == 0), bool(episode.get("defect_signals"))


def measure_model_outcomes(
    usage: Iterable[Mapping[str, object]],
    episodes: Iterable[Mapping[str, object]],
) -> tuple[ModelOutcome, ...]:
    """Соединить вызовы моделей с исходами их прогонов. Чистая функция.

    Считаются только УСПЕШНЫЕ вызовы: отказ провайдера — факт о ключе, а не о
    качестве модели, и записывать его в её счёт значило бы наказывать модель за
    пустой счёт оператора.

    Один прогон считается модели один раз на роль, сколько бы вызовов она в нём
    ни сделала: иначе многословный прогон весил бы больше короткого.
    """
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


def preferred_model(
    outcomes: Iterable[ModelOutcome],
    *,
    role: str,
    provider: str,
    min_runs: int = MIN_RUNS,
    offered: Container[str] | None = None,
) -> str | None:
    """Лучшая ИЗМЕРЕННАЯ модель этой роли у этого провайдера, или None.

    None означает «замер молчит» и обязан возвращать вызывающего к прежнему
    правилу — карте уровней. Молчание здесь не отказ, а честность: до восьми
    прогонов доля не отличима от случайности.

    Сравниваются две оси в порядке важности: доля полностью подтверждённых
    ответов, затем доля прогонов с сигналами дефектов (меньше — лучше). Обе
    считаны по исходам, ни одна не выведена из имени модели.

    `offered` — что провайдер предлагает СЕЙЧАС. Опыт живёт дольше мира:
    замер 2026-08-15 назвал лучшим `claude-sonnet-4-5` (67% на 63 прогонах), а
    в текущем каталоге такого имени уже нет. Собственная история без сверки с
    миром — голосование за вчерашний день. `None` означает «мир не наблюдён» и
    сверку отключает: не знать и отрицать — разные вещи.
    """
    ranked = [
        outcome for outcome in outcomes
        if outcome.role == role and outcome.provider == provider
        and outcome.runs >= min_runs
        and (offered is None or outcome.model in offered)
    ]
    if not ranked:
        return None
    ranked.sort(key=lambda o: (o.verified_share, -o.defect_share, o.runs), reverse=True)
    return ranked[0].model


#: Каждый четвёртый прогон таблицы отдаёт следующее решение разведчику: хватает,
#: чтобы незамеренный ровесник набирал MIN_RUNS за дни, а не никогда; мало
#: настолько, чтобы измеренный победитель оставался рабочей лошадью.
SCOUT_PERIOD = 4


def scout_turn(
    outcomes: Iterable[ModelOutcome], *, role: str, provider: str,
    period: int = SCOUT_PERIOD,
) -> bool:
    """Чей ход — разведчика или победителя. По счёту прогонов, не по часам.

    Первая конструкция была окном по настенным часам (10 минут из каждых 40) и
    сломалась на первом же живом замере 2026-08-15: охота №3 уложила все 12
    failover-решений в 4 минуты внутри закрытого отрезка, и разведчик не
    получил ни одного вызова. Нагрузка этого агента живёт вспышками, а доля
    времени не равна доле решений. Счёт по прогонам вспышка проскочить не
    может: он растёт самими прогонами.
    """
    material = sum(
        o.runs for o in outcomes
        if o.role == role and o.provider == provider
    )
    return material % period == 0


def scout_model(
    outcomes: Iterable[ModelOutcome],
    *,
    role: str,
    provider: str,
    current_model: str | None,
    min_runs: int = MIN_RUNS,
) -> str | None:
    """Незамеренный ровесник по уровню — кандидат на разведку, или None.

    Замер, который всегда выигрывает, отрезает себе материал: победитель
    получает все прогоны, ровесник — ни одного, и его строка таблицы молчит
    вечно. Живой пример 2026-08-15: gpt-5.4-nano (65% на 34 прогонах) выигрывал
    каждый отказ, а gpt-5.6-terra — лучшая standard-модель того же ключа — не
    имела ни одного прогона с вердиктом и потому не могла быть предпочтена
    никогда. Эксплуатация без разведки — самозапирание.

    Разведка кончается там, где начинается замер: набравший `min_runs`
    ровесник дальше судится таблицей, а не окном.
    """
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
    """Кем заменить `current_model` у нового провайдера.

    Сначала спрашивается ЗАМЕР: какая модель этой роли у этого провайдера
    реально давала подтверждённые ответы. Живой замер 2026-08-15 по openai:
    `gpt-5.4-nano` — 50% полностью подтверждённых, `gpt-4o-mini` — 15%. Карта
    уровней предпочла бы второго, потому что он «standard» по имени.

    Карта остаётся полом: пока прогонов меньше `MIN_RUNS`, доля не отличима от
    случайности, и правило по уровню лучше, чем правило по трём случаям.

    Поверх обоих правил — разведка (`scout_model`): на ходу разведчика
    (`scout_turn`) отказ отдаётся незамеренному ровеснику, чтобы у замера
    появлялся материал и таблица не запирала сама себя на первом победителе.
    """
    return substitute_model_with_reason(
        role=role, provider=provider, current_model=current_model,
        workspace=workspace,
    )[0]


def substitute_model_with_reason(
    *, role: str, provider: str, current_model: str | None,
    workspace: Path | None = None,
) -> tuple[str | None, str]:
    """(модель, причина) — решение обязано рассказывать себя.

    Живой разрыв 2026-08-16 (охота №9): при материале 108 (%4=0, ход
    разведчика открыт) выбран nano, и журнал не мог сказать почему — решение
    молчало. Причина едет в route_reason: замер/разведка/пол и материал.
    """
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
        if scout and scout != measured and scout_turn(
                outcomes, role=role, provider=provider):
            return scout, f"scout:{scout}(mat={material})"
        why = (
            "off-turn" if scout and scout != measured
            else ("no-scout-candidate" if not scout else "scout==measured")
        )
        return measured, f"measured:{measured}(mat={material},{why})"
    peer = peer_model_at_same_tier(current_model, provider)
    return peer, f"floor:{peer}(mat={material})"
