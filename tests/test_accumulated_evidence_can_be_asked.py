"""Накопленную улику теперь есть чем спросить — оба читателя MIR-138.

Замер и границы: MIR-138 в docs/audit/MASTER_ISSUE_REGISTRY.md.

Две оси одного класса: квитанции инструментов (1,2 МБ первичной улики,
спрашиваемой раньше только внутри одного прогона) и архив памяти (778 строк,
86 % памяти, недостижимые ни одной командой). Оба читателя — ТОЛЬКО чтение:
анти-требование записи («не удалять хранилище, не делать писателя условным»)
здесь закон, и свидетель байтовой неизменности стоит на обоих.
"""
from __future__ import annotations

from pathlib import Path

from core.models import MemoryRecord
from core.persistent_memory import PersistentMemoryStore
from core.tool_receipts import ToolReceipt, ToolReceiptLedger, summarise_receipts


def _ledger(tmp_path: Path) -> ToolReceiptLedger:
    ledger = ToolReceiptLedger(path=tmp_path / "data" / "tool_receipts.jsonl")
    for op, trace in (("file_write", "trace-aaa"), ("file_write", "trace-bbb"),
                      ("shell_exec", "trace-aaa")):
        ledger.append_receipt(ToolReceipt(
            receipt_id=f"r-{op}-{trace}", operation=op, status="ok",
            summary=f"{op} по {trace}", args_fingerprint="a",
            result_fingerprint="b", trace_id=trace,
        ))
    return ledger


def test_receipts_answer_counts_and_a_trace_question(tmp_path: Path) -> None:
    """Красный свидетель: недельную улику можно спросить, а не только копить."""
    ledger = _ledger(tmp_path)

    report = summarise_receipts(ledger)
    assert "file_write" in report and "2" in report, report
    assert "shell_exec" in report

    focused = summarise_receipts(ledger, trace="trace-aaa")
    assert "trace-aaa" in focused
    assert "trace-bbb" not in focused


def test_an_empty_ledger_is_an_honest_zero(tmp_path: Path) -> None:
    ledger = ToolReceiptLedger(path=tmp_path / "data" / "tool_receipts.jsonl")

    report = summarise_receipts(ledger)

    assert "0" in report


def test_the_archive_is_searchable_and_reading_changes_nothing(tmp_path: Path) -> None:
    """86 % памяти достижимы; чтение не трогает ни байта (анти-требование)."""
    store = PersistentMemoryStore(tmp_path / "data" / "persistent_memory.jsonl")
    from core.state_integrity import append_state_jsonl
    append_state_jsonl(store.archive_path, [
        MemoryRecord(content="оператор говорит по-русски").model_dump(mode="json"),
        MemoryRecord(content="таймаут веб-инструмента 30 секунд").model_dump(mode="json"),
    ])
    before = store.archive_path.read_bytes()

    hits = store.search_archive("таймаут")

    assert len(hits) == 1 and "таймаут" in str(hits[0].content)
    assert store.search_archive("несуществующее-слово") == []
    assert store.archive_path.read_bytes() == before, "чтение обязано быть чтением"


def test_both_readers_are_reachable_from_the_keyboard() -> None:
    """Проводка: читатель без двери — снова MIR-138. Пин по смыслу."""
    import inspect

    from cli import command_dispatch as disp
    from cli import commands_memory as mem

    assert '":receipts"' in inspect.getsource(disp)
    assert "archive" in inspect.getsource(mem._handle_smart_memory)
