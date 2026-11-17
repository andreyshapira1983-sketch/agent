"""Three minor defects found in the 16.11 smart-memory check; each test fails on the code before the fix."""
from __future__ import annotations

from core.smart_memory import (
    EpisodeRecord,
    ProcedureRecord,
    _smoothed_confidence,
    admit_for_storage,
    episode_from_agent_cycle,
)

_ROW = {"goal": "g", "question": "q", "outcome": "success", "summary": "s"}


# 11 -- a stored confidence of 0.0 is a value, not a missing one; NaN is not certainty
def test_zero_confidence_is_kept() -> None:
    assert ProcedureRecord.from_dict({"name": "x", "confidence": 0.0}).confidence == 0.0


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


# 13 -- a relevance that is not a number in [0, 1] is "not measured", never a pass
def test_nan_relevance_on_write_is_not_measured() -> None:
    ep = episode_from_agent_cycle(goal="g", question="как починить core/foo.py", answer="a",
                                  tools_used=["file_read"], source_labels=["file:x"],
                                  verified_chunks=1, declared_completion="achieved",
                                  relevance_score=float("nan"))
    assert ep.relevance_score is None
    out_of_range = episode_from_agent_cycle(goal="g", question="q", answer="a", tools_used=[],
                                            source_labels=[], relevance_score=7.0)
    assert out_of_range.relevance_score is None
    assert admit_for_storage(ep).usage_eligible is True  # unmeasured is not a failure
