"""Роутер изредка разведывает незамеренного ровесника — иначе замер запирает сам себя.

Background: docs/CODE_NOTES.md, "The measurement that locked itself in".
"""
from __future__ import annotations

import core.model_outcomes as mo
from core.model_outcomes import (
    MIN_RUNS,
    SCOUT_PERIOD,
    measure_model_outcomes,
    scout_model,
    scout_turn,
    substitute_model,
)

#: Живая пара 2026-08-15: nano замерен и выигрывает каждый отказ, terra — лучшая
#: standard-модель того же ключа — не имеет ни одного прогона с вердиктом.
_MEASURED = "gpt-5.4-nano"
_UNMEASURED_PEER = "gpt-5.6-terra"


def _corpus(model: str, n: int = MIN_RUNS):
    usage = [
        {"run_id": f"{model}-{i}", "role": "planner", "provider": "openai",
         "model": model, "status": "success"}
        for i in range(n)
    ]
    episodes = [
        {"run_id": f"{model}-{i}", "verified_chunks": 3, "unverified_chunks": 0,
         "defect_signals": []}
        for i in range(n)
    ]
    return usage, episodes


def _outcomes(*models_runs: tuple[str, int]):
    usage, episodes = [], []
    for model, n in models_runs:
        u, e = _corpus(model, n)
        usage += u
        episodes += e
    return measure_model_outcomes(usage, episodes)


def _pin_world(monkeypatch, *, peer=_UNMEASURED_PEER):
    monkeypatch.setattr(mo, "peer_model_at_same_tier", lambda _m, _p: peer)
    monkeypatch.setattr(
        mo, "offered_models",
        lambda _p: frozenset({_MEASURED, _UNMEASURED_PEER}),
    )


def test_an_unmeasured_peer_is_named_as_scout(monkeypatch):
    _pin_world(monkeypatch)

    picked = scout_model(
        _outcomes((_MEASURED, MIN_RUNS)),
        role="planner", provider="openai", current_model="claude-sonnet-5",
    )

    assert picked == _UNMEASURED_PEER


def test_a_measured_peer_is_not_scouted(monkeypatch):
    """Разведка кончается там, где начинается замер: набрал прогоны — дальше
    решает таблица, а не очередь.
    """
    _pin_world(monkeypatch)

    picked = scout_model(
        _outcomes((_MEASURED, MIN_RUNS), (_UNMEASURED_PEER, MIN_RUNS)),
        role="planner", provider="openai", current_model="claude-sonnet-5",
    )

    assert picked is None


def test_an_undermeasured_peer_is_still_scouted(monkeypatch):
    """Два прогона — не замер: молчащей строке таблицы материал ещё нужен."""
    _pin_world(monkeypatch)

    picked = scout_model(
        _outcomes((_MEASURED, MIN_RUNS), (_UNMEASURED_PEER, 2)),
        role="planner", provider="openai", current_model="claude-sonnet-5",
    )

    assert picked == _UNMEASURED_PEER


def test_no_peer_means_no_scout(monkeypatch):
    _pin_world(monkeypatch, peer=None)

    picked = scout_model(
        _outcomes((_MEASURED, MIN_RUNS)),
        role="planner", provider="openai", current_model="claude-sonnet-5",
    )

    assert picked is None


def test_the_turn_advances_with_decisions_not_with_the_capped_table():
    """Две фальсификации одной конструкции. Первая (2026-08-15): окно по
    настенным часам пропустило всю охоту — вспышка короче закрытого отрезка.
    Вторая (2026-08-16): счёт по прогонам таблицы замер НАВСЕГДА — таблица
    питается кольцевым буфером эпизодов (потолок 200), плюс прогон — минус
    вытесненный, mat=110 на шести решениях трёх попыток подряд, остаток %4
    не меняется, ход не выпадает никогда. Часы хода обязаны быть монотонными:
    считаются РЕШЕНИЯ по append-only журналу вызовов.
    """
    assert scout_turn(SCOUT_PERIOD * 2) is True
    assert scout_turn(SCOUT_PERIOD * 2 + 1) is False
    assert scout_turn(0) is True, "первое решение в истории — ход разведчика"


