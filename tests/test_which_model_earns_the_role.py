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


def test_the_measured_winner_is_chosen():
    outcomes = measure_model_outcomes(*_corpus("gpt-5.4-nano", "gpt-4o-mini"))

    assert preferred_model(outcomes, role="planner", provider="openai") == "gpt-5.4-nano"


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


def test_too_few_runs_stay_silent():
    """Молчание — не отказ, а честность: до `MIN_RUNS` доля неотличима от
    случайности, и решать по трём случаям хуже, чем по правилу уровня.
    """
    outcomes = measure_model_outcomes(
        *_corpus("gpt-5.4-nano", "gpt-4o-mini", n=MIN_RUNS - 1)
    )

    assert preferred_model(outcomes, role="planner", provider="openai") is None


def test_a_call_without_an_episode_is_not_counted():
    """Вызов без исхода ничего не говорит о качестве — считать его значило бы
    добирать объём пустотой.
    """
    assert measure_model_outcomes([_call("r1", "gpt-4o-mini")], []) == ()


def test_roles_and_providers_do_not_bleed_into_each_other():
    """У планировщика и синтезатора разная работа; общий счёт скрыл бы, что
    модель хороша на одной роли и плоха на другой.
    """
    usage, episodes = _corpus("gpt-5.4-nano", "gpt-4o-mini")
    usage += [_call(f"s{i}", "gpt-4o-mini", role="synthesizer") for i in range(MIN_RUNS)]
    episodes += [_episode(f"s{i}", verified=4) for i in range(MIN_RUNS)]

    outcomes = measure_model_outcomes(usage, episodes)

    assert preferred_model(outcomes, role="planner", provider="openai") == "gpt-5.4-nano"
    assert preferred_model(outcomes, role="synthesizer", provider="openai") == "gpt-4o-mini"


def test_defects_break_a_tie_on_verification():
    """Вторая ось — сигналы дефектов. При равной доле подтверждённых выигрывает
    та модель, что реже поднимала детекторы.
    """
    usage, episodes = [], []
    for i in range(MIN_RUNS):
        usage.append(_call(f"c{i}", "тихая"))
        episodes.append(_episode(f"c{i}", verified=2))
        usage.append(_call(f"n{i}", "шумная"))
        episodes.append(_episode(f"n{i}", verified=2,
                                 signals=("reasoning_action_mismatch",)))

    outcomes = measure_model_outcomes(usage, episodes)

    assert preferred_model(outcomes, role="planner", provider="openai") == "тихая"


def test_the_router_asks_the_measurement_before_the_tier_map():
    """Вторая половина дороги. Порядок здесь и есть вся правка: сначала замер,
    карта имён — пол.
    """
    import inspect

    from core.model_router import UsageTrackedLLM

    source = inspect.getsource(UsageTrackedLLM._failover_llm)
    assert "substitute_model(" in source
    assert "peer_model_at_same_tier" not in source


def test_the_tier_map_still_answers_when_nothing_is_measured():
    """Пол на месте: без замеров решение прежнее, а не отсутствующее."""
    from core.model_outcomes import substitute_model

    picked = substitute_model(
        role="роль-которой-не-было", provider="openai",
        current_model="claude-sonnet-5", workspace="/nonexistent",
    )

    assert picked, "без замеров не осталось даже прежнего правила"


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


def test_an_unobserved_world_does_not_veto_experience():
    """Пустое наблюдение — «не знаю», а не «ничего не предлагают». Разница
    между незнанием и отрицанием здесь стоит выбора модели.
    """
    outcomes = measure_model_outcomes(*_corpus("gpt-5.4-nano", "gpt-4o-mini"))

    assert preferred_model(
        outcomes, role="planner", provider="openai", offered=None,
    ) == "gpt-5.4-nano"


def test_when_the_world_retired_everything_measured_the_floor_answers():
    """Все измеренные сняты — замер молчит, и решает карта уровней, а не
    пустота.
    """
    outcomes = measure_model_outcomes(*_corpus("снятая-1", "снятая-2"))

    assert preferred_model(
        outcomes, role="planner", provider="openai", offered={"совсем-другая"},
    ) is None


def test_the_live_selection_consults_the_world():
    """Вторая половина дороги: сверка обязана стоять в живом пути, а не только
    в чистой функции.
    """
    import inspect

    from core.model_outcomes import substitute_model

    source = inspect.getsource(substitute_model)
    assert "offered_models(provider)" in source
