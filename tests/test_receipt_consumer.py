"""Tool receipts slice 1c — verifier consumer tests."""
from __future__ import annotations

from pathlib import Path

from core.evidence import Evidence, ProvenanceChain, make_evidence
from core.receipt_consumer import (
    has_receipt_for_operation,
    matched_evidence_lacks_receipt,
    receipt_supports_evidence,
)
from core.tool_receipts import ToolReceipt, ToolReceiptLedger, default_receipts_path
from core.verifier import verify


def _test_ev(target: str = "tests") -> Evidence:
    return make_evidence(
        kind="test_result",
        source_id=f"test_result:run_tests:pytest:{target}",
        obtained_via="run_tests",
        claim="Test run verdict: passed=1, failed=0, errors=0",
        excerpt="1 passed",
    )


def _memory_ev() -> Evidence:
    return make_evidence(
        kind="memory",
        source_id="memory:mem_abc",
        obtained_via="memory",
        claim="Prior session note",
        excerpt="remember this",
    )


def _append_run_tests_receipt(
    workspace: Path,
    *,
    trace_id: str,
    receipt_id: str = "trc_test0001",
) -> None:
    ledger = ToolReceiptLedger(default_receipts_path(workspace))
    ledger.append_receipt(
        ToolReceipt(
            receipt_id=receipt_id,
            trace_id=trace_id,
            path="repl",
            operation="run_tests",
            status="success",
            summary="run_tests success",
            args_fingerprint="abc",
            result_fingerprint="def",
        )
    )


def test_has_receipt_for_operation_matches_trace_and_operation(workspace: Path) -> None:
    _append_run_tests_receipt(workspace, trace_id="trace_a")
    ledger = ToolReceiptLedger(default_receipts_path(workspace))
    assert has_receipt_for_operation(ledger, "trace_a", "run_tests")
    assert not has_receipt_for_operation(ledger, "trace_b", "run_tests")
    assert not has_receipt_for_operation(ledger, "trace_a", "file_read")


def test_receipt_supports_evidence_skips_non_slice_tools() -> None:
    ev = _memory_ev()
    ledger = ToolReceiptLedger(default_receipts_path(Path("/tmp/unused")))
    assert receipt_supports_evidence(ev, ledger, "trace_x")


def test_matched_evidence_lacks_receipt_when_slice_tool_missing_row(
    workspace: Path,
) -> None:
    ev = _test_ev()
    ledger = ToolReceiptLedger(default_receipts_path(workspace))
    assert matched_evidence_lacks_receipt(
        [ev],
        ledger=ledger,
        trace_id="trace_missing",
    )


def test_matched_evidence_lacks_receipt_false_when_receipt_present(
    workspace: Path,
) -> None:
    _append_run_tests_receipt(workspace, trace_id="trace_ok")
    ev = _test_ev()
    ledger = ToolReceiptLedger(default_receipts_path(workspace))
    assert not matched_evidence_lacks_receipt(
        [ev],
        ledger=ledger,
        trace_id="trace_ok",
    )


def test_verify_demotes_run_tests_claim_without_receipt(workspace: Path) -> None:
    ev = _test_ev(target="tests/bug_lab")
    chain = ProvenanceChain()
    chain.add(ev)
    answer = "Conclusion:\nTests passed [test:run_tests:bug_lab]."
    ledger = ToolReceiptLedger(default_receipts_path(workspace))

    report = verify(
        answer=answer,
        chain=chain,
        receipt_ledger=ledger,
        trace_id="trace_no_receipt",
    )

    assert report.verified_chunks == 0
    assert report.receipt_missing_chunks == 1
    assert report.chunks[0].verdict == "receipt_missing"
    assert "[no-receipt]" in report.annotated_answer
    assert "[unverified:test:run_tests:bug_lab]" in report.annotated_answer


def test_verify_keeps_run_tests_claim_when_receipt_present(workspace: Path) -> None:
    trace_id = "trace_with_receipt"
    _append_run_tests_receipt(workspace, trace_id=trace_id)
    ev = _test_ev(target="tests/bug_lab")
    chain = ProvenanceChain()
    chain.add(ev)
    answer = "Conclusion:\nTests passed [test:run_tests:bug_lab]."
    ledger = ToolReceiptLedger(default_receipts_path(workspace))

    report = verify(
        answer=answer,
        chain=chain,
        receipt_ledger=ledger,
        trace_id=trace_id,
    )

    assert report.verified_chunks == 1
    assert report.receipt_missing_chunks == 0
    assert report.chunks[0].verdict == "verified"
    assert "[verified:test:run_tests:bug_lab]" in report.annotated_answer
    assert "[no-receipt]" not in report.annotated_answer


def test_verify_memory_only_claim_unaffected_without_receipt(workspace: Path) -> None:
    ev = _memory_ev()
    chain = ProvenanceChain()
    chain.add(ev)
    answer = "Conclusion:\nAs noted [memory:mem_abc]."
    ledger = ToolReceiptLedger(default_receipts_path(workspace))

    report = verify(
        answer=answer,
        chain=chain,
        receipt_ledger=ledger,
        trace_id="trace_mem",
    )

    assert report.receipt_missing_chunks == 0


def test_verify_demotes_claim_that_starts_with_its_citation(workspace: Path) -> None:
    """Regression: a claim whose text begins with the citation must still get the
    ``[no-receipt]`` marker in the rendered answer. The old string-matching
    downgrade computed an empty prefix here and silently left ``[verified:...]``
    visible on an internally-distrusted claim."""
    ev = _test_ev(target="tests/bug_lab")
    chain = ProvenanceChain()
    chain.add(ev)
    # The claim chunk starts with the citation, not with prose.
    answer = "Conclusion:\n[test:run_tests:bug_lab] confirms the suite is green."
    ledger = ToolReceiptLedger(default_receipts_path(workspace))

    report = verify(
        answer=answer,
        chain=chain,
        receipt_ledger=ledger,
        trace_id="trace_no_receipt_prefix",
    )

    assert report.verified_chunks == 0
    assert report.receipt_missing_chunks == 1
    assert report.chunks[0].verdict == "receipt_missing"
    # The rendered answer must not still claim the demoted chunk is verified.
    assert "[verified:test:run_tests:bug_lab]" not in report.annotated_answer
    assert "[no-receipt]" in report.annotated_answer
