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


def test_a_success_episode_cannot_shed_its_declined_product() -> None:
    """Ядро замысла агента: оба исхода одного прогона — одновременно."""
    success, refusal = _poisoned_success(), _honest_refusal()

    warnings = family_product_warnings([success], [success, refusal])

    assert len(warnings) == 1
    assert "no_grounded_target" in warnings[0], (
        "отказ продукта не доехал до подсказки — успех снова стёр его")
