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

from core.self_repair_models import RepairProposal
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


def test_a_green_baseline_is_not_a_verified_diagnosis() -> None:
    """FIXED 2026-09-25 — the operator chose option (a): reproduce the named failure."""
    assert _diagnosis_verified(_GREEN) is False
    assert _diagnosis_verified(_GREEN, _proposal()) is False


_FAILED = {"timed_out": False, "exit_code": 1, "failed": 1, "errors": 0,
           "failed_tests": ["tests/test_units.py::test_meters_to_feet"]}


def _proposal(**kw) -> RepairProposal:
    return RepairProposal(path="core/units.py", proposed_content="x = 1\n", **kw)


def test_a_red_test_the_diagnosis_does_not_name_verifies_nothing() -> None:
    """Something red somewhere in the whole suite is not THIS defect reproduced."""
    assert _diagnosis_verified(_FAILED, _proposal(reason="off-by-one in parse()")) is False


@pytest.mark.parametrize("kw", [
    {"reason": "test_meters_to_feet fails: factor 3.28 is applied twice"},
    {"evidence": ("tests/test_units.py::test_meters_to_feet",)},
    {"test_paths": ("tests/test_units.py",)},
    {"test_pattern": "meters"},
], ids=["named_in_reason", "named_in_evidence", "named_by_path", "named_by_pattern"])
def test_a_reproduced_named_failure_is_a_verified_diagnosis(kw) -> None:
    assert _diagnosis_verified(_FAILED, _proposal(**kw)) is True


def test_a_red_run_without_test_names_cannot_show_the_named_one_failed() -> None:
    assert _diagnosis_verified(_RED, _proposal(reason="test_meters_to_feet")) is False
