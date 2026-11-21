"""Модель выбирается по измеренному исходу, а не по своему имени.

Background: docs/CODE_NOTES.md, "Which model earns the role".
"""
from __future__ import annotations

from core.model_outcomes import (
    MIN_RUNS,
    measure_model_outcomes,
    preferred_model,
)


def _call(run: str, model: str, *, role: str = "planner",
          provider: str = "openai", status: str = "success") -> dict:
    return {"run_id": run, "role": role, "provider": provider,
            "model": model, "status": status}


def _episode(run: str, *, verified: int, unverified: int = 0,
             signals: tuple = ()) -> dict:
    return {"run_id": run, "verified_chunks": verified,
            "unverified_chunks": unverified, "defect_signals": list(signals)}


def _corpus(good: str, bad: str, n: int = MIN_RUNS):
    """`good` подтверждается целиком, `bad` — никогда. Пропорции живого замера
    2026-08-15: gpt-5.4-nano 50% против gpt-4o-mini 15%.
    """
    usage, episodes = [], []
    for i in range(n):
        usage.append(_call(f"g{i}", good))
        episodes.append(_episode(f"g{i}", verified=3))
        usage.append(_call(f"b{i}", bad))
        episodes.append(_episode(f"b{i}", verified=1, unverified=2,
                                 signals=("citation_fabricated",)))
    return usage, episodes


def test_a_provider_refusal_is_not_charged_to_the_model():
    """Отказ провайдера — факт о ключе оператора, а не о качестве модели.
    Живой контекст: 73 отказа anthropic за 2026-08-15 при пустом счёте.
    """
    usage = [_call(f"r{i}", "claude-sonnet-5", status="error") for i in range(20)]
    episodes = [_episode(f"r{i}", verified=0, unverified=5) for i in range(20)]

    assert measure_model_outcomes(usage, episodes) == ()


def test_one_run_counts_once_however_many_calls_it_made():
    """Иначе многословный прогон весил бы больше короткого."""
    usage = [_call("r1", "gpt-4o-mini") for _ in range(9)]
    episodes = [_episode("r1", verified=2)]

    outcomes = measure_model_outcomes(usage, episodes)

    assert [o.runs for o in outcomes] == [1]


def test_a_measured_winner_that_the_world_no_longer_offers_is_dropped():
    """Опыт живёт дольше мира.

    Замер 2026-08-15: `claude-sonnet-4-5` — 67% полностью подтверждённых на 63
    прогонах, сильнейшее свидетельство в таблице. В текущем каталоге такого
    имени нет: провайдер его больше не предлагает. Голосовать за него значит
    выбирать вчерашний день по вчерашним данным.
    """
    usage, episodes = _corpus("снятая-с-производства", "живая")
    outcomes = measure_model_outcomes(usage, episodes)

    assert preferred_model(outcomes, role="planner", provider="openai") == "снятая-с-производства"
    assert preferred_model(
        outcomes, role="planner", provider="openai", offered={"живая"},
    ) == "живая"
