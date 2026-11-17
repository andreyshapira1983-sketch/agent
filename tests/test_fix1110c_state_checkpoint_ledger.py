"""Checkpoint appends after a torn tail, odd checkpoint lines, and the campaign ledger reader."""
from __future__ import annotations

import json
from pathlib import Path

from core.campaign_ledger import load_ledger_rows
from core.checkpoint import PHASE_RESPOND, CheckpointLoader, CheckpointWriter

TORN = b'{"checkpoint_id": "cp_x", "phase": "act", "tra'


def _observed(tmp_path: Path, trace_id: str, tail: bytes) -> CheckpointWriter:
    writer = CheckpointWriter(trace_id, tmp_path)
    writer.save_observe("вопрос")
    with writer.path.open("ab") as fh:
        fh.write(tail)
    return writer


# ── checkpoint writer ────────────────────────────────────────────────────────

def test_record_appended_after_a_torn_tail_is_not_lost(tmp_path: Path) -> None:
    writer = _observed(tmp_path, "t1", TORN)

    writer.save_respond("готовый ответ")

    ctx = CheckpointLoader(tmp_path).load("t1")
    assert ctx is not None
    assert ctx.answer == "готовый ответ"
    assert ctx.last_phase == PHASE_RESPOND


def test_several_records_after_a_torn_tail_do_not_void_the_checkpoint(tmp_path: Path) -> None:
    writer = _observed(tmp_path, "t2", TORN)

    writer.save_plan(1, ["s1"])
    writer.save_respond("готовый ответ")

    ctx = CheckpointLoader(tmp_path).load("t2")
    assert ctx is not None
    assert ctx.answer == "готовый ответ"


def test_torn_tail_is_copied_aside_before_it_leaves_the_checkpoint(tmp_path: Path) -> None:
    writer = _observed(tmp_path, "t3", TORN)
    before = writer.path.read_bytes()

    writer.save_respond("a")

    copies = list(tmp_path.glob("checkpoints_t3.jsonl.*.bak"))
    assert len(copies) == 1
    assert copies[0].read_bytes() == before


def test_whole_record_that_only_lost_its_newline_is_kept(tmp_path: Path) -> None:
    whole = json.dumps({"phase": "plan", "trace_id": "t4", "data": {"attempt": 3}}).encode()
    writer = _observed(tmp_path, "t4", whole)

    writer.save_respond("a")

    ctx = CheckpointLoader(tmp_path).load("t4")
    assert ctx is not None
    assert (ctx.attempt, ctx.answer) == (3, "a")
    assert list(tmp_path.glob("*.bak")) == []


def test_clean_checkpoint_gets_no_copy_and_no_blank_lines(tmp_path: Path) -> None:
    writer = CheckpointWriter("t5", tmp_path)
    writer.save_observe("q")
    writer.save_respond("a")

    assert len(writer.path.read_bytes().splitlines()) == 2
    assert list(tmp_path.glob("*.bak")) == []


# ── checkpoint loader ────────────────────────────────────────────────────────

def test_last_line_that_is_json_but_not_an_object_is_skipped(tmp_path: Path) -> None:
    _observed(tmp_path, "t6", b"[1, 2]\n")

    ctx = CheckpointLoader(tmp_path).load("t6")

    assert ctx is not None
    assert ctx.question == "вопрос"


def test_middle_line_that_is_json_but_not_an_object_voids_the_checkpoint(tmp_path: Path) -> None:
    writer = _observed(tmp_path, "t7", b'"just a string"\n')
    writer.save_respond("a")

    assert CheckpointLoader(tmp_path).load("t7") is None


# ── campaign ledger reader ───────────────────────────────────────────────────

def _row(reason: str) -> bytes:
    return json.dumps({"action": "fix", "reason": reason, "cost_units_spent": 1}, ensure_ascii=False).encode()


def test_ledger_torn_inside_a_character_still_gives_the_whole_rows(tmp_path: Path) -> None:
    path = tmp_path / "campaign_ledger.jsonl"
    torn = '{"action": "fix", "reason": "трет'.encode()[:-1]
    path.write_bytes(_row("первая") + b"\n" + _row("вторая") + b"\n" + torn)

    rows = load_ledger_rows(path)

    assert [r["reason"] for r in rows] == ["первая", "вторая"]


def test_line_separator_inside_a_value_does_not_break_the_row(tmp_path: Path) -> None:
    reason = "до после\u0085и ещё"
    path = tmp_path / "campaign_ledger.jsonl"
    path.write_bytes(_row(reason) + b"\r\n" + _row("следующая") + b"\n")

    rows = load_ledger_rows(path)

    assert [r["reason"] for r in rows] == [reason, "следующая"]
