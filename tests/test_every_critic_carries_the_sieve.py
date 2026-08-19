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

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

#: Modules whose job is to judge model-generated Python before a human sees it.
_CRITIC_MODULES = (
    "core/self_task_producer.py",   # Stage A: the generated acceptance test
    "core/self_task_builder.py",    # Stage B: the generated implementation
    "core/self_build_producer.py",  # self-build: module splits and rewrites
)

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


def test_every_critic_module_runs_the_sieve() -> None:
    unarmed = [
        m for m in _CRITIC_MODULES
        if "attribute_sieve" not in (_REPO / m).read_text(encoding="utf-8")
    ]
    assert not unarmed, (
        "these critics judge generated Python without the attribute sieve, so "
        "an invented field ships with veto_reasons=[]: " + ", ".join(unarmed)
    )


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


def test_the_sieve_itself_was_never_the_problem() -> None:
    """Recorded so the post-mortem stays honest: the sieve named the phantom
    all along; only the wiring was missing."""
    from core.attribute_sieve import phantom_attribute_reason

    reason = phantom_attribute_reason(_THIRD_PHANTOM)
    assert reason is not None and "CausalClaim" in reason


def test_no_new_critic_escapes_the_registry() -> None:
    """A fourth critic must not appear unnoticed: any module defining a
    _critic_review that judges generated content belongs in the list above."""
    found = {
        p.relative_to(_REPO).as_posix()
        for p in _REPO.glob("core/*.py")
        if re.search(r"^def _\w*critic_review\(", p.read_text(encoding="utf-8"), re.MULTILINE)
    }
    assert found <= set(_CRITIC_MODULES), (
        "a critic exists that this guard does not know about: "
        + ", ".join(sorted(found - set(_CRITIC_MODULES)))
    )
