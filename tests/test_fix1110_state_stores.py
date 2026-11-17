"""Damaged or odd state files must not lose data or mislead the operator."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cli.resume import resolve_resume
from core.campaign_ledger import load_ledger_rows
from core.checkpoint import PHASE_RESPOND, CheckpointLoader, CheckpointWriter
from core.market_ledger import LedgerUnreadable, MarketLedger
from core.scheduler import SchedulerService, SchedulerStore, ScheduleTickReport
from core.spend_report import load_spend_rows
from core.state_integrity import read_state_jsonl, rewrite_state_jsonl
from core.task_queue import TaskQueueStore

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


# ── market ledger ────────────────────────────────────────────────────────────

def _order(i: int) -> dict:
    return {"assignment": {"assignmentId": f"a{i}", "jobId": f"j{i}", "escrowAmount": "5", "status": "accepted"},
            "job": {"title": f"order {i}"}}


def _torn_ledger(tmp_path: Path) -> tuple[MarketLedger, str]:
    ledger = MarketLedger(tmp_path / "ledger.json")
    for i in (1, 2, 3):
        ledger.add_run(_order(i), 60, 0.1)
    raw = ledger.path.read_text(encoding="utf-8")
    torn = raw[: len(raw) // 2]
    ledger.path.write_text(torn, encoding="utf-8")
    return ledger, torn


def test_torn_market_ledger_is_copied_aside_before_a_write(tmp_path: Path) -> None:
    ledger, torn = _torn_ledger(tmp_path)

    ledger.add_run(_order(4), 60, 0.1)

    copies = list(tmp_path.glob("ledger.json.*.bak"))
    assert len(copies) == 1
    assert copies[0].read_text(encoding="utf-8") == torn
    assert sorted(ledger.lines()) == ["a4"]


def test_torn_market_ledger_is_not_touched_by_observe_or_mark(tmp_path: Path) -> None:
    ledger, torn = _torn_ledger(tmp_path)

    ledger.observe(_order(1))
    with pytest.raises(LedgerUnreadable):
        ledger.mark("a1", claude_minutes=5)

    assert ledger.path.read_text(encoding="utf-8") == torn


def test_missing_market_ledger_is_just_empty(tmp_path: Path) -> None:
    ledger = MarketLedger(tmp_path / "ledger.json")

    ledger.add_run(_order(1), 60, 0.1)

    assert sorted(ledger.lines()) == ["a1"]
    assert list(tmp_path.glob("*.bak")) == []


# ── scheduler: unparseable rows ──────────────────────────────────────────────

def _store_with_hand_edit(tmp_path: Path) -> SchedulerStore:
    store = SchedulerStore(tmp_path / "schedules.jsonl")
    far = NOW + timedelta(days=1)
    store.add(name="good", goal="g1", every_minutes=60, start_at=far)
    store.add(name="hand-edited", goal="g2", every_minutes=60, start_at=far)
    rows = read_state_jsonl(store.path)
    rows[1]["status"] = "Active"
    rewrite_state_jsonl(store.path, rows)
    return store


def test_tick_keeps_a_row_it_cannot_parse_and_reports_it(tmp_path: Path) -> None:
    store = _store_with_hand_edit(tmp_path)

    report = store.tick(task_queue=TaskQueueStore(tmp_path / "tasks.jsonl"), now=NOW)

    rows = read_state_jsonl(store.path)
    assert [r["name"] for r in rows] == ["good", "hand-edited"]
    assert rows[1]["status"] == "Active"
    assert report.total_count == 1
    assert report.unparsed_count == 1
    assert report.to_dict()["unparsed_count"] == 1
    assert "unparsed=1" in report.user_summary()


def test_add_and_pause_keep_a_row_they_cannot_parse(tmp_path: Path) -> None:
    store = _store_with_hand_edit(tmp_path)

    added = store.add(name="new", goal="g3", every_minutes=60, start_at=NOW)
    store.pause(added.id)

    assert sorted(r["name"] for r in read_state_jsonl(store.path)) == ["good", "hand-edited", "new"]


def test_only_unparsed_rows_are_not_called_an_empty_store() -> None:
    report = ScheduleTickReport(total_count=0, due_count=0, enqueued_count=0,
                                task_ids=(), schedule_ids=(), unparsed_count=2)

    assert "no schedules configured" not in report.user_summary()
    assert "unparsed=2" in report.user_summary()


def test_clean_tick_summary_is_unchanged(tmp_path: Path) -> None:
    store = SchedulerStore(tmp_path / "schedules.jsonl")
    store.add(name="good", goal="g1", every_minutes=60, start_at=NOW + timedelta(days=1))

    report = store.tick(task_queue=TaskQueueStore(tmp_path / "tasks.jsonl"), now=NOW)

    assert report.unparsed_count == 0
    assert "unparsed" not in report.user_summary()


# ── scheduler service: limit ─────────────────────────────────────────────────

def test_service_rejects_zero_limit(tmp_path: Path) -> None:
    store = SchedulerStore(tmp_path / "schedules.jsonl")
    queue = TaskQueueStore(tmp_path / "tasks.jsonl")

    with pytest.raises(ValueError, match="limit must be >= 1"):
        SchedulerService(store, queue, limit=0)


# ── checkpoint ───────────────────────────────────────────────────────────────

def _finished_run(log_dir: Path, trace_id: str) -> CheckpointWriter:
    writer = CheckpointWriter(trace_id, log_dir)
    writer.save_observe("вопрос")
    writer.save_respond("готовый ответ")
    return writer


def test_torn_last_checkpoint_line_is_skipped(tmp_path: Path) -> None:
    writer = _finished_run(tmp_path, "t1")
    with writer.path.open("a", encoding="utf-8") as fh:
        fh.write('{"checkpoint_id": "cp_x", "phase": "act", "tra')

    ctx = CheckpointLoader(tmp_path).load("t1")

    assert ctx is not None
    assert ctx.answer == "готовый ответ"
    assert ctx.last_phase == PHASE_RESPOND


def test_checkpoint_line_torn_inside_a_utf8_character_is_skipped(tmp_path: Path) -> None:
    writer = _finished_run(tmp_path, "t2")
    torn = '{"phase": "act", "data": {"label": "шаг'.encode()[:-1]
    writer.path.write_bytes(writer.path.read_bytes() + torn)

    ctx = CheckpointLoader(tmp_path).load("t2")

    assert ctx is not None
    assert ctx.answer == "готовый ответ"


def test_damage_in_the_middle_of_a_checkpoint_still_voids_it(tmp_path: Path) -> None:
    writer = CheckpointWriter("t3", tmp_path)
    writer.save_observe("q")
    with writer.path.open("ab") as fh:
        fh.write(b'{"phase": "act", "data": {"label": "\xd1\n')
    writer.save_respond("a")

    assert CheckpointLoader(tmp_path).load("t3") is None


# ── spend mirror ─────────────────────────────────────────────────────────────

def test_spend_mirror_reads_the_ledger_like_its_own_reader(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    path = tmp_path / "data" / "campaign_ledger.jsonl"
    good = json.dumps({"action": "fix", "result": "completed", "cost_units_spent": 10})
    path.write_text("\n".join([good] * 100) + "\n" + '{"action": "fix", "resu', encoding="utf-8")

    _usage, ledger = load_spend_rows(tmp_path)

    assert len(ledger) == 100
    assert ledger == load_ledger_rows(path)


# ── resume ───────────────────────────────────────────────────────────────────

def test_resume_says_when_a_new_ask_is_ignored(tmp_path: Path, capsys) -> None:
    _finished_run(tmp_path / "logs", "run_done")

    decision = resolve_resume("run_done", workspace=tmp_path, ask="new question", file_hint=None)

    out = capsys.readouterr()
    assert decision.exit_code == 0
    assert "готовый ответ" in out.out
    assert "--ask" in out.err and "ignored" in out.err


def test_resume_replay_without_ask_has_no_ignored_notice(tmp_path: Path, capsys) -> None:
    _finished_run(tmp_path / "logs", "run_done")

    resolve_resume("run_done", workspace=tmp_path, ask=None, file_hint=None)

    assert "ignored" not in capsys.readouterr().err
