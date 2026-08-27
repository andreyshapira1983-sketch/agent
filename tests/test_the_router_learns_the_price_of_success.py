"""Выбор модели читает цену успеха, а не только его долю.

Замер, отвергнутые варианты и границы: MIR-176 в docs/audit/MASTER_ISSUE_REGISTRY.md.

Тридцатилетняя посылка (дайджест, записи 6 и 7): роутер, обученный на исходах,
экономит 85 % при 95 % качества, а отчёт без знаменателя учит «тратить больше =
становиться лучше». До этой правки слово «стоимость» в органе выбора не
встречалось ни разу — измерено grep'ом 2026-08-27.
"""
from __future__ import annotations

from core.model_outcomes import ModelOutcome, preferred_model


def _o(model: str, runs: int, ok: int, *, defects: int = 0) -> ModelOutcome:
    return ModelOutcome(
        role="synthesizer", provider="openai", model=model,
        runs=runs, fully_verified=ok, with_defect=defects,
    )


def test_equally_capable_models_resolve_to_the_cheaper_tier() -> None:
    """Красный свидетель: неотличимое по качеству не должно стоить втрое.

    `gpt-5.6-terra` — уровень standard (3 ед./1k), `o5-mini` — deep (8 ед./1k).
    Доли подтверждённых различаются на 0.025 — меньше цены одного прогона при
    MIN_RUNS. Прежний порядок выбирал дорогую за третий знак.
    """
    cheap_standard = _o("gpt-5.6-terra", 40, 36)   # 0.900
    dear_deep = _o("o5-mini", 40, 37)              # 0.925

    pick = preferred_model(
        [dear_deep, cheap_standard], role="synthesizer", provider="openai",
    )

    assert pick == "gpt-5.6-terra"


def test_measurably_better_quality_is_never_traded_for_price() -> None:
    """Контроль против болезни спуска: дешёвое НЕ покупает измеримо худшее.

    Ровно от этой болезни чинили H-tier (73 спуска за сутки): «дешевле» не
    аргумент, когда качество различимо. Допуск меньше шага одной ошибки при
    MIN_RUNS=8, поэтому на минимуме прогонов вниз не меняют никогда.
    """
    weak_cheap = _o("gpt-5.6-terra", 40, 32)       # 0.800
    strong_dear = _o("o5-mini", 40, 37)            # 0.925

    pick = preferred_model(
        [weak_cheap, strong_dear], role="synthesizer", provider="openai",
    )

    assert pick == "o5-mini"


def test_same_tier_still_ranks_by_quality() -> None:
    """Контроль: внутри одного уровня цены порядок прежний — по качеству."""
    a = _o("gpt-5.6-terra", 40, 37)
    b = _o("gpt-5.6-sol", 40, 33)

    pick = preferred_model([b, a], role="synthesizer", provider="openai")

    assert pick == "gpt-5.6-terra"


def test_defect_share_breaks_ties_within_the_bar() -> None:
    """При равной цене и равной доле — меньше дефектов, как и раньше."""
    clean = _o("gpt-5.6-terra", 40, 36, defects=1)
    dirty = _o("gpt-5.6-sol", 40, 36, defects=9)

    pick = preferred_model([dirty, clean], role="synthesizer", provider="openai")

    assert pick == "gpt-5.6-terra"


def test_below_min_runs_still_yields_nothing() -> None:
    """Шум не становится свидетельством оттого, что он дешёвый."""
    assert preferred_model(
        [_o("gpt-4o-mini", 3, 3)], role="synthesizer", provider="openai",
    ) is None


def test_the_tier_bridge_covers_every_complexity_tier() -> None:
    """Мост словарей полон: новый уровень не провалится в «unknown» молча.

    Первая версия оси не переводила `light/standard/deep` в `low/medium/high`,
    и обе модели свидетеля получили одинаковый тариф «unknown» — ось не
    работала, а тесты без этой проверки не сказали бы почему.
    """
    from core.model_catalog import ComplexityTier
    from core.model_outcomes import _TIER_TO_COST
    from core.model_usage import cost_units_per_1k

    for tier in ComplexityTier:
        assert tier.value in _TIER_TO_COST, tier
    prices = {cost_units_per_1k(v) for v in _TIER_TO_COST.values()}
    assert len(prices) == len(_TIER_TO_COST), (
        "уровни слились в одну цену — ось перестала различать"
    )
