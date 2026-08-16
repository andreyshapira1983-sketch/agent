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


def test_the_turn_is_counted_in_runs_not_in_clock_time():
    """Ломка первой конструкции, живой замер 2026-08-15: окно по настенным
    часам (10 минут из 40) пропустило ВСЮ охоту №3 — 12 failover-решений за
    4 минуты, все в закрытом отрезке, разведчик не получил ни одного вызова.
    Нагрузка живёт вспышками, и доля времени не равна доле решений. Ход
    разведчика считается по числу прогонов в таблице: каждый SCOUT_PERIOD-й
    прогон отдаёт следующее решение разведчику — вспышка не может проскочить
    мимо, потому что счёт растёт самими прогонами.
    """
    on_turn = _outcomes((_MEASURED, SCOUT_PERIOD * 2))
    off_turn = _outcomes((_MEASURED, SCOUT_PERIOD * 2 + 1))

    assert scout_turn(on_turn, role="planner", provider="openai") is True
    assert scout_turn(off_turn, role="planner", provider="openai") is False


def test_other_roles_and_providers_do_not_advance_the_turn():
    """Счёт у каждой пары роль+провайдер свой, как и сама таблица."""
    usage, episodes = _corpus(_MEASURED, SCOUT_PERIOD * 2)
    for i in range(3):
        usage.append({"run_id": f"s{i}", "role": "synthesizer",
                      "provider": "openai", "model": _MEASURED,
                      "status": "success"})
        episodes.append({"run_id": f"s{i}", "verified_chunks": 1,
                         "unverified_chunks": 0, "defect_signals": []})
    outcomes = measure_model_outcomes(usage, episodes)

    assert scout_turn(outcomes, role="planner", provider="openai") is True


def test_substitute_sends_the_scout_on_its_turn(monkeypatch):
    _pin_world(monkeypatch)
    monkeypatch.setattr(
        mo, "measured_outcomes",
        lambda _w=None: _outcomes((_MEASURED, SCOUT_PERIOD * 2)),
    )

    picked = substitute_model(
        role="planner", provider="openai", current_model="claude-sonnet-5",
    )

    assert picked == _UNMEASURED_PEER


def test_substitute_keeps_the_measured_winner_off_turn(monkeypatch):
    _pin_world(monkeypatch)
    monkeypatch.setattr(
        mo, "measured_outcomes",
        lambda _w=None: _outcomes((_MEASURED, SCOUT_PERIOD * 2 + 1)),
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
        lambda _w=None: _outcomes((_MEASURED, SCOUT_PERIOD * 2)),
    )
    model, reason = substitute_model_with_reason(
        role="planner", provider="openai", current_model="claude-sonnet-5",
    )
    assert model == _UNMEASURED_PEER
    assert "scout" in reason and "mat=8" in reason

    monkeypatch.setattr(
        mo, "measured_outcomes",
        lambda _w=None: _outcomes((_MEASURED, SCOUT_PERIOD * 2 + 1)),
    )
    model, reason = substitute_model_with_reason(
        role="planner", provider="openai", current_model="claude-sonnet-5",
    )
    assert model == _MEASURED
    assert "measured" in reason and "mat=9" in reason
