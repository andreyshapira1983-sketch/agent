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
