"""Рефлексия училась только на падениях и не видела собственных детекторов.

Background: docs/CODE_NOTES.md, "Reflection was blind to its own detectors".
"""
from __future__ import annotations

from core.reflection import ReflectionConfig, ReflectionEngine


def _extract(events: list[dict], *, min_occurrences: int = 2):
    engine = ReflectionEngine.__new__(ReflectionEngine)
    return ReflectionEngine._extract_patterns(
        engine, events, ReflectionConfig(min_occurrences=min_occurrences),
    )


def _observation(signal: str, trace: str = "trace_1") -> dict:
    return {
        "event": "causal_observation",
        "trace_id": trace,
        "payload": {
            "defect_signals": [signal],
            "observed_mismatch": f"детекторы {signal} при завершении unknown",
        },
    }


def test_a_repeated_defect_signal_becomes_a_pattern():
    """Живой замер 2026-08-15: 1318 событий просмотрено, 0 закономерностей — а
    `reasoning_action_mismatch` к тому часу сработал девять раз. Ничего не
    падало, и учиться было якобы не на чем.
    """
    patterns = _extract([_observation("reasoning_action_mismatch") for _ in range(9)])

    assert [p.tool_name for p in patterns] == ["reasoning_action_mismatch"]
    assert patterns[0].count == 9


def test_each_signal_is_counted_separately():
    """Один ход поднимает несколько сигналов. «mismatch девять раз» — не то же
    самое, что «девять ходов с каким-нибудь дефектом», и слипшееся ведро
    сделало бы урок ни о чём.
    """
    events = [{
        "event": "causal_observation",
        "trace_id": "trace_1",
        "payload": {"defect_signals": ["reasoning_action_mismatch", "citation_fabricated"]},
    } for _ in range(3)]

    counts = {p.tool_name: p.count for p in _extract(events)}

    assert counts == {"reasoning_action_mismatch": 3, "citation_fabricated": 3}


def test_a_single_occurrence_is_not_a_pattern():
    """Порог повторов не тронут: один случай — наблюдение, а не закономерность
    (та же граница, что у падений)."""
    assert _extract([_observation("reasoning_action_mismatch")]) == []


def test_failures_are_still_counted():
    """Улов не отдан: рефлексия по-прежнему учится на падениях инструментов."""
    events = [{
        "event": "tool_result",
        "trace_id": "trace_1",
        "payload": {"status": "error", "tool_call_id": "tc_1", "error": "boom"},
    } for _ in range(2)]
    events.insert(0, {
        "event": "tool_call",
        "trace_id": "trace_1",
        "payload": {"id": "tc_1", "tool_name": "file_read"},
    })

    patterns = _extract(events)

    assert [(p.event_type, p.tool_name, p.count) for p in patterns] == [
        ("tool_result_error", "file_read", 2)
    ]


def test_an_observation_without_signals_adds_nothing():
    """Запись без сигналов — не дефект. Пустое ведро завело бы закономерность
    «что-то произошло», на которой нечему учиться.
    """
    events = [{
        "event": "causal_observation", "trace_id": "trace_1",
        "payload": {"defect_signals": []},
    } for _ in range(5)]

    assert _extract(events) == []


def test_the_traces_are_kept_so_a_lesson_can_be_checked():
    """Урок без адреса непроверяем: закономерность обязана назвать прогоны, на
    которых её увидели.
    """
    events = [
        _observation("citation_fabricated", "trace_a"),
        _observation("citation_fabricated", "trace_b"),
        _observation("citation_fabricated", "trace_a"),
    ]

    pattern = _extract(events)[0]

    assert sorted(pattern.trace_ids) == ["trace_a", "trace_b"]
