"""MIR-048 — close the procedural feedback loop, using only recorded attribution.

`failure_count == 0` on all 65 live procedures: the ratchet has never once
turned. Demotion is unreachable, so a procedure can fail forever and stay
`active` at rising confidence.

The fix consumes MIR-049's `used_procedure_ids` and nothing else. There is
deliberately NO fallback — not workflow_key, not the tool set, not a similar
name, not a fresh retrieval, not inference from episode content. MIR-050
measured that keys pool unrelated goals, so any of those would debit a
procedure that had nothing to do with the run. An id that no longer resolves
is reported as `orphaned`, never substituted.

Outcome matrix:

    success                     success_count += 1
    partial                     neutral (MIR-057: evidence weakness
                                is not procedural failure)
    failed / exception          failure_count += 1
    cancelled                   nothing — a control signal is not evidence
                                that the procedure is bad
    replay (executed nothing)   nothing (its used_procedure_ids is empty)
    used_procedure_ids is None  nothing — legacy, no guessing
    used_procedure_ids == ()    nothing — known to have applied none

Idempotence is keyed on `episode_id` recorded in the procedure's own
`source_episode_ids` journal, not on "the counters look about right":
re-processing a queue must not turn the ratchet twice.

Status when written: all fail — `apply_episode_feedback` does not exist.
"""
from __future__ import annotations

from pathlib import Path

from core.smart_memory import (
    EpisodeRecord,
    ProceduralMemoryStore,
    ProcedureRecord,
    _smoothed_confidence,
)


def _proc(pid: str, *, success: int = 1, failure: int = 0, tools: str = "file_read") -> ProcedureRecord:
    return ProcedureRecord(
        name=f"Workflow {pid}", workflow_key=f"tools:{tools}",
        trigger_tags=(), steps=("Run tool",), id=pid,
        success_count=success, failure_count=failure,
        confidence=_smoothed_confidence(success, failure),
        source_episode_ids=(),
    )


_COMPLETION_FOR = {"success": "achieved", "partial": "partially_achieved",
                   "failed": "failed"}


def _episode(
    *, outcome: str = "success", used: tuple[str, ...] | None = ("p",),
    eid: str = "ep-1", tags: tuple[str, ...] = (),
    completion: str | None = None, replan: bool = False,
) -> EpisodeRecord:
    # Feedback reads BOTH axes since MIR-057. The default mirrors `outcome`
    # onto the completion axis so each case still says one thing; a debit
    # additionally needs `replan_exhausted`, which is what makes the failure
    # something the RUN demonstrated rather than something the answer claimed.
    return EpisodeRecord(
        goal="g", question="q", outcome=outcome,  # type: ignore[arg-type]
        summary="s", tools_used=("file_read",), id=eid,
        completion_state=(completion or _COMPLETION_FOR.get(outcome)),  # type: ignore[arg-type]
        replan_exhausted=replan,
        used_procedure_ids=used, tags=tags,
        # Credit needs one verified fact since 2026-11-16 (self-praise gate).
        verified_chunks=1,
    )


def _store(tmp_path: Path, procs: list[ProcedureRecord]) -> ProceduralMemoryStore:
    store = ProceduralMemoryStore(tmp_path / "procedural_memory.jsonl")
    store.rewrite(procs)
    return store


def _by_id(store: ProceduralMemoryStore) -> dict[str, ProcedureRecord]:
    return {p.id: p for p in store.load()}


# ==========================================================================
# No fallback, ever.
# ==========================================================================
def test_unknown_id_is_orphaned_not_substituted(tmp_path: Path) -> None:
    """A dangling id must not be re-pointed at a procedure with the same shape."""
    store = _store(tmp_path, [_proc("p", tools="file_read")])
    before = _by_id(store)["p"]

    report = store.apply_episode_feedback(
        _episode(outcome="failed", replan=True, used=("proc-deleted",))
    )

    assert report["orphaned"] == 1
    assert report["applied"] == 0
    assert _by_id(store)["p"] == before, (
        "the surviving procedure shares the workflow but was not attributed"
    )


# ==========================================================================
# Idempotence — a re-processed queue must not turn the ratchet twice.
# ==========================================================================
def test_reapplying_the_same_episode_is_a_no_op(tmp_path: Path) -> None:
    store = _store(tmp_path, [_proc("p")])

    first = store.apply_episode_feedback(_episode(outcome="failed", replan=True, used=("p",), eid="ep-42"))
    after_first = _by_id(store)["p"]
    second = store.apply_episode_feedback(_episode(outcome="failed", replan=True, used=("p",), eid="ep-42"))

    assert first["applied"] == 1
    assert second["already_applied"] == 1
    assert second["applied"] == 0
    assert _by_id(store)["p"] == after_first


def test_one_orphan_does_not_silence_the_rest(tmp_path: Path) -> None:
    """A partly-unresolvable attribution must still apply what it can, visibly."""
    store = _store(tmp_path, [_proc("real")])

    report = store.apply_episode_feedback(
        _episode(outcome="failed", replan=True, used=("real", "ghost"))
    )

    assert report["applied"] == 1
    assert report["orphaned"] == 1
    assert _by_id(store)["real"].failure_count == 1
