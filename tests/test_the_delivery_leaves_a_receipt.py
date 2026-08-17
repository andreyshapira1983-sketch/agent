"""A lesson delivered into a prompt leaves a receipt the meter can read.

The provenance meter measured the gap (2026-08-17): delivery was
structurally unobservable — distilled_lessons rode into the Stage A builder
prompt without a trace, so `injected` could never leave ABSENT. Contract
under test: the moment lessons actually reach a prompt, one row per lesson
lands in data/lesson_injections.jsonl; a produced approval adds an
action receipt. No lessons — no rows; a keyless card — no row.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from core.causal_claim_store import LessonCard, distilled_lessons, save_claim
from core.lesson_provenance import (
    record_lesson_injections,
    trace_lesson_provenance,
)


def _card(key: str = "cclaim_test0001") -> LessonCard:
    return LessonCard(
        rule="rule", scope="scope", directive="directive",
        machine_action="", evidence=(), key=key,
    )


def _journal_rows(tmp_path: Path) -> list[dict]:
    p = tmp_path / "data" / "lesson_injections.jsonl"
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        rows.append(rec.get("payload", rec))
    return rows


def test_a_card_knows_its_claim_key(tmp_path: Path) -> None:
    """distilled_lessons carries the store key — receipts need an identity."""
    from tests.test_lessons_are_distilled_not_assumed import _full_ladder

    key = save_claim(_full_ladder(), workspace=tmp_path, directive="d")
    cards = distilled_lessons(tmp_path)
    assert cards and cards[0].key == key


def _saved_card(tmp_path: Path) -> LessonCard:
    """A card whose key names a claim the store actually holds."""
    from tests.test_lessons_are_distilled_not_assumed import _full_ladder

    key = save_claim(_full_ladder(), workspace=tmp_path, directive="d")
    return _card(key=key)


def test_recording_a_delivery_proves_injection(tmp_path: Path) -> None:
    card = _saved_card(tmp_path)
    record_lesson_injections(tmp_path, (card,), consumer="test.builder")
    report = trace_lesson_provenance(tmp_path, card.key)
    link = next(lk for lk in report.links if lk.name == "injected")
    assert link.status == "PROVEN"


def test_no_lessons_leave_no_receipt(tmp_path: Path) -> None:
    record_lesson_injections(tmp_path, (), consumer="test.builder")
    assert not (tmp_path / "data" / "lesson_injections.jsonl").exists()


def test_a_keyless_card_leaves_no_row(tmp_path: Path) -> None:
    record_lesson_injections(
        tmp_path, (_card(key=""),), consumer="test.builder")
    assert not _journal_rows(tmp_path)


def test_an_action_ref_rides_the_receipt(tmp_path: Path) -> None:
    card = _saved_card(tmp_path)
    record_lesson_injections(
        tmp_path, (card,), consumer="test.builder",
        action_ref="approval:ain_x")
    report = trace_lesson_provenance(tmp_path, card.key)
    link = next(lk for lk in report.links if lk.name == "acted")
    assert link.status == "PROVEN"
    assert "approval:ain_x" in link.refs


def test_the_producer_leaves_the_receipt_live(tmp_path: Path) -> None:
    """End to end: a LESSON in the store, a Stage A run, a receipt row with
    the approval as the action — `injected` and `acted` measure PROVEN."""
    from core.self_task_producer import produce_coding_task
    from tests.test_lessons_are_distilled_not_assumed import _full_ladder

    key = save_claim(_full_ladder(), workspace=tmp_path, directive="d")
    target = "core/sample_module.py"
    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "core" / "sample_module.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()

    class _JsonLLM:
        def complete(self, *, system: str, user: str, **_kw) -> str:
            return json.dumps({
                "task_title": "t", "task_summary": "s",
                "impl_path": target,
                "test_path": "tests/test_sample_module_repro.py",
                "test_lines": [
                    "from core.sample_module import add",
                    "",
                    "def test_add_reproduces_the_defect():",
                    "    assert add(1, 2) == 4",
                ],
                "confidence": 0.9,
            })

    class _Inbox:
        def list(self):
            return []

        def add(self, **kw):
            return SimpleNamespace(id="ain_receipt_e2e")

    report = produce_coding_task(
        workspace=tmp_path, inbox=_Inbox(), llm=_JsonLLM(),
        task_selector=lambda: SimpleNamespace(
            target_path=target,
            problem_quote="def add(a, b):",
            evidence_ref="verified_diagnosis:trace_x",
        ),
        source_kind="verified_diagnosis",
    )

    assert report.status == "proposed", report.reason
    rows = [r for r in _journal_rows(tmp_path) if r.get("lesson_key") == key]
    assert rows, "the delivery left no receipt"
    prov = trace_lesson_provenance(tmp_path, key)
    statuses = {lk.name: lk.status for lk in prov.links}
    assert statuses["injected"] == "PROVEN"
    assert statuses["acted"] == "PROVEN"
    acted = next(lk for lk in prov.links if lk.name == "acted")
    assert "approval:ain_receipt_e2e" in acted.refs
