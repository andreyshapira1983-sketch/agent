"""Критерий успеха не имеет права требовать несобранного (LPF-007).

Каждая цель строилась с критерием «grounded answer citing every claim back to
a provided source» — и на ходах, где источники не собираются вовсе (светская
реплика, чистый синтез), обязанность цитировать выданное превращалась в
обязанность выдумывать. `docs/LIVE_PROBE_FINDINGS.md` зафиксировал это как
корень петли LPF-007: обязательная цитата → источников нет → синтез цитирует
память → фабрикация → плохой эпизод уходит в опыт.

Трасса 2026-08-13 показала тот же критерий на «скажи что-нибудь умное».
Починка — формулировка, выполнимая на ЛЮБОМ ходе: цитируется то, что собрано;
несобранное подаётся как неподтверждённое; выдумывать нельзя никогда.
"""
from __future__ import annotations

from types import SimpleNamespace

from core.loop_observe import AgentLoopObserve
from core.models import Observation


def _goal_for(question: str):
    observation = Observation(
        source="cli", modality="text", content={"question": question},
        provenance="user",
    )
    return AgentLoopObserve._interpret(SimpleNamespace(), observation)


def test_the_criterion_does_not_demand_citations_unconditionally() -> None:
    """ГЛАВНОЕ: «every claim back to a provided source» ушло из каждой цели."""
    goal = _goal_for("скажи что-нибудь умное")
    assert "every claim back to a provided source" not in goal.success_criteria


def test_the_criterion_is_conditional_on_collected_evidence() -> None:
    """Обязанность цитировать возникает у собранного, не у воображаемого."""
    criteria = _goal_for("скажи что-нибудь умное").success_criteria
    assert "collected" in criteria
    assert "never invented" in criteria


def test_unbacked_claims_owe_an_unverified_label_not_a_citation() -> None:
    """Честный выход для хода без источников назван прямо в критерии."""
    criteria = _goal_for("what is a monad").success_criteria
    assert "unverified" in criteria
