"""Three minor defects found in the 16.11 smart-memory check; each test fails on the code before the fix."""
from __future__ import annotations

from core.smart_memory import (
    EpisodeRecord,
    ProcedureRecord,
    _smoothed_confidence,
)

_ROW = {"goal": "g", "question": "q", "outcome": "success", "summary": "s"}


def test_nan_confidence_falls_back_to_the_counters() -> None:
    p = ProcedureRecord.from_dict({"name": "x", "confidence": float("nan"),
                                   "success_count": 1, "failure_count": 3})
    assert p.confidence == _smoothed_confidence(1, 3)


# 12 -- a flag stored as text is read by its meaning, not by being non-empty
def test_text_false_is_not_eligible() -> None:
    assert EpisodeRecord.from_dict({**_ROW, "usage_eligible": "false"}).usage_eligible is not True
    assert EpisodeRecord.from_dict({**_ROW, "usage_eligible": "true"}).usage_eligible is True
    assert EpisodeRecord.from_dict({**_ROW, "usage_eligible": False}).usage_eligible is False


def test_text_false_replan_is_false() -> None:
    assert EpisodeRecord.from_dict({**_ROW, "replan_exhausted": "false"}).replan_exhausted is False
    assert EpisodeRecord.from_dict({**_ROW, "replan_exhausted": "true"}).replan_exhausted is True