def test_decisions_are_counted_per_role_from_the_ledger(tmp_path):
    """Счёт у каждой пары роль+провайдер свой; один прогон — одно решение,
    сколько бы вызовов подменённая модель в нём ни сделала.
    """
    import json

    from core.model_outcomes import failover_decisions

    ledger = tmp_path / "data" / "model_usage.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"role": "planner", "provider": "openai", "run_id": "r1",
         "route_reason": "provider_failover:anthropic->openai|x", "status": "success"},
        {"role": "planner", "provider": "openai", "run_id": "r1",
         "route_reason": "provider_failover:anthropic->openai|x", "status": "success"},
        {"role": "planner", "provider": "openai", "run_id": "r2",
         "route_reason": "provider_failover:anthropic->openai|x", "status": "success"},
        {"role": "synthesizer", "provider": "openai", "run_id": "r3",
         "route_reason": "provider_failover:anthropic->openai|x", "status": "success"},
        {"role": "planner", "provider": "openai", "run_id": "r4",
         "route_reason": "policy:balanced", "status": "success"},
    ]
    ledger.write_text(
        "".join(json.dumps({"payload": r}) + "\n" for r in rows), encoding="utf-8",
    )

    assert failover_decisions(tmp_path, role="planner", provider="openai") == 2
    assert failover_decisions(tmp_path, role="synthesizer", provider="openai") == 1


def test_substitute_sends_the_scout_on_its_turn(monkeypatch):
    _pin_world(monkeypatch)
    monkeypatch.setattr(
        mo, "measured_outcomes",
        lambda _w=None: _outcomes((_MEASURED, MIN_RUNS)),
    )
    monkeypatch.setattr(
        mo, "failover_decisions", lambda *_a, **_k: SCOUT_PERIOD * 2,
    )

    picked = substitute_model(
        role="planner", provider="openai", current_model="claude-sonnet-5",
    )

    assert picked == _UNMEASURED_PEER


def test_substitute_keeps_the_measured_winner_off_turn(monkeypatch):
    _pin_world(monkeypatch)
    monkeypatch.setattr(
        mo, "measured_outcomes",
        lambda _w=None: _outcomes((_MEASURED, MIN_RUNS)),
    )
    monkeypatch.setattr(
        mo, "failover_decisions", lambda *_a, **_k: SCOUT_PERIOD * 2 + 1,
    )

    picked = substitute_model(
        role="planner", provider="openai", current_model="claude-sonnet-5",
    )

    assert picked == _MEASURED


def test_the_floor_is_untouched_when_nothing_is_measured(monkeypatch):
    """Без замеров разведке нечего разведывать особым путём: пол (карта
    уровней) и так отдаёт ровесника.
    """
    _pin_world(monkeypatch)
    monkeypatch.setattr(mo, "measured_outcomes", lambda _w=None: ())

    picked = substitute_model(
        role="planner", provider="openai", current_model="claude-sonnet-5",
    )

    assert picked == _UNMEASURED_PEER


def test_the_live_selection_carries_the_scout():
    """Вторая половина дороги: разведка обязана стоять в живом пути выбора,
    а не только существовать чистой функцией рядом.
    """
    import inspect

    from core.model_outcomes import substitute_model_with_reason

    source = inspect.getsource(substitute_model_with_reason)
    assert "scout_model(" in source
    assert "scout_turn(" in source
    assert "substitute_model_with_reason(" in inspect.getsource(substitute_model)


def test_the_substitution_names_its_reason(monkeypatch):
    """Живой разрыв 2026-08-16 (охота №9): при материале 108 (%4=0, ход
    разведчика открыт) выбран nano, и по journal нельзя сказать почему —
    решение молчало. Замена обязана рассказывать себя: замер/разведка/пол
    и материал на момент решения.
    """
    from core.model_outcomes import substitute_model_with_reason

    _pin_world(monkeypatch)
    monkeypatch.setattr(
        mo, "measured_outcomes",
        lambda _w=None: _outcomes((_MEASURED, MIN_RUNS)),
    )
    monkeypatch.setattr(
        mo, "failover_decisions", lambda *_a, **_k: SCOUT_PERIOD * 2,
    )
    model, reason = substitute_model_with_reason(
        role="planner", provider="openai", current_model="claude-sonnet-5",
    )
    assert model == _UNMEASURED_PEER
    assert "scout" in reason and "dec=8" in reason

    monkeypatch.setattr(
        mo, "failover_decisions", lambda *_a, **_k: SCOUT_PERIOD * 2 + 1,
    )
    model, reason = substitute_model_with_reason(
        role="planner", provider="openai", current_model="claude-sonnet-5",
    )
    assert model == _MEASURED
    assert "measured" in reason and "dec=9" in reason
