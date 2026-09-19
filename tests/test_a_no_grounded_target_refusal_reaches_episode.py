"""Отказ продукта доезжает до будущей подсказки — успех его не стирает (MIR-184).

Имя файла и замысел принадлежат САМОМУ АГЕНТУ: в прогоне run_e4d9a8e8
(2026-08-29, серия «учимся программировать себя») он спроектировал этот тест
сам — «tests/test_a_no_grounded_target_refusal_reaches_episode.py; тест
фиксирует оба значения одновременно» — а его страж улик задушил код по
дороге. Реализовано по слову оператора («начни лечить эти болезни»), поверх
вскрытой анатомии: отказ БЫЛ записан честным эпизодом-братом, но у брата
пустая связка (run_id=""), фильтр впрыска пускает только success — честность
записана и структурно неслышима.

Лечение: EpisodeRecord несёт trace_id (семейную связку тика), оба писаря её
заполняют, читатель впрыска сшивает семью и подаёт подсказке ОБА исхода.

Красные свидетели до починки: у эпизодов не было trace_id вовсе; сшивки в
читателе не существовало.
"""
from __future__ import annotations

from core.self_build_memory import build_self_build_episode
from core.smart_memory import (
    EpisodeRecord,
    episode_from_agent_cycle,
    family_product_warnings,
)

_TRACE = "trace_0aab958417927d8deabc839bb7fcb428"


def _poisoned_success() -> EpisodeRecord:
    return episode_from_agent_cycle(
        goal="Campaign goal: split the measured module",
        question="campaign cycle",
        answer="Сначала вручную выберите один реальный TODO/FIXME…",
        tools_used=["file_read"],
        source_labels=["file:core/x.py"],
        verified_chunks=4,
        run_id="run_fc8125",
        trace_id=_TRACE,
        usage_eligible=True,
    )


def _honest_refusal() -> EpisodeRecord:
    return build_self_build_episode(
        "self-build-produce",
        {
            "status": "no_grounded_target",
            "reason": "grounded target 'core/self_build_producer.py' is critical",
        },
        trace_id=_TRACE,
    )


def test_both_writers_carry_the_family_link() -> None:
    assert _poisoned_success().trace_id == _TRACE
    assert _honest_refusal().trace_id == _TRACE


def test_a_success_episode_cannot_shed_its_declined_product() -> None:
    """Ядро замысла агента: оба исхода одного прогона — одновременно."""
    success, refusal = _poisoned_success(), _honest_refusal()

    warnings = family_product_warnings([success], [success, refusal])

    assert len(warnings) == 1
    assert "no_grounded_target" in warnings[0], (
        "отказ продукта не доехал до подсказки — успех снова стёр его")


def test_an_unrelated_trace_is_not_glued_on() -> None:
    """Граница: чужой отказ из другого прогона к успеху не пришивается."""
    success = _poisoned_success()
    stranger = build_self_build_episode(
        "self-build-produce",
        {"status": "no_grounded_target", "reason": "other tick"},
        trace_id="trace_другого_прогона",
    )

    assert family_product_warnings([success], [success, stranger]) == []


def test_a_legacy_row_without_the_link_stays_silent_not_broken() -> None:
    """Граница: старые строки без trace_id читаются и просто не сшиваются."""
    legacy = EpisodeRecord.from_dict({
        "goal": "g", "question": "q", "outcome": "success", "summary": "s",
    })

    assert legacy.trace_id == ""
    assert family_product_warnings([legacy], [legacy]) == []


def test_the_reader_wires_the_join_into_the_block() -> None:
    """Проводка: сшивка стоит в читателе впрыска, а не только в библиотеке."""
    import inspect

    from core import loop_memory_read

    src = inspect.getsource(loop_memory_read)
    assert "family_product_warnings" in src
    assert "Продуктовые исходы тех же прогонов" in src
