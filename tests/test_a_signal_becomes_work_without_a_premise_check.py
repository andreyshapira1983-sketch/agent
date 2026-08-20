"""MIR-100: the system's ONE premise gate, pinned so it cannot vanish.

Every other gate between a backlog signal and an approval item checks
authority, grounding (the quote exists) or product quality (the artefact the
hands made). Only the diagnosis road asks whether the premise is true, and
it asks the MIR-060 verifier — the counter that scored a false claim 3/3 and
a true claim about an absence 0/5. Measurement and why no xfail is banked
here: MIR-100.
"""
from __future__ import annotations

import ast
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _function_source(module_rel: str, name: str) -> str:
    src = (_REPO / module_rel).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"{name}() is gone from {module_rel} — see MIR-100")


def test_the_repair_road_refuses_a_partly_verified_diagnosis() -> None:
    """The gate itself: every examined chunk must be verified, or no work."""
    body = _function_source("core/campaign_io.py", "_propose_repair_from_diagnosis")
    assert "last_verification" in body
    assert "verified_chunks" in body and "total_chunks" in body
    assert "verified != examined" in body, (
        "the only premise gate in the system changed shape; MIR-100 assumes it "
        "demands FULL verification, not a threshold"
    )


def test_that_gate_rides_on_the_citation_verifier() -> None:
    """Why MIR-100 calls it a gate on a known-asymmetric instrument: the
    counter it reads is the verifier's chunk tally, not an independent check."""
    src = (_REPO / "core" / "verification_summary.py")
    if not src.is_file():  # organ renamed — the pin below still holds the claim
        src = _REPO / "core" / "answer_verification.py"
    assert src.is_file(), "the verifier organ moved; re-check MIR-060/MIR-100"
    text = src.read_text(encoding="utf-8")
    assert "verified_chunks" in text, (
        "verified_chunks no longer originates in the verifier — the premise "
        "gate may now read something else; MIR-100 needs re-measuring"
    )
