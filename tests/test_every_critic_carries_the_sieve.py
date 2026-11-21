"""The attribute sieve guards EVERY producer critic, not two of three.

Measured 2026-08-19 (meas_df15479ba17a, the third recurrence): Stage B
generated `getattr(claim, "state", None) == "LESSON"` for the third time —
now triple-wrapped in .value/.name fallbacks that silently return an empty
list on real data — and shipped a proposal with veto_reasons=[]. The sieve
itself was innocent: called directly on that content it names the phantom.
The hole was mine: I armed core/self_task_producer.py (Stage A tests) and
core/self_build_producer.py (module splits) and left
core/self_task_builder.py (Stage B implementations) unarmed — coverage
copied from the neighbour, holes in the same detail.

This test pins the invariant at the level where it cannot rot again: every
critic that judges generated Python must run the sieve.
"""
from __future__ import annotations

# Verbatim from the third denied generation (ain_3d5f1efa), condensed to the
# decisive lines: the type of `claim` is reachable only through load_claims'
# return annotation, and the phantom hides behind getattr fallbacks.
_THIRD_PHANTOM = '''
from core.causal_claim_store import load_claims


def pick(workspace):
    claims = load_claims(workspace)
    return [
        extra["key"]
        for claim, extra in claims
        if (
            getattr(claim, "state", None) == "LESSON"
            or getattr(getattr(claim, "state", None), "value", None) == "LESSON"
            or getattr(getattr(claim, "state", None), "name", None) == "LESSON"
        )
    ]
'''


def test_the_stage_b_critic_vetoes_the_third_phantom() -> None:
    """The behavioural half: the very generation that got through must not."""
    from core.self_task_builder import _impl_critic_review

    out = _impl_critic_review(
        {"content": _THIRD_PHANTOM, "confidence": 0.9},
        impl_path="tools/lesson_provenance_tool.py",
        current_content="def old():\n    return 1\n",
        confidence_threshold=0.6,
    )
    assert out.decision == "veto"
    reasons = list(out.data.get("veto_reasons", []))
    assert any("state" in r and "CausalClaim" in r for r in reasons), reasons


def test_a_clean_implementation_still_passes() -> None:
    """The boundary: the honest form (state_of) must not be vetoed."""
    from core.self_task_builder import _impl_critic_review

    clean = (
        "from core.causal_claim_store import load_claims\n"
        "from core.causal_lesson import state_of\n"
        "\n\ndef pick(workspace):\n"
        "    return [\n"
        "        extra['key']\n"
        "        for claim, extra in load_claims(workspace)\n"
        "        if state_of(claim) == 'LESSON'\n"
        "    ]\n"
    )
    out = _impl_critic_review(
        {"content": clean, "confidence": 0.9},
        impl_path="tools/lesson_provenance_tool.py",
        current_content="def old():\n    return 1\n",
        confidence_threshold=0.6,
    )
    assert out.decision == "pass", out.data.get("veto_reasons")
