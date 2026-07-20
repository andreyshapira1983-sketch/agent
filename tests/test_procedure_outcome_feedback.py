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

import pytest

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
    )


def _store(tmp_path: Path, procs: list[ProcedureRecord]) -> ProceduralMemoryStore:
    store = ProceduralMemoryStore(tmp_path / "procedural_memory.jsonl")
    store.rewrite(procs)
    return store


def _by_id(store: ProceduralMemoryStore) -> dict[str, ProcedureRecord]:
    return {p.id: p for p in store.load()}


# ==========================================================================
# THE central test — the ratchet closes without reintroducing MIR-050.
# ==========================================================================
def test_failure_debits_only_the_attributed_procedure(tmp_path: Path) -> None:
    """Continues the MIR-049 scenario through to the counter update.

    Two procedures share a workflow_key; only one was attributed. The failure
    must land on that one alone, and the other must come out byte-identical —
    proof the loop closed without routing back through the shared shape.
    """
    store = _store(tmp_path, [_proc("proc-selected"), _proc("proc-other")])
    before_other = _by_id(store)["proc-other"]

    report = store.apply_episode_feedback(
        _episode(outcome="failed", replan=True, used=("proc-selected",))
    )

    after = _by_id(store)
    assert report["applied"] == 1
    assert after["proc-selected"].failure_count == 1, "the attributed one is debited"
    assert after["proc-other"] == before_other, (
        "a procedure sharing the workflow_key but not the attribution must be "
        "untouched — no field, no counter, no confidence"
    )


# ==========================================================================
# Outcome matrix.
# ==========================================================================
@pytest.mark.parametrize(
    "outcome,replan,d_success,d_failure",
    [
        ("success", False, 1, 0),
        # POLICY CHANGE (MIR-057): an evidence-`partial` no longer debits.
        # `partial` says unverified support outnumbered verified support —
        # a fact about how well the ANSWER was grounded, not proof that the
        # workflow failed. It used to cost the procedure a failure.
        ("partial", False, 0, 0),
        # A `failed` the answer merely declared is likewise neutral: the
        # cause is usually upstream of the procedure.
        ("failed", False, 0, 0),
        # A failure the RUN demonstrated still debits, exactly as MIR-048
        # intended.
        ("failed", True, 0, 1),
    ],
)
def test_outcome_matrix(
    tmp_path: Path, outcome: str, replan: bool, d_success: int, d_failure: int
) -> None:
    store = _store(tmp_path, [_proc("p", success=2, failure=1)])

    store.apply_episode_feedback(_episode(outcome=outcome, used=("p",), replan=replan))

    got = _by_id(store)["p"]
    assert (got.success_count, got.failure_count) == (2 + d_success, 1 + d_failure)


def test_cancellation_is_not_negative_evidence(tmp_path: Path) -> None:
    """A control signal is not proof the procedure is bad."""
    store = _store(tmp_path, [_proc("p")])
    before = _by_id(store)["p"]

    report = store.apply_episode_feedback(
        _episode(outcome="failed", completion="cancelled", used=("p",),
                 tags=("aborted", "aborted:cancelled"))
    )

    assert report["skipped"] == 1
    assert _by_id(store)["p"] == before


@pytest.mark.parametrize("used", [None, ()])
def test_no_attribution_means_no_feedback(tmp_path: Path, used) -> None:
    """`None` is legacy-unknown, `()` is known-nothing. Neither invents a target."""
    store = _store(tmp_path, [_proc("p")])
    before = _by_id(store)["p"]

    report = store.apply_episode_feedback(_episode(outcome="failed", replan=True, used=used))

    assert report["applied"] == 0
    assert _by_id(store)["p"] == before


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


def test_a_different_episode_still_counts(tmp_path: Path) -> None:
    store = _store(tmp_path, [_proc("p")])

    store.apply_episode_feedback(_episode(outcome="failed", replan=True, used=("p",), eid="ep-1"))
    store.apply_episode_feedback(_episode(outcome="failed", replan=True, used=("p",), eid="ep-2"))

    assert _by_id(store)["p"].failure_count == 2


# ==========================================================================
# Consistency and multiplicity.
# ==========================================================================
def test_counters_confidence_and_status_move_together(tmp_path: Path) -> None:
    """No intermediate state where a counter moved but confidence did not."""
    store = _store(tmp_path, [_proc("p", success=1, failure=0)])

    store.apply_episode_feedback(_episode(outcome="failed", replan=True, used=("p",)))

    got = _by_id(store)["p"]
    assert got.confidence == _smoothed_confidence(got.success_count, got.failure_count)
    assert got.status == ("active" if got.confidence >= 0.6 else "needs_review")


def test_demotion_is_now_reachable(tmp_path: Path) -> None:
    """The whole point: a procedure that keeps failing must be able to fall."""
    store = _store(tmp_path, [_proc("p", success=1, failure=0)])
    assert _by_id(store)["p"].status == "active"

    for i in range(4):
        store.apply_episode_feedback(_episode(outcome="failed", replan=True, used=("p",), eid=f"ep-{i}"))

    got = _by_id(store)["p"]
    assert got.failure_count == 4
    assert got.status == "needs_review", (
        f"confidence {got.confidence} — demotion must be reachable through outcomes"
    )


def test_two_used_procedures_each_get_one_observation(tmp_path: Path) -> None:
    store = _store(tmp_path, [_proc("a"), _proc("b")])

    store.apply_episode_feedback(_episode(outcome="failed", replan=True, used=("a", "b")))

    got = _by_id(store)
    assert (got["a"].failure_count, got["b"].failure_count) == (1, 1), (
        "one run is one discrete observation per procedure, not a split share"
    )


def test_duplicate_ids_are_collapsed_before_updating(tmp_path: Path) -> None:
    store = _store(tmp_path, [_proc("p")])

    store.apply_episode_feedback(_episode(outcome="failed", replan=True, used=("p", "p", "p")))

    assert _by_id(store)["p"].failure_count == 1


def test_one_orphan_does_not_silence_the_rest(tmp_path: Path) -> None:
    """A partly-unresolvable attribution must still apply what it can, visibly."""
    store = _store(tmp_path, [_proc("real")])

    report = store.apply_episode_feedback(
        _episode(outcome="failed", replan=True, used=("real", "ghost"))
    )

    assert report["applied"] == 1
    assert report["orphaned"] == 1
    assert _by_id(store)["real"].failure_count == 1
