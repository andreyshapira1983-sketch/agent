import dataclasses

from core.causal_claim_store import save_claim
from core.causal_climb_action import (
    AWAITING_EXPERIMENT_MARK,
    discriminable_claims,
)
from core.causal_lesson import (
    CausalClaim,
    Explanation,
    Observation,
)


def _build_claim():
    return CausalClaim(
        observation=Observation(
            episode_id="ep1",
            trace_id="t1",
            run_id="r1",
            evidence_refs=("ev1",),
            observed_mismatch="mismatch",
            defect_signals=("sig_a",),
        ),
        explanations=(
            Explanation(
                statement="A",
                author="agent",
                predicts="X [probe: logs/a.log | needle | есть]",
                refuted_by="",
            ),
            Explanation(
                statement="B",
                author="agent",
                predicts="Y [probe: logs/b.log | needle | есть]",
                refuted_by="",
            ),
        ),
    )


def test_marked_claim_is_not_discriminable(tmp_path):
    claim = _build_claim()
    marked = dataclasses.replace(
        claim, notes=(*claim.notes, AWAITING_EXPERIMENT_MARK)
    )
    save_claim(marked, workspace=tmp_path)
    assert len(discriminable_claims(tmp_path)) == 0
