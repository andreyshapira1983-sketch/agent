"""Выполненная запись файла — улика хода; невыполненная подтверждения не получает."""
from __future__ import annotations

from pathlib import Path

from core.answer_format import format_allowed_citations_block
from core.evidence import (
    ProvenanceChain,
    evidence_from_sensor_block,
    evidence_from_tool_result,
)
from core.tool_receipts import ToolReceipt, ToolReceiptLedger, default_receipts_path
from core.unsupported_claims import apply_answer_enforcement
from core.verifier import verify

_QUESTION = "Создай файл w1.txt с одной строкой: привет из чата"
_CONTENT = "привет из чата"
_OUTPUT = {"path": "w1.txt", "mode": "create",
           "bytes_written": len(_CONTENT.encode("utf-8")), "backup_path": None}
_DRAFT = """Conclusion: Файл w1.txt создан, в него записана одна строка.

Facts:
- Запись выполнена в режиме создания нового файла w1.txt. [tool:file_write:w1.txt]
- Объём записи — 26 байт. [tool:file_write:w1.txt]

Sources:
  1. file_write:w1.txt - w1.txt

Confidence: medium

Unverified: nothing

Safety: nothing
"""


def _chain(*, status: str | None) -> ProvenanceChain:
    """Цепочка хода: сенсор есть всегда, запись — только если вызов был."""
    chain = ProvenanceChain()
    chain.add(evidence_from_sensor_block(
        name="model_roster", content="<model_roster>planner: deepseek</model_roster>"))
    if status is not None:
        ev = evidence_from_tool_result(
            tool_name="file_write", arguments={"path": "w1.txt", "content": _CONTENT},
            output=_OUTPUT if status == "success" else None, status=status)
        if ev is not None:
            chain.add(ev)
    return chain


def _enforced(chain: ProvenanceChain, **verify_kwargs):
    report = verify(answer=_DRAFT, chain=chain, user_question=_QUESTION, **verify_kwargs)
    return report, apply_answer_enforcement(answer=_DRAFT, report=report, question=_QUESTION)


def test_successful_write_is_a_citable_evidence_of_the_turn() -> None:
    chain = _chain(status="success")
    written = chain.by_kind("tool_output")
    assert len(written) == 1
    assert written[0].source_id == "tool_output:file_write:w1.txt"
    assert written[0].obtained_via == "file_write"
    assert "path=w1.txt" in written[0].excerpt
    assert "bytes_written=26" in written[0].excerpt
    assert "[tool:file_write:w1.txt]" in format_allowed_citations_block(chain)


def test_confirmation_of_a_done_write_is_not_withheld() -> None:
    report, result = _enforced(_chain(status="success"))
    assert report.cited_but_unmatched_chunks == 0
    assert report.verified_chunks == 2
    assert result.outcome != "citation_integrity"
    assert "Файл w1.txt создан" in result.answer
    assert "Черновик ответа не отправлен" not in result.answer


def test_failed_write_is_not_confirmed() -> None:
    chain = _chain(status="error")
    assert chain.by_kind("tool_output") == []
    report, result = _enforced(chain)
    assert report.verified_chunks == 0
    assert result.outcome == "citation_integrity"
    assert "Файл w1.txt создан" not in result.answer


def test_denied_or_never_attempted_write_is_not_confirmed() -> None:
    report, result = _enforced(_chain(status=None))
    assert report.verified_chunks == 0
    assert result.outcome == "citation_integrity"
    assert "Файл w1.txt создан" not in result.answer


def test_malformed_write_output_yields_no_evidence() -> None:
    for output in (None, "ok", {"path": "w1.txt"}, {"bytes_written": 26}):
        assert evidence_from_tool_result(
            tool_name="file_write", arguments={"content": _CONTENT}, output=output) is None


def test_content_is_named_only_when_the_byte_count_agrees() -> None:
    agreed = evidence_from_tool_result(
        tool_name="file_write", arguments={"path": "w1.txt", "content": _CONTENT}, output=_OUTPUT)
    differs = evidence_from_tool_result(
        tool_name="file_write", arguments={"path": "w1.txt", "content": _CONTENT},
        output={**_OUTPUT, "bytes_written": 3})
    assert agreed is not None and _CONTENT in agreed.excerpt
    assert differs is not None and _CONTENT not in differs.excerpt


def test_secret_in_written_content_does_not_reach_the_excerpt() -> None:
    secret = "sk-" + "a1B2c3D4" * 6
    content = f"api_key={secret}"
    ev = evidence_from_tool_result(
        tool_name="file_write", arguments={"path": "k.txt", "content": content},
        output={"path": "k.txt", "mode": "create",
                "bytes_written": len(content.encode("utf-8")), "backup_path": None})
    assert ev is not None
    assert secret not in ev.excerpt


def test_write_claim_needs_the_tool_receipt_of_this_trace(workspace: Path) -> None:
    ledger = ToolReceiptLedger(default_receipts_path(workspace))
    chain = _chain(status="success")
    report, _ = _enforced(chain, receipt_ledger=ledger, trace_id="trace_w")
    assert report.verified_chunks == 0
    assert report.receipt_missing_chunks == 2

    ledger.append_receipt(ToolReceipt(
        receipt_id="trc_w1", trace_id="trace_w", path="repl", operation="file_write",
        status="success", summary="file_write success path=w1.txt bytes=26 mode=create",
        args_fingerprint="a", result_fingerprint="b"))
    report, result = _enforced(chain, receipt_ledger=ledger, trace_id="trace_w")
    assert report.verified_chunks == 2
    assert report.receipt_missing_chunks == 0
    assert "Файл w1.txt создан" in result.answer
