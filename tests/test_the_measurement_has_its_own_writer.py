"""The measured link gets its writer — and it is the one who measures.

Operator's chain, last link: the injector must not certify outcomes. The
Stage A critic already IS the measuring instrument for the phantom-kwargs
class (structural signature check on every generation), so the critic's
verdict writes the measurement: defect_absent when an armed generation
shows no phantoms, defect_recurred when phantoms survived the lesson.
A skipped instrument (test did not parse) writes nothing — no measurement
happened. The record lands in data/lesson_measurements.jsonl and its
receipt lifts the meter's `measured` link out of ABSENT.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from core.causal_claim_store import save_claim
from core.lesson_provenance import (
    record_lesson_measurement,
    trace_lesson_provenance,
)


def _saved_lesson(tmp_path: Path) -> str:
    """A full-ladder LESSON carrying the REAL store spelling of the signal.

    The shared fixture says "phantom_signature"; the live store says
    "phantom_signature_kwargs" — and the instrument matches exactly, so a
    lesson of another vocabulary is honestly not measured by it.
    """
    from core.causal_lesson import CausalClaim, Observation
    from tests.test_lessons_are_distilled_not_assumed import _full_ladder

    ladder = _full_ladder()
    claim = CausalClaim(**{**ladder.__dict__, "observation": Observation(
        episode_id="ep-run-a", trace_id="trace_a", run_id="run_a",
        defect_signals=("phantom_signature_kwargs",),
        evidence_refs=("approval:ain_31874b06",),
        observed_mismatch="RepairProposal called with invented kwargs",
    )})
    return save_claim(claim, workspace=tmp_path, directive="d")


def _measured_link(tmp_path: Path, key: str):
    report = trace_lesson_provenance(tmp_path, key)
    return next(lk for lk in report.links if lk.name == "measured")


def test_a_recorded_measurement_proves_the_link(tmp_path: Path) -> None:
    key = _saved_lesson(tmp_path)
    meas_id = record_lesson_measurement(
        tmp_path, key, instrument="test.critic", outcome="defect_absent")
    link = _measured_link(tmp_path, key)
    assert link.status == "PROVEN"
    assert f"measurement:{meas_id}" in link.refs


def test_a_ghost_measurement_ref_proves_nothing(tmp_path: Path) -> None:
    """A receipt naming a measurement the store does not hold stays ABSENT."""
    from core.causal_claim_store import LessonCard
    from core.lesson_provenance import record_lesson_injections

    key = _saved_lesson(tmp_path)
    card = LessonCard(rule="r", scope="s", directive="d", key=key)
    record_lesson_injections(
        tmp_path, (card,), consumer="test",
        measurement_ref="measurement:meas_ghost")
    assert _measured_link(tmp_path, key).status == "ABSENT"


def test_a_measurement_of_another_lesson_is_not_borrowed(tmp_path: Path) -> None:
    """The record's own lesson_key must match the traced lesson."""
    key = _saved_lesson(tmp_path)
    other_id = record_lesson_measurement(
        tmp_path, "cclaim_other", instrument="test.critic",
        outcome="defect_absent")
    from core.causal_claim_store import LessonCard
    from core.lesson_provenance import record_lesson_injections

    card = LessonCard(rule="r", scope="s", directive="d", key=key)
    record_lesson_injections(
        tmp_path, (card,), consumer="test",
        measurement_ref=f"measurement:{other_id}")
    assert _measured_link(tmp_path, key).status == "ABSENT"


def test_a_recurred_defect_is_still_a_measurement(tmp_path: Path) -> None:
    """Measurement proves measuring happened; the outcome is data, not shame."""
    key = _saved_lesson(tmp_path)
    meas_id = record_lesson_measurement(
        tmp_path, key, instrument="test.critic", outcome="defect_recurred")
    assert _measured_link(tmp_path, key).status == "PROVEN"
    store = (tmp_path / "data" / "lesson_measurements.jsonl").read_text(
        encoding="utf-8")
    rec = json.loads(store.splitlines()[0])
    payload = rec.get("payload", rec)
    assert payload["outcome"] == "defect_recurred"
    assert payload["id"] == meas_id


