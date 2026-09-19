"""Tool receipts slice 1c — minimal consumer for verifier integration."""
from __future__ import annotations

from core.evidence import Evidence
from core.tool_receipts import SLICE_1A_RECEIPT_TOOLS, ToolReceiptLedger


def operation_requires_receipt(operation: str) -> bool:
    return operation in SLICE_1A_RECEIPT_TOOLS


def has_receipt_for_operation(
    ledger: ToolReceiptLedger,
    trace_id: str,
    operation: str,
) -> bool:
    tid = (trace_id or "").strip()
    op = (operation or "").strip()
    if not tid or not op:
        return False
    return any(r.operation == op for r in ledger.list_by_trace(tid))


def receipt_supports_evidence(
    ev: Evidence,
    ledger: ToolReceiptLedger,
    trace_id: str,
) -> bool:
    if not operation_requires_receipt(ev.obtained_via):
        return True
    return has_receipt_for_operation(ledger, trace_id, ev.obtained_via)


def matched_evidence_lacks_receipt(
    evidences: list[Evidence],
    *,
    ledger: ToolReceiptLedger | None,
    trace_id: str | None,
) -> bool:
    """Return True when any slice-1a evidence lacks a ledger row for this trace."""
    if ledger is None:
        return False
    tid = (trace_id or "").strip()
    if not tid:
        return False
    for ev in evidences:
        if operation_requires_receipt(ev.obtained_via) and not has_receipt_for_operation(
            ledger, tid, ev.obtained_via
        ):
            return True
    return False
