"""Tests for core.confidence_gate (post-verifier confidence gate)."""

from dataclasses import dataclass

import pytest

from core.confidence_gate import (
    ConfidenceGate,
    compute_confidence,
)


@dataclass
class _FakeReport:
    total_chunks: int = 0
    verified_chunks: int = 0
    cited_but_unmatched_chunks: int = 0
    unverified_chunks: int = 0
    self_declared_chunks: int = 0
    chain_was_empty: bool = False
    fully_unverified: bool = False


def test_compute_confidence_none_report():
    assert compute_confidence(None) == 0.0


def test_compute_confidence_zero_chunks():
    assert compute_confidence(_FakeReport(total_chunks=0)) == 0.0


def test_compute_confidence_all_verified_returns_one():
    r = _FakeReport(total_chunks=4, verified_chunks=4)
    assert compute_confidence(r) == 1.0


def test_compute_confidence_half_credit_for_cited():
    r = _FakeReport(total_chunks=4, cited_but_unmatched_chunks=4)
    assert compute_confidence(r) == 0.5


def test_compute_confidence_penalises_unverified():
    r = _FakeReport(
        total_chunks=4, verified_chunks=2, unverified_chunks=2
    )
    # (2 * 1.0 + 2 * -0.25) / 4 = 1.5 / 4 = 0.375
    assert compute_confidence(r) == pytest.approx(0.375)


def test_compute_confidence_clamped_to_zero():
    r = _FakeReport(total_chunks=2, unverified_chunks=2)
    assert compute_confidence(r) == 0.0


def test_gate_does_not_trigger_below_min_total():
    gate = ConfidenceGate(threshold=0.5, min_total_chunks=3)
    r = _FakeReport(total_chunks=2, unverified_chunks=2)
    res = gate.evaluate(r)
    assert res.triggered is False
    assert res.confidence == 0.0


def test_gate_triggers_on_low_confidence():
    gate = ConfidenceGate(threshold=0.5, min_total_chunks=2)
    r = _FakeReport(total_chunks=4, unverified_chunks=4)
    res = gate.evaluate(r)
    assert res.triggered is True
    assert res.confidence == 0.0


def test_gate_does_not_trigger_when_above_threshold():
    gate = ConfidenceGate(threshold=0.5)
    r = _FakeReport(total_chunks=4, verified_chunks=3, unverified_chunks=1)
    res = gate.evaluate(r)
    assert res.triggered is False


def test_gate_handles_none_report():
    gate = ConfidenceGate()
    res = gate.evaluate(None)
    assert res.triggered is False
    assert res.chain_was_empty is True


def test_gate_invalid_threshold_raises():
    with pytest.raises(ValueError):
        ConfidenceGate(threshold=1.5)
    with pytest.raises(ValueError):
        ConfidenceGate(min_total_chunks=-1)


def test_gate_log_payload_shape():
    gate = ConfidenceGate(threshold=0.5)
    r = _FakeReport(total_chunks=4, unverified_chunks=4)
    payload = gate.evaluate(r).to_log_payload()
    for key in ("confidence", "threshold", "total_chunks", "triggered",
                "chain_was_empty", "fully_unverified"):
        assert key in payload
