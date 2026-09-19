"""The causal-provenance meter: a lesson's use is a chain of receipts, not prose.

Operator ruling 2026-08-17 (after the exam replay measured the gap live):
before any "organ" is built, build the METER. For a lesson key it must
assemble the chain derived_from -> injected -> acted -> measured, where every
link is PROVEN only by an independent machine receipt, prose inside the claim
itself reaches at most SELF_DECLARED, and a link with nothing behind it is
ABSENT. A full chain of receipts proves PROVENANCE, never EFFECT (operator
ruling 2026-08-17: the action could have been clean without the lesson —
effect needs a differentiating experiment), so the top verdict says
exactly that; anything less says CAUSAL USE NOT PROVEN and names the
missing links. No creativity.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.causal_claim_store import save_claim
from core.causal_lesson import CausalClaim, Intervention, Observation
from core.lesson_provenance import trace_lesson_provenance


def _mk_claim(
    tmp_path: Path,
    *,
    episode_id: str = "ep-x",
    intervention: Intervention | None = None,
) -> str:
    claim = CausalClaim(
        observation=Observation(
            episode_id=episode_id,
            trace_id="tr_test",
            run_id="run_origin",
            defect_signals=("phantom_signature_kwargs",),
            evidence_refs=("approval:ain_test1",),
        ),
        intervention=intervention,
    )
    return save_claim(claim, workspace=tmp_path)


def _write_episode(tmp_path: Path, episode_id: str) -> None:
    rec = {"payload": {"id": episode_id, "run_id": "run_a", "outcome": "partial"}}
    p = tmp_path / "data" / "episodic_memory.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def _write_injection(tmp_path: Path, lesson_key: str, **extra) -> None:
    rec = {"lesson_key": lesson_key, "run_id": "run_b",
           "consumer": "self_task_producer", "ts": "2026-08-17T00:00:00+00:00"}
    rec.update(extra)
    p = tmp_path / "data" / "lesson_injections.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def _link(report, name: str):
    return next(link for link in report.links if link.name == name)


def test_an_unknown_lesson_is_not_invented(tmp_path: Path) -> None:
    report = trace_lesson_provenance(tmp_path, "cclaim_missing")
    assert report.verdict == "CAUSAL USE NOT PROVEN"
    assert report.state == "not_found"
    assert not report.links


def test_a_named_but_missing_episode_is_self_declared(tmp_path: Path) -> None:
    key = _mk_claim(tmp_path, episode_id="ep-never-recorded")
    report = trace_lesson_provenance(tmp_path, key)
    assert _link(report, "derived_from").status == "SELF_DECLARED"
    assert report.verdict == "CAUSAL USE NOT PROVEN"


def test_a_recorded_episode_proves_derivation(tmp_path: Path) -> None:
    _write_episode(tmp_path, "ep-real")
    key = _mk_claim(tmp_path, episode_id="ep-real")
    report = trace_lesson_provenance(tmp_path, key)
    assert _link(report, "derived_from").status == "PROVEN"


def test_without_an_injection_journal_the_link_is_absent(tmp_path: Path) -> None:
    """Today's live truth: nothing journals delivery, so nothing can prove it."""
    key = _mk_claim(tmp_path)
    report = trace_lesson_provenance(tmp_path, key)
    link = _link(report, "injected")
    assert link.status == "ABSENT"
    assert "journal" in link.detail
    assert "injected" in report.missing


def test_an_injection_receipt_proves_delivery(tmp_path: Path) -> None:
    key = _mk_claim(tmp_path)
    _write_injection(tmp_path, key)
    report = trace_lesson_provenance(tmp_path, key)
    assert _link(report, "injected").status == "PROVEN"


def test_a_receipt_for_another_lesson_proves_nothing(tmp_path: Path) -> None:
    key = _mk_claim(tmp_path)
    _write_injection(tmp_path, "cclaim_other")
    report = trace_lesson_provenance(tmp_path, key)
    assert _link(report, "injected").status == "ABSENT"


def test_prose_inside_the_claim_cannot_prove_action(tmp_path: Path) -> None:
    """intervention text naming a commit stays the claim's own assertion."""
    key = _mk_claim(tmp_path, intervention=Intervention(
        mutated="added check (commit deadbee)",
        predicted="phantoms stop",
        observed="meter says it held",
    ))
    report = trace_lesson_provenance(tmp_path, key)
    assert _link(report, "acted").status == "SELF_DECLARED"
    assert _link(report, "measured").status == "SELF_DECLARED"
    assert report.verdict == "CAUSAL USE NOT PROVEN"


def test_an_action_receipt_on_the_injection_proves_the_act(tmp_path: Path) -> None:
    key = _mk_claim(tmp_path)
    _write_injection(tmp_path, key, action_ref="task:tsk_1", measurement_ref="episode:ep-after")
    _write_episode(tmp_path, "ep-after")
    report = trace_lesson_provenance(tmp_path, key)
    assert _link(report, "acted").status == "PROVEN"
    assert _link(report, "measured").status == "PROVEN"


def test_a_measurement_ref_must_itself_resolve(tmp_path: Path) -> None:
    """A receipt pointing at a non-existent episode proves nothing."""
    key = _mk_claim(tmp_path)
    _write_injection(tmp_path, key, action_ref="task:tsk_1", measurement_ref="episode:ep-ghost")
    report = trace_lesson_provenance(tmp_path, key)
    assert _link(report, "measured").status == "ABSENT"


def test_the_full_chain_proves_provenance_never_effect(tmp_path: Path) -> None:
    """Even a complete chain of receipts must not claim causal effect: the
    action could have been clean without the lesson."""
    _write_episode(tmp_path, "ep-origin")
    key = _mk_claim(tmp_path, episode_id="ep-origin")
    _write_injection(tmp_path, key, action_ref="task:tsk_1", measurement_ref="episode:ep-after")
    _write_episode(tmp_path, "ep-after")
    report = trace_lesson_provenance(tmp_path, key)
    assert report.verdict == "PROVENANCE PROVEN — CAUSAL EFFECT UNPROVEN", [
        (link.name, link.status, link.detail) for link in report.links
    ]
    assert not report.missing


def test_one_missing_link_breaks_the_whole_verdict(tmp_path: Path) -> None:
    _write_episode(tmp_path, "ep-origin")
    key = _mk_claim(tmp_path, episode_id="ep-origin")
    _write_injection(tmp_path, key, action_ref="task:tsk_1")  # no measurement_ref
    report = trace_lesson_provenance(tmp_path, key)
    assert report.verdict == "CAUSAL USE NOT PROVEN"
    assert "measured" in report.missing


def test_the_meter_has_a_live_door(tmp_path: Path, capsys) -> None:
    """`:causal provenance` renders the chain and journals the measurement."""
    from cli.commands_causal import _handle_causal

    key = _mk_claim(tmp_path)
    logged: list[tuple[str, dict]] = []

    class _Log:
        def log(self, event: str, payload: dict) -> None:
            logged.append((event, payload))

    class _Agent:
        workspace = str(tmp_path)
        log = _Log()

    assert _handle_causal(f"provenance {key}", _Agent()) is True
    err = capsys.readouterr().err
    assert "CAUSAL USE NOT PROVEN" in err
    assert key in err
    assert logged and logged[0][0] == "lesson_provenance_measured"
    assert logged[0][1]["verdict"] == "CAUSAL USE NOT PROVEN"
