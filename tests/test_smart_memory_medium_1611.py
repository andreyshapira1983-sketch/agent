"""Four medium defects found in the 16.11 smart-memory check; each test fails on the code before the fix."""
from __future__ import annotations

from dataclasses import replace

from core.smart_memory import (
    EpisodeRecord,
    ProcedureRecord,
    resolve_used_procedures,
)


def test_repair_pass_frees_a_tie_retired_by_the_old_rule(tmp_path) -> None:
    from core.smart_memory import ProceduralMemoryStore
    from core.state_integrity import append_state_jsonl_unlocked, read_state_jsonl_unlocked

    path = tmp_path / "procs.jsonl"
    append_state_jsonl_unlocked(path, [{"name": "x", "workflow_key": "tools:file_read",
                                        "success_count": 1, "failure_count": 1,
                                        "confidence": 0.5, "status": "needs_review"}])
    legacy_id = ProcedureRecord.from_dict(read_state_jsonl_unlocked(path)[0]).id
    report = ProceduralMemoryStore(path).recompute_legacy_confidence(dry_run=False)
    row = read_state_jsonl_unlocked(path)[0]
    assert report["corrected"] == 1
    assert row["status"] == "candidate"
    assert row["id"] == legacy_id  # the id it was known by survives the repair


# 8 -- a workflow counts as applied only when its steps ran as often as it names them
def _proc(key: str, pid: str = "P") -> ProcedureRecord:
    return ProcedureRecord(name="p", workflow_key=key, trigger_tags=(), steps=(), id=pid)


def test_workflow_needs_its_repeats() -> None:
    sel = [_proc("tools:web_search->web_search")]
    assert resolve_used_procedures(selected=sel, executed_tools=["web_search"]) == ()
    assert resolve_used_procedures(selected=sel, executed_tools=["web_search", "web_search"]) == ("P",)


# 10 -- the provenance list of a procedure is bounded
def test_source_episode_ids_bounded() -> None:
    from core.smart_memory import _MAX_SOURCE_EPISODE_IDS

    p = ProcedureRecord(name="p", workflow_key="k", trigger_tags=(), steps=())
    ep = EpisodeRecord(goal="g", question="q", outcome="success", summary="s")
    for i in range(_MAX_SOURCE_EPISODE_IDS + 50):
        p = p.merged_from_episode(replace(ep, id=f"e{i}"))
    assert len(p.source_episode_ids) == _MAX_SOURCE_EPISODE_IDS
    assert p.source_episode_ids[-1] == f"e{_MAX_SOURCE_EPISODE_IDS + 49}"  # newest kept
    q = p.with_outcome(replace(ep, id="last"), "success")
    assert len(q.source_episode_ids) == _MAX_SOURCE_EPISODE_IDS
    loaded = ProcedureRecord.from_dict({"name": "x", "source_episode_ids": [f"x{i}" for i in range(999)]})
    assert len(loaded.source_episode_ids) == _MAX_SOURCE_EPISODE_IDS
