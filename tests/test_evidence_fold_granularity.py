"""One bad item must skip itself, in all three folds — not just the first one.

WHO NEEDS THIS FILE. `_fold_evidence_chain` folds three sources into the
provenance chain, each with a guard around the conversion of ONE item:
persistent records, working-memory artifacts, and recent dialogue turns. The
comment beside the first records what a loop-level guard cost when it was there
— 1 of 5 records arriving, and a verifier judging the answer against a truncated
chain — and warns that the loops must not drift apart.

The persistent fold is behaviourally guarded, by
`tests/test_evidence_integration.py::TestMalformedMemoryRecordDoesNotTruncateChain`.
The other two were not. Measured 2026-08-09: with both of their guards changed
to abandon the loop on the first failure, the whole suite reported one failure —
`test_loop_evidence_chain_split.py::test_logic_moved_symbol_for_symbol`, an AST
body-equivalence ratchet from the module split. That guard fires on ANY edit to
the module. It cannot tell a deleted safety property from a renamed local, and
this project has already ruled once that such a guard is not behavioural
protection. Behaviourally, 7302 tests passed with both folds truncating.

WHY A SHIM RATHER THAN A RUN. The property is about one method's internal loop
structure, and driving it through a full `run()` would require arranging a
malformed artifact and a failing turn conversion to survive every phase before
the fold. The sibling test above does use a real run, and covers the same
property for the fold that needs the agent's own store; these two need only the
method and the objects it reads. The trade is stated rather than hidden: this
file would not notice if `_fold_evidence_chain` stopped being CALLED. The
`evidence_collected` assertions elsewhere would.
"""
from __future__ import annotations

from core import loop_evidence_chain
from core.evidence import ProvenanceChain
from core.loop_evidence_chain import AgentLoopEvidenceChain


class _Turn:
    def __init__(self, turn_id: str, index: int) -> None:
        self.id = turn_id
        self.index = index
        self.question = f"q{index}"
        self.answer = f"a{index}"


class _Memory:
    def __init__(self, artifacts: dict, turns: list) -> None:
        self.artifacts = artifacts
        self._turns = turns

    def recent_turns(self, _count: int) -> list:
        return self._turns


class _Log:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def log(self, event: str, payload: dict | None = None, **_kw: object) -> None:
        self.events.append((event, payload or {}))


class _Shim:
    """Exactly the attributes `_fold_evidence_chain` reads, and nothing else."""

    persistent_store = None
    last_self_analysis = None

    def __init__(self, memory: _Memory) -> None:
        self._last_persistent_records: list = []
        self.memory = memory
        self.log = _Log()
        self.last_provenance = None

    def _sensor_failed(self, sensor: str, _exc: BaseException) -> None:
        self.log.log("sensor_failed", {"sensor": sensor})


def _fold(memory: _Memory) -> tuple[ProvenanceChain, _Shim]:
    shim = _Shim(memory)
    chain = ProvenanceChain()
    AgentLoopEvidenceChain._fold_evidence_chain(shim, chain, persistent_block="")
    return chain, shim


#: `turn_index` is cast with `int()`, so a non-numeric one raises inside the
#: guarded body — a malformed artifact, not a contrived exception.
_BAD_ARTIFACT = {"label": "bad", "turn_index": "not-an-int", "output": "x"}
_GOOD_ARTIFACT = {"label": "ok", "turn_index": 1, "output": "usable"}


def test_a_malformed_working_memory_artifact_only_skips_itself() -> None:
    chain, shim = _fold(_Memory({"a": _BAD_ARTIFACT, "b": _GOOD_ARTIFACT}, []))
    assert [name for name, _ in shim.log.events].count("sensor_failed") == 1, (
        "precondition: the bad artifact must actually have failed"
    )
    assert len(chain) == 1, (
        "the good artifact vanished with the bad one — the guard is around the "
        "loop, not around the item"
    )


def test_a_failing_turn_conversion_only_skips_that_turn(monkeypatch) -> None:
    original = loop_evidence_chain.evidence_from_prior_turn

    def _fails_on_the_first(*, turn_id: str, turn_index: int, question: str,
                            answer: str):
        if turn_id == "t1":
            raise ValueError("bad turn")
        return original(turn_id=turn_id, turn_index=turn_index,
                        question=question, answer=answer)

    monkeypatch.setattr(loop_evidence_chain, "evidence_from_prior_turn",
                        _fails_on_the_first)
    chain, shim = _fold(_Memory({}, [_Turn("t1", 1), _Turn("t2", 2)]))

    names = [name for name, _ in shim.log.events]
    assert names.count("dialogue_evidence_skipped") == 1, (
        "precondition: the first turn must actually have failed"
    )
    assert len(chain) == 1, (
        "the second turn vanished with the first — the guard is around the loop"
    )


def test_the_admitted_count_reports_what_actually_landed(monkeypatch) -> None:
    """A count that included the skipped turn would be a second, quieter defect."""
    original = loop_evidence_chain.evidence_from_prior_turn

    def _fails_on_the_first(*, turn_id: str, turn_index: int, question: str,
                            answer: str):
        if turn_id == "t1":
            raise ValueError("bad turn")
        return original(turn_id=turn_id, turn_index=turn_index,
                        question=question, answer=answer)

    monkeypatch.setattr(loop_evidence_chain, "evidence_from_prior_turn",
                        _fails_on_the_first)
    _chain, shim = _fold(_Memory({}, [_Turn("t1", 1), _Turn("t2", 2)]))

    admitted = [p for name, p in shim.log.events
                if name == "dialogue_evidence_admitted"]
    assert admitted and admitted[0]["turns"] == 1


def test_all_three_items_land_when_nothing_fails() -> None:
    """GUARD: the assertions above must not be satisfied by an empty fold."""
    chain, _shim = _fold(
        _Memory({"b": _GOOD_ARTIFACT}, [_Turn("t1", 1), _Turn("t2", 2)])
    )
    assert len(chain) == 3
