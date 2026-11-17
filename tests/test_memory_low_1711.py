"""Three low-severity defects from the 17.11 second check of memory; kept closed here."""
from __future__ import annotations

from pathlib import Path

from core.smart_memory import EpisodeRecord, EpisodicMemoryStore, decide_usage_eligibility
from core.state_integrity import read_state_jsonl_unlocked, rewrite_state_jsonl_unlocked


class _Log:
    def __init__(self) -> None:
        self.events: list = []

    def log(self, name, payload) -> None:
        self.events.append((name, payload))


# 1 -- identical legacy rows (one content-hash id) are collapsed by position
def test_collapse_removes_identical_legacy_rows(tmp_path: Path) -> None:
    from core.episodic_hygiene import collapse_duplicate_episodes

    path = tmp_path / "ep.jsonl"
    row = {"goal": "g", "question": "q", "outcome": "success", "summary": "same lesson"}
    rewrite_state_jsonl_unlocked(path, [dict(row) for _ in range(4)])
    dropped = collapse_duplicate_episodes(EpisodicMemoryStore(path, max_episodes=0))
    assert len(dropped) == 3
    assert len(read_state_jsonl_unlocked(path)) == 1


# 2 -- the fast path gets the best REPLAYABLE match, not a failed lesson
def test_fast_path_candidate_skips_unreplayable_lesson(tmp_path: Path) -> None:
    from core.loop import AgentLoop
    from core.loop_memory_read import AgentLoopMemoryRead

    class Host(AgentLoopMemoryRead):
        _fast_path_allows_replay = staticmethod(AgentLoop._fast_path_allows_replay)

        def __init__(self, store) -> None:
            self.log = _Log()
            self.episodic_store, self.procedural_store, self.persistent_store = store, None, None

    store = EpisodicMemoryStore(tmp_path / "ep.jsonl")
    q = "explain photosynthesis light reactions chlorophyll"
    store.save(EpisodeRecord(id="ep-lesson", goal="g", question=q, outcome="failed", summary="lesson",
                             tags=("lesson",), usage_eligible=True, completion_state="failed"))
    store.save(EpisodeRecord(id="ep-good", goal="g", question=q + " detail", outcome="success",
                             summary="good", full_answer="ANSWER", usage_eligible=True,
                             completion_state="achieved", verified_chunks=1))
    host = Host(store)
    host._retrieve_experience_memory(q)
    assert host._last_best_similar_episode.id == "ep-good"


# 3 -- a list field stored as a plain string is one item, not letters
def test_string_signal_is_one_item(tmp_path: Path) -> None:
    path = tmp_path / "e.jsonl"
    rewrite_state_jsonl_unlocked(path, [{"id": "e", "goal": "g", "question": "q", "outcome": "success",
                                          "summary": "s", "defect_signals": "self_contradiction",
                                          "verified_chunks": 1, "completion_state": "achieved"}])
    ep = EpisodicMemoryStore(path).load()[0]
    assert ep.defect_signals == ("self_contradiction",)
    assert not decide_usage_eligibility(ep)
