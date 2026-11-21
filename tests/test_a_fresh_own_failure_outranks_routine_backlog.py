"""Свежая собственная неудача обгоняет рутинный бэклог — и демон её ВИДИТ.

MIR-186, ночь 2026-08-29. Живой материал: откат первого самопредложенного
раскола честно лёг в память — и не имел ни единого шанса стать выбранным
делом. Две причины, обе тут закрыты:

1. ГОЛОДАНИЕ: кандидат неудачи стоял на 55, рутинный бэклог — на 59; свежая
   боль всегда проигрывала. Но свежая неудача — скоропортящийся сигнал (её
   трасса, дерево и память ещё говорят об одном; живой зонд 2026-08-28
   разошёлся с тиком за 3,5 часа), а измеренный раскол подождёт. Свежая —
   60; здоровье на тех же 60 побеждает связку порядком допуска (оно первым).
2. СЛЕПОТА: безлюдный путь кампании вообще не передавал неудачи в
   выбиратель — только REPL-команда, где и так сидит человек. Класс «The
   unattended path was the blind one», этажом ниже своей первой поимки.

Красные свидетели: до починки у кандидата не было параметра свежести, у
демона — проводки неудач, у детектора свежести — самого себя.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from core.best_next_action import (
    _P_TESTS_INCONCLUSIVE,
    _candidate_self_improvement_failure,
)
from core.self_build_memory import has_fresh_self_improvement_failure
from core.smart_memory import EpisodeRecord


def test_fresh_pain_never_outranks_health() -> None:
    """Граница: здоровье выше — связка на 60 решается порядком допуска."""
    fresh = _candidate_self_improvement_failure(("rollback",), fresh=True)

    assert fresh is not None
    assert fresh.priority <= _P_TESTS_INCONCLUSIVE


def _episode(created_at: datetime) -> EpisodeRecord:
    return EpisodeRecord(
        goal="apply self-build proposal ain_x",
        question="self-apply-run",
        outcome="failed",
        summary="self-apply rolled_back: targeted tests failed",
        tags=("self-build", "lesson", "self-apply-run", "rolled_back", "failed"),
        created_at=created_at.isoformat(),
    )


def _agent_with(episodes) -> SimpleNamespace:
    return SimpleNamespace(
        episodic_store=SimpleNamespace(load=lambda: list(episodes)),
        log=None,
    )


def test_the_freshness_detector_reads_the_stamp(tmp_path) -> None:
    now = datetime.now(timezone.utc)
    fresh_agent = _agent_with([_episode(now - timedelta(hours=2))])
    stale_agent = _agent_with([_episode(now - timedelta(days=3))])

    assert has_fresh_self_improvement_failure(fresh_agent, tmp_path) is True
    assert has_fresh_self_improvement_failure(stale_agent, tmp_path) is False
