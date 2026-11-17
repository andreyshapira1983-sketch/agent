"""Two plausible defects named by the 16.11 reviewer; each test fails on the code before the fix."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from core.smart_memory import EpisodicMemoryStore, ProceduralMemoryStore, episode_from_agent_cycle
from core.state_integrity import append_state_jsonl_unlocked, read_state_jsonl_unlocked

QUESTION = "как починить файл core/foo.py"


def _ep(**kw):
    base = {"goal": "g", "question": QUESTION, "answer": "ответ", "tools_used": ["file_read", "file_write"],
            "source_labels": ["file:core/foo.py"], "verified_chunks": 1, "declared_completion": "achieved"}
    base.update(kw)
    return episode_from_agent_cycle(**base)


# A -- an unreadable row of the SAME workflow must not be shadowed by a fresh duplicate
def test_no_duplicate_beside_an_unreadable_twin(tmp_path: Path) -> None:
    path = tmp_path / "procs.jsonl"
    append_state_jsonl_unlocked(path, [{"id": "broken", "name": "x", "success_count": "много",
                                        "workflow_key": "tools:file_read->file_write"}])
    store = ProceduralMemoryStore(path)
    for i in range(3):
        store.upsert_from_episode(_ep(run_id=f"r{i}"))
    rows = read_state_jsonl_unlocked(path)
    same = [r for r in rows if r.get("workflow_key") == "tools:file_read->file_write"]
    assert len(same) == 1, f"duplicates created: {[r.get('id') for r in same]}"


# B -- one episode the gate cannot judge must not abort the whole search
def test_a_failing_gate_drops_one_record_not_the_search(tmp_path: Path) -> None:
    store = EpisodicMemoryStore(tmp_path / "ep.jsonl", max_episodes=0)
    store.save(replace(_ep(run_id="bad"), summary="BAD"))
    store.save(_ep(run_id="good"))

    def admit(ep):
        if ep.summary == "BAD":
            raise RuntimeError("cannot judge")

    found = store.search_with_report(QUESTION, limit=3, admit=admit)
    assert [e.id for e in found.episodes] == ["ep-run-good"]
    assert found.rejected_by.get("admit_error") == 1
