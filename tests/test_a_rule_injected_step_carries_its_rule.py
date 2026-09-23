"""Шаг, который дописало правило, несёт довод — само правило.

Замер 2026-09-23 по 898 срабатываниям датчика «шаг без довода»
(`check_by_rationale`): в 799 обвинён только file_read, в 515 планах без довода
ровно 4 шага. Это чтения доктрины, которые дописывает маршрутизация
(`core/doc_routing.py`: 5 документов, один модель обычно называет сама). У
шагов модели довод был, у дописанных кодом — нет, и датчик неделями обвинял
агента в действиях, которых он не выбирал.

Первый диагноз (запись в памяти агента 23.09 18:33) называл другое место —
`loop_attempt._build_plan`; он неверен: датчик читает выход планировщика
(`planner_out.sources`), а не шаги плана. Проверено по коду вызова и по
журналам.
"""
from __future__ import annotations

import pytest

import core.doc_routing as dr
from core.reasoning_action_check import check_by_rationale

MODEL_STEP = {"tool": "file_read", "arguments": {"path": "core/loop.py"},
              "label": "file:core/loop.py", "rationale": "the loop is where the question points"}


def _doctrine(sources):
    return dr._ensure_doctrine_docs_first(sources, [], drop_default_code_sources=False)


def _confidence(sources):
    return dr._ensure_confidence_evidence_sources_first(sources, [], drop_low_signal_defaults=False)


@pytest.mark.parametrize("inject", [
    _doctrine,
    _confidence,
    lambda s: dr._ensure_subagent_governance_docs_first(s, []),
    lambda s: dr._ensure_memory_governance_docs_first(s, []),
    lambda s: dr._ensure_self_repair_doctrine_docs_first(s, []),
], ids=["doctrine", "confidence", "subagent", "memory", "self_repair"])
def test_every_injected_step_names_the_rule_that_put_it_there(inject) -> None:
    out = inject([dict(MODEL_STEP)])
    injected = [s for s in out if s is not MODEL_STEP and s.get("arguments") != MODEL_STEP["arguments"]]
    assert injected, "правило ничего не дописало — проверять нечего"
    for step in injected:
        assert (step.get("rationale") or "").startswith("routing rule: ")
        assert len(step["rationale"].split()) >= 3
    assert not check_by_rationale(out).has_mismatch, "датчик обвинил шаг, поставленный правилом"


def test_the_detector_still_accuses_a_model_step_without_a_reason() -> None:
    """Сторож не ослаблен: шаг МОДЕЛИ без довода по-прежнему обвиняется."""
    bare = {"tool": "list_dir", "arguments": {"path": "core"}, "label": "list_dir:core"}
    out = _doctrine([dict(MODEL_STEP), bare])
    report = check_by_rationale(out)
    assert report.has_mismatch and "list_dir" in report.unjustified_actions


def test_a_new_rule_cannot_forget_the_reason() -> None:
    """Довод — обязательный параметр фабрики: забыть его нельзя."""
    with pytest.raises(TypeError):
        dr._file_read_source_spec("knowledge/doctrine/X.md")  # type: ignore[call-arg]
