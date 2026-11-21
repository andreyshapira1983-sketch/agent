"""Three low-severity defects from the 17.11 second check of memory; kept closed here."""
from __future__ import annotations

from pathlib import Path

from core.smart_memory import EpisodicMemoryStore, decide_usage_eligibility
from core.state_integrity import rewrite_state_jsonl_unlocked


# 3 -- a list field stored as a plain string is one item, not letters
def test_string_signal_is_one_item(tmp_path: Path) -> None:
    path = tmp_path / "e.jsonl"
    rewrite_state_jsonl_unlocked(path, [{"id": "e", "goal": "g", "question": "q", "outcome": "success",
                                          "summary": "s", "defect_signals": "self_contradiction",
                                          "verified_chunks": 1, "completion_state": "achieved"}])
    ep = EpisodicMemoryStore(path).load()[0]
    assert ep.defect_signals == ("self_contradiction",)
    assert not decide_usage_eligibility(ep)
