"""Four medium defects found in the 16.11 smart-memory check; each test fails on the code before the fix."""
from __future__ import annotations

from dataclasses import replace

from core.smart_memory import (
    EpisodeRecord,
    ProcedureRecord,
    _smoothed_confidence,
    resolve_used_procedures,
)
from core.smart_memory_helpers import _procedure_status_for


# 7 -- one win and one loss is a tie, not a verdict: the procedure stays offerable
def test_a_tie_does_not_retire_a_procedure() -> None:
    assert _procedure_status_for(1, _smoothed_confidence(1, 1), failure_count=1) == "candidate"


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


def test_more_losses_than_wins_still_retires() -> None:
    assert _procedure_status_for(0, _smoothed_confidence(0, 1), failure_count=1) == "needs_review"
    assert _procedure_status_for(1, _smoothed_confidence(1, 2), failure_count=2) == "needs_review"


# 8 -- a workflow counts as applied only when its steps ran as often as it names them
def _proc(key: str, pid: str = "P") -> ProcedureRecord:
    return ProcedureRecord(name="p", workflow_key=key, trigger_tags=(), steps=(), id=pid)


def test_multi_round_order_still_counts() -> None:
    # `episode_tools` puts the last round's plan first, so a key may list tools
    # in an order the execution log never shows; that must not block attribution.
    sel = [_proc("tools:grep->edit->file_read")]
    assert resolve_used_procedures(selected=sel, executed_tools=["file_read", "grep", "edit"]) == ("P",)


def test_workflow_needs_its_repeats() -> None:
    sel = [_proc("tools:web_search->web_search")]
    assert resolve_used_procedures(selected=sel, executed_tools=["web_search"]) == ()
    assert resolve_used_procedures(selected=sel, executed_tools=["web_search", "web_search"]) == ("P",)


# 9 -- a legacy row without id or date reads the same every time, and as old, not fresh
_ROW = {"goal": "g", "question": "q", "outcome": "success", "summary": "s"}


def test_legacy_episode_id_is_stable() -> None:
    assert EpisodeRecord.from_dict(dict(_ROW)).id == EpisodeRecord.from_dict(dict(_ROW)).id
    other = EpisodeRecord.from_dict({**_ROW, "question": "другой"}).id
    assert other != EpisodeRecord.from_dict(dict(_ROW)).id


def test_legacy_procedure_id_is_stable() -> None:
    row = {"name": "x", "workflow_key": "tools:file_read"}
    assert ProcedureRecord.from_dict(dict(row)).id == ProcedureRecord.from_dict(dict(row)).id


def test_legacy_episode_without_date_reads_as_old() -> None:
    ep = EpisodeRecord.from_dict(dict(_ROW))
    assert ep.created_at < "2000"


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
