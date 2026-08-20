"""Banked: `evidence_verified` measures that pytest finished, not that a
diagnosis was verified.

Measured 2026-08-20, tracing the governance chain end to end after a mutation
audit. `core/self_repair.py` computes the input to its own safety gate as
`_diagnosis_verified(baseline.output)`, and that function is:

    return isinstance(output, dict) and output.get("timed_out") is False

So the three worlds resolve like this:

    baseline GREEN  (no defect at all)     -> verified=True  -> diff ALLOWED
    baseline RED    (defect reproduced)    -> verified=True  -> diff ALLOWED
    baseline TIMED OUT                     -> verified=False -> diff escalates

The gate named "verified diagnosis" separates exactly one thing: whether the
test command came back. It cannot tell a reproduced defect from no defect,
which is the distinction the name promises and the distinction the governance
rule spends its authority on — an unverified diagnosis is DENIED an
APPLY_CODE_CHANGE, and allowed to PROPOSE_DIFF only once verified.

Nothing here says the agent repairs healthy code today; other gates (empty
diff, confidence floor) stop most of that. It says the word "verified" in the
safety decision is carried by a proxy that never checked.

The invariant below is deliberately the weakest one that separates the two
worlds. What "verified" SHOULD mean — the baseline reproduces the failure the
proposal names, or a citation-verified diagnosis, or a named failing test — is
a semantic decision for the operator, not something to settle inside a test.
"""
from __future__ import annotations

import pytest

from core.self_repair_utils import _diagnosis_verified

_GREEN = {"timed_out": False, "exit_code": 0, "failed": 0, "errors": 0}
_RED = {"timed_out": False, "exit_code": 1, "failed": 3, "errors": 0}
_TIMED_OUT = {"timed_out": True, "exit_code": 1, "failed": 0, "errors": 0}


def test_a_timed_out_baseline_is_not_a_verified_diagnosis() -> None:
    """Boundary pin: the one distinction the current proxy does make."""
    assert _diagnosis_verified(_TIMED_OUT) is False
    assert _diagnosis_verified(None) is False


def test_a_reproduced_defect_is_a_verified_diagnosis() -> None:
    """The other boundary: whatever replaces the proxy must keep this true, or
    self-repair can never act on a real defect."""
    assert _diagnosis_verified(_RED) is True


@pytest.mark.xfail(
    reason=(
        "KNOWN GAP, measured 2026-08-20 and banked rather than fixed "
        "(MIR-110): `_diagnosis_verified` returns True for a baseline run in "
        "which nothing failed, so a repair proposal against healthy code is "
        "governed as though its diagnosis had been verified. The invariant: a "
        "baseline that reproduced no failure is not a verified diagnosis. The "
        "definition is unprescribed — reproduce-the-named-failure, a "
        "citation-verified diagnosis, or a named failing test are different "
        "policies and the choice belongs to the operator."
    ),
    strict=True,
)
def test_a_green_baseline_is_not_a_verified_diagnosis() -> None:
    assert _diagnosis_verified(_GREEN) is False
