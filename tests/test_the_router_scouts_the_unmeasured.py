"""Роутер изредка разведывает незамеренного ровесника — иначе замер запирает сам себя.

Background: docs/CODE_NOTES.md, "The measurement that locked itself in".
"""
from __future__ import annotations

import core.model_outcomes as mo
from core.model_outcomes import (
    MIN_RUNS,
    measure_model_outcomes,
    scout_model,
    scout_window,
    substitute_model,
)

#: Живая пара 2026-08-15: nano замерен и выигрывает каждый отказ, terra — лучшая
#: standard-модель того же ключа — не имеет ни одного прогона с вердиктом.
_MEASURED = "gpt-5.4-nano"
_UNMEASURED_PEER = "gpt-5.6-terra"


def _corpus(model: str, n: int = MIN_RUNS):
    usage = [
        {"run_id": f"r{i}", "role": "planner", "provider": "openai",
         "model": model, "status": "success"}
        for i in range(n)
    ]
    episodes = [
        {"run_id": f"r{i}", "verified_chunks": 3, "unverified_chunks": 0,
         "defect_signals": []}
        for i in range(n)
    ]
    return usage, episodes


def _outcomes(*models_runs: tuple[str, int]):
    usage, episodes = [], []
    for model, n in models_runs:
        u, e = _corpus(model, n)
        for i, (uu, ee) in enumerate(zip(u, e, strict=True)):
            uu["run_id"] = ee["run_id"] = f"{model}-{i}"
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
    решает таблица, а не окно.
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


def test_the_window_opens_one_bucket_in_four():
    """Детерминировано по часам, без датчика случайности: ~10 минут разведки
    из каждых 40, и тот же момент времени даёт тот же ответ.
    """
    assert scout_window(0.0) is True
    assert scout_window(600.0) is False
    assert scout_window(1200.0) is False
    assert scout_window(1800.0) is False
    assert scout_window(2400.0) is True


def test_substitute_sends_the_scout_inside_the_window(monkeypatch):
    _pin_world(monkeypatch)
    monkeypatch.setattr(
        mo, "measured_outcomes",
        lambda _w=None: _outcomes((_MEASURED, MIN_RUNS)),
    )

    picked = substitute_model(
        role="planner", provider="openai",
        current_model="claude-sonnet-5", now=0.0,
    )

    assert picked == _UNMEASURED_PEER


def test_substitute_keeps_the_measured_winner_outside_the_window(monkeypatch):
    _pin_world(monkeypatch)
    monkeypatch.setattr(
        mo, "measured_outcomes",
        lambda _w=None: _outcomes((_MEASURED, MIN_RUNS)),
    )

    picked = substitute_model(
        role="planner", provider="openai",
        current_model="claude-sonnet-5", now=600.0,
    )

    assert picked == _MEASURED


def test_the_floor_is_untouched_when_nothing_is_measured(monkeypatch):
    """Без замеров разведке нечего разведывать особым путём: пол (карта
    уровней) и так отдаёт ровесника, в окне и вне окна одинаково.
    """
    _pin_world(monkeypatch)
    monkeypatch.setattr(mo, "measured_outcomes", lambda _w=None: ())

    for now in (0.0, 600.0):
        assert substitute_model(
            role="planner", provider="openai",
            current_model="claude-sonnet-5", now=now,
        ) == _UNMEASURED_PEER


def test_the_live_selection_carries_the_scout():
    """Вторая половина дороги: разведка обязана стоять в живом пути выбора,
    а не только существовать чистой функцией рядом.
    """
    import inspect

    source = inspect.getsource(substitute_model)
    assert "scout_model(" in source
    assert "scout_window(" in source
