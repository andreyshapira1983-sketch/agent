"""Собственная остановка попадает в поток наблюдений — иначе лестница слепа.

Замер 2026-09-01: четыре самостоятельных запуска не дали работы, и ни один
отказ не попал в `data/causal_observations.jsonl`. Причинная лестница агента
объясняла что угодно, кроме собственных стен, а спрошенный напрямую агент
назвал причиной чужую ошибку из соседнего прогона — материала о себе у него
просто не было.

Мостик собирает наблюдение из СТАБИЛЬНЫХ частей стены (род и каноническая
причина), поэтому та же стена копит счётчик повторений вместо того, чтобы
плодить записи, а разные стены остаются разными поводами. Без источника
наблюдения не бывает — то же правило, что у журнала остановок.
"""
from __future__ import annotations

from core.causal_lesson import observation_from_self_stop
from core.causal_store import CausalObservationStore
from core.self_stop_record import record_stop_observation


def _stop_kwargs(**overrides):
    base = {
        "kind": "goal_selection_failure",
        "reason": "goal_repeat",
        "signature": "abc123def456",
        "source": "data/charter_decisions.jsonl",
    }
    base.update(overrides)
    return base


def test_a_different_wall_is_a_different_reason_to_investigate(tmp_path):
    (tmp_path / "data").mkdir()

    goal_wall = record_stop_observation(tmp_path, **_stop_kwargs())
    budget_wall = record_stop_observation(tmp_path, **_stop_kwargs(
        kind="budget_stop", reason="propose_engineering_task:cap",
        source="data/campaign_ledger.jsonl",
    ))

    assert goal_wall and budget_wall and goal_wall != budget_wall
    store = CausalObservationStore(
        tmp_path / "data" / "causal_observations.jsonl")
    assert len(store.load()) == 2


def test_without_a_source_there_is_no_observation(tmp_path):
    (tmp_path / "data").mkdir()

    assert observation_from_self_stop(**_stop_kwargs(source="")) is None
    assert record_stop_observation(tmp_path, **_stop_kwargs(source="")) == ""
    assert not (tmp_path / "data" / "causal_observations.jsonl").exists()
