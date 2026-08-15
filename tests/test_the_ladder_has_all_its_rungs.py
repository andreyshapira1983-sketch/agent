"""A rung that is declared, numbered and unreachable is not a rung.

Background: docs/CODE_NOTES.md, "The rung nobody could stand on".
"""
from __future__ import annotations

from core.causal_lesson import (
    CausalClaim,
    Explanation,
    GeneralizationTest,
    Intervention,
    Observation,
    blocking_reason,
    state_of,
)

_OBS = Observation(
    episode_id="ep_1", trace_id="tr_1", run_id="run_1",
    defect_signals=("stale_memory_outlived_code",),
    evidence_refs=("file:core/loop.py",),
    observed_mismatch="a fixed bug was reported as current",
)
_EXPL = (
    Explanation(statement="memory outranked the file", author="agent",
                predicts="the block reaches the model at zero"),
    Explanation(statement="the file was never read", author="agent",
                refuted_by="the read is in the trace"),
)
_INTERV = Intervention(
    mutated="floored the demoted block at one whole record",
    predicted="memory survives with a citable id",
    observed="822 -> 393, one id kept",
)


def _claim(**over) -> CausalClaim:
    base = {
        "observation": _OBS,
        "explanations": _EXPL,
        "chosen": "memory outranked the file",
        "violated_invariant": "fresh evidence outranks recollection",
        "intervention": _INTERV,
        "generalized_rule": "a demoted block is spent before fresh evidence",
        "scope": "trimming a stale record against fresher code",
        "generalization": GeneralizationTest(
            case_ref="ep_2", origin_ref="ep_1", held=True,
        ),
    }
    base.update(over)
    return CausalClaim(**base)


def test_a_rule_that_held_elsewhere_but_names_no_scope_is_generalized():
    """The rung the module documents and the machine never produced.

    `GENERALIZED` is defined in the header as «правило проверено на СЛУЧАЕ,
    отличном от исходного» and listed in `CausalState` and `_ORDER` — and
    `state_of` returned it zero times, because the scope check sat BEFORE the
    generalization check and collapsed everything into ATTRIBUTED.

    The order matters beyond tidiness: a scope can only be stated honestly
    AFTER the rule has held somewhere else. Demanding it first asks the author
    to guess the область before seeing it.
    """
    assert state_of(_claim(scope="")) == "GENERALIZED"
    assert "область" in blocking_reason(_claim(scope=""))


def test_the_rungs_below_are_unchanged():
    """Each rung still removes exactly its own way of being wrong."""
    assert state_of(_claim(explanations=(), chosen="")) == "OBSERVED"
    assert state_of(_claim(violated_invariant="")) == "EXPLAINED"
    assert state_of(_claim(intervention=None)) == "EXPLAINED"
    assert state_of(_claim(generalized_rule="")) == "ATTRIBUTED"
    assert state_of(_claim(generalization=None)) == "ATTRIBUTED"


def test_a_generalization_on_the_origin_case_does_not_climb():
    """Self-confirmation is the one thing this rung exists to refuse."""
    same = GeneralizationTest(case_ref="ep_1", origin_ref="ep_1", held=True)
    assert state_of(_claim(generalization=same)) == "ATTRIBUTED"


def test_the_top_is_still_the_top():
    """Scope plus an independent hold is a lesson, as before."""
    assert state_of(_claim()) == "LESSON"
