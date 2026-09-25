"""Работа полезна, если ею ПОТОМ воспользовались (core/work_usefulness.py).

Мерило только считает. Первый прогон на живых данных 2026-09-25 засчитал
пользой повторное чтение книги, которую цель велела читать (629 из 802), —
этот случай закреплён отдельным тестом.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from core.work_usefulness import measure, summary

NOW = datetime(2026, 9, 30, tzinfo=timezone.utc)


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _cycle(ts: str, goal: str, **extra) -> dict:
    return {"ts": ts, "goal": goal, "work_done": True, "llm_calls_spent": 4, "result": "completed", **extra}


def _call(ts: str, tool: str, path: str, cid: str = "tc") -> dict:
    return {"ts": ts, "event": "tool_call", "payload": {"id": cid, "tool_name": tool, "arguments": {"path": path}}}


def _ws(tmp_path: Path, cycles: list[dict], trace: list[dict], **data_files) -> Path:
    _jsonl(tmp_path / "data" / "campaign_ledger.jsonl",
           [{"ts": "2026-09-22T09:00:00+00:00", "goal": "start", "work_done": False}, *cycles])
    _jsonl(tmp_path / "logs" / "trace_a.jsonl", trace)
    for name, rows in data_files.items():
        _jsonl(tmp_path / "data" / f"{name}.jsonl", rows)
    return tmp_path


def _one(tmp_path: Path, **kw):
    [cycle] = measure(tmp_path, window_days=3, now=NOW, **kw)
    return cycle


def test_a_written_note_read_later_is_useful(tmp_path: Path) -> None:
    _ws(tmp_path, [_cycle("2026-09-22T10:00:00+00:00", "write a note")],
        [_call("2026-09-22T09:30:00+00:00", "file_write", "data/notes/n.md"),
         _call("2026-09-23T08:00:00+00:00", "file_read", "data/notes/n.md")])
    cycle = _one(tmp_path)
    assert cycle.verdict == "useful" and cycle.signals["read_later"]


def test_rereading_the_source_the_goal_named_is_not_use_of_the_work(tmp_path: Path) -> None:
    book = "knowledge_library/cs/txt/Book.txt"
    _ws(tmp_path, [_cycle("2026-09-22T10:00:00+00:00", f"read {book}",
                          artifact=f"reasoning: Conclusion: in {book} the section says ...")],
        [_call("2026-09-22T09:30:00+00:00", "file_read", book),
         _call("2026-09-23T08:00:00+00:00", "file_read", book)])
    cycle = _one(tmp_path)
    assert cycle.verdict == "not_yet" and not cycle.products


def test_only_a_human_approval_counts_as_approved(tmp_path: Path) -> None:
    inbox = [
        {"id": "ain_aaaaaaaaaaaa", "created_at": "2026-09-22T09:40:00+00:00", "status": "pending"},
        {"id": "ain_aaaaaaaaaaaa", "status": "approved", "decided_by": "sandbox:burn_in",
         "updated_at": "2026-09-22T11:00:00+00:00"},
    ]
    filed = _cycle("2026-09-22T10:00:00+00:00", "propose", proposal="approvals_new=1")
    _ws(tmp_path, [filed], [], approval_inbox=inbox)
    assert _one(tmp_path).signals["approved"] is False
    inbox.append({"id": "ain_aaaaaaaaaaaa", "status": "approved", "decided_by": "andre (operator)",
                  "updated_at": "2026-09-22T12:00:00+00:00"})
    _ws(tmp_path, [filed], [], approval_inbox=inbox)
    assert _one(tmp_path).signals["approved"] is True
    assert summary(measure(tmp_path, window_days=3, now=NOW))["out_of_band"] == 1
    # Тот же ящик, но цикл заявки не подавал: чужая заявка в том же окне не его.
    _ws(tmp_path, [_cycle("2026-09-22T10:00:00+00:00", "read a book")], [], approval_inbox=inbox)
    assert _one(tmp_path).signals["approved"] is False


def test_memory_written_in_the_cycle_and_served_later_is_useful(tmp_path: Path) -> None:
    _ws(tmp_path, [_cycle("2026-09-22T10:00:00+00:00", "learn")],
        [{"ts": "2026-09-24T10:00:00+00:00", "event": "persistent_memory_inject", "payload": {"ids": ["mem_1"]}}],
        persistent_memory=[{"id": "mem_1", "created_at": "2026-09-22T09:50:00+00:00", "content": "x"}])
    assert _one(tmp_path).signals["memory_used"] is True


def test_machine_checks_count_without_a_human(tmp_path: Path) -> None:
    _ws(tmp_path, [_cycle("2026-09-22T10:00:00+00:00", "fix the parser")],
        [_call("2026-09-22T09:20:00+00:00", "file_write", "core/parser.py"),
         _call("2026-09-22T09:30:00+00:00", "run_tests", "tests", cid="tc_t"),
         {"ts": "2026-09-22T09:31:00+00:00", "event": "tool_result",
          "payload": {"tool_call_id": "tc_t", "output": {"exit_code": 0, "passed": 12, "failed": 0}}}],
        campaign_verdicts=[{"ts": "2026-09-22T10:01:00+00:00", "goal": "fix the parser", "verdict": "verified"}])
    cycle = _one(tmp_path)
    assert cycle.signals["tests_passed"] and cycle.signals["goal_verified"] and cycle.verdict == "useful"
    # Свои тесты и свой судья — польза, но не подтверждение извне.
    assert summary([cycle])["out_of_band"] == 0


def test_a_young_cycle_is_pending_and_the_summary_counts_honestly(tmp_path: Path) -> None:
    _ws(tmp_path, [_cycle("2026-09-22T10:00:00+00:00", "old, nothing came of it"),
                   _cycle("2026-09-29T10:00:00+00:00", "too young to judge")], [])
    cycles = measure(tmp_path, window_days=3, now=NOW)
    assert [c.verdict for c in cycles] == ["not_yet", "pending"]
    stats = summary(cycles)
    assert (stats["judged"], stats["useful"], stats["pending"], stats["no_product_named"]) == (1, 0, 1, 1)