def _producer_run(tmp_path: Path, test_lines: list[str]):
    from core.self_task_producer import produce_coding_task

    target = "core/sample_module.py"
    (tmp_path / "core").mkdir(parents=True, exist_ok=True)
    (tmp_path / "core" / "sample_module.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "tests").mkdir(exist_ok=True)

    class _JsonLLM:
        def complete(self, *, system: str, user: str, **_kw) -> str:
            return json.dumps({
                "task_title": "t", "task_summary": "s",
                "impl_path": target,
                "test_path": "tests/test_sample_module_repro.py",
                "test_lines": test_lines,
                "confidence": 0.9,
            })

    class _Inbox:
        def list(self):
            return []

        def add(self, **kw):
            return SimpleNamespace(id="ain_meas_e2e")

    return produce_coding_task(
        workspace=tmp_path, inbox=_Inbox(), llm=_JsonLLM(),
        task_selector=lambda: SimpleNamespace(
            target_path=target,
            problem_quote="def add(a, b):",
            evidence_ref="verified_diagnosis:trace_x",
        ),
        source_kind="verified_diagnosis",
    )


def _measurements(tmp_path: Path) -> list[dict]:
    p = tmp_path / "data" / "lesson_measurements.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        out.append(rec.get("payload", rec))
    return out


def test_the_critic_measures_a_clean_generation_live(tmp_path: Path) -> None:
    """End to end: armed generation, no phantoms — defect_absent, full chain."""
    key = _saved_lesson(tmp_path)
    report = _producer_run(tmp_path, [
        "from core.sample_module import add",
        "",
        "def test_add_reproduces_the_defect():",
        "    assert add(1, 2) == 4",
    ])
    assert report.status == "proposed", report.reason
    rows = [m for m in _measurements(tmp_path) if m["lesson_key"] == key]
    assert rows and rows[0]["outcome"] == "defect_absent"
    assert _measured_link(tmp_path, key).status == "PROVEN"


def test_the_critic_measures_a_recurrence_live(tmp_path: Path) -> None:
    """Phantom kwargs survived the lesson: veto AND an honest recurrence row."""
    key = _saved_lesson(tmp_path)
    # The phantom sieve verifies against REAL importable signatures
    # (doubt = silence), so the invented kwarg rides a real repo class.
    report = _producer_run(tmp_path, [
        "from core.sample_module import add",
        "from core.causal_lesson import Observation",
        "",
        "def test_add_reproduces_the_defect():",
        ("    obs = Observation(episode_id='e', trace_id='t', run_id='r',"
         " invented_field=1)"),
        "    assert add(1, 2) == 4",
    ])
    assert report.status == "task_veto"
    rows = [m for m in _measurements(tmp_path) if m["lesson_key"] == key]
    assert rows and rows[0]["outcome"] == "defect_recurred"
    assert _measured_link(tmp_path, key).status == "PROVEN"


def test_a_skipped_instrument_writes_nothing(tmp_path: Path) -> None:
    """The test did not parse — the phantom check never ran, so no row."""
    key = _saved_lesson(tmp_path)
    report = _producer_run(tmp_path, [
        "from core.sample_module import add",
        "def test_broken(:",
    ])
    assert report.status == "task_veto"
    assert not [m for m in _measurements(tmp_path) if m["lesson_key"] == key]


def test_without_lessons_the_critic_measures_nothing(tmp_path: Path) -> None:
    _producer_run(tmp_path, [
        "from core.sample_module import add",
        "",
        "def test_add_reproduces_the_defect():",
        "    assert add(1, 2) == 4",
    ])
    assert not _measurements(tmp_path)
