"""A rule proven somewhere is not a rule proven everywhere.

Background: docs/CODE_NOTES.md, "A lesson carries the scope it was proven in".
"""
from __future__ import annotations

from core.causal_lesson import (
    CausalClaim,
    Explanation,
    GeneralizationTest,
    Intervention,
    Observation,
    applies_to,
    blocking_reason,
    claim_tags,
    is_lesson,
    state_of,
)


def _claim(*, scope: str = "trimming a stale record against fresher code") -> CausalClaim:
    return CausalClaim(
        observation=Observation(
            episode_id="ep_1", trace_id="tr_1", run_id="run_1",
            defect_signals=("stale_memory_outlived_code",),
            evidence_refs=("file:core/loop.py",),
            observed_mismatch="a fixed bug was reported as current",
        ),
        explanations=(
            Explanation(statement="memory outranked the file", author="agent",
                        predicts="the block reaches the model at zero"),
            Explanation(statement="the file was never read", author="agent",
                        refuted_by="the read is in the trace"),
        ),
        chosen="memory outranked the file",
        violated_invariant="fresh evidence outranks recollection",
        intervention=Intervention(
            mutated="floored the demoted block at one whole record",
            predicted="memory survives with a citable id",
            observed="822 -> 393, one id kept",
        ),
        generalized_rule="a demoted block is spent before fresh evidence",
        scope=scope,
        generalization=GeneralizationTest(
            case_ref="ep_2", origin_ref="ep_1", held=True,
        ),
    )


def test_a_rule_without_a_stated_scope_is_not_a_lesson():
    """Measured 2026-08-15: `CausalClaim.scope` was declared and read by nobody.

    The live consequence is in this repository. «Memory pays first» was derived
    from ONE incident — a stale "Bug fixed…" record outliving the code
    disproving it — and applied to every turn, including «что ты помнишь», where
    it zeroed the block and taught the agent it has no past. The rule was right
    in the case that produced it and wrong outside it, and nothing was obliged
    to notice, because the scope it was proven in was never written down.
    """
    unscoped = _claim(scope="")

    assert not is_lesson(unscoped)
    # GENERALIZED, не ATTRIBUTED: ступень стала достижимой в тот же день,
    # когда область стала последним условием. Утверждение теста — «без
    # области это не урок» — не изменилось, уточнилась ступень.
    assert state_of(unscoped) == "GENERALIZED"
    assert "область" in blocking_reason(unscoped)
    assert "lesson" not in claim_tags(unscoped)


def test_a_scoped_rule_that_held_is_a_lesson():
    """The guard against over-correcting: scope is a requirement, not a wall."""
    scoped = _claim()

    assert is_lesson(scoped)
    assert "lesson" in claim_tags(scoped)


def test_inside_its_scope_the_lesson_applies():
    """The cases it was demonstrated on are exactly where it is proven."""
    scoped = _claim()

    assert applies_to(scoped, case_ref="ep_1")
    assert applies_to(scoped, case_ref="ep_2")


def test_outside_its_scope_it_is_extrapolation_not_proof():
    """A third case is not covered by two demonstrations.

    This is the whole point: applying a lesson beyond where it held must be a
    decision the caller makes knowingly, not a silent inheritance of authority.
    """
    scoped = _claim()

    assert not applies_to(scoped, case_ref="ep_3")


def test_a_refuted_claim_applies_nowhere():
    """Refutation outranks scope; a dead rule has no область at all."""
    dead = CausalClaim(
        observation=_claim().observation,
        scope="anything",
        refuted_reason="the intervention did not reproduce",
    )
    assert not applies_to(dead, case_ref="ep_1")
