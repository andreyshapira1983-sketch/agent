"""The legacy report diagnoses and never repairs.

A diagnostic that quietly wrote to a store would be worse than one that never
ran, so the first thing pinned here is that every store file comes out
byte-identical. The rest pins the distinctions the report exists to make:

*   a `lesson` admitted without completion is the AGREED exception, not an
    anomaly — it is counted on its own line and never in the anomaly list;
*   an anomaly is an unclassified record reaching a gate that requires a
    verdict, and the detector is proved to fire rather than assumed to;
*   a "safe backfill candidate" is exactly what `scripts/completion_backfill.py`
    would write — the report delegates that decision instead of restating it,
    so the diagnostic cannot drift away from the migration it describes.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.smart_memory import (
    EpisodeRecord,
    EpisodicMemoryStore,
    ProceduralMemoryStore,
    ProcedureRecord,
)
from scripts.completion_legacy_report import build_report, main, render


def _episode(
    eid: str,
    *,
    completion: str | None = None,
    outcome: str = "success",
    tags: tuple[str, ...] = (),
    replan: bool = False,
    eligible: bool | None = True,
) -> EpisodeRecord:
    return EpisodeRecord(
        goal="g", question="q", outcome=outcome, summary="s",  # type: ignore[arg-type]
        tools_used=("file_read",), verified_chunks=3, unverified_chunks=0,
        answer_quality_score=1.0, full_answer="an answer",
        completion_state=completion, replan_exhausted=replan,  # type: ignore[arg-type]
        usage_eligible=eligible, tags=tags, id=eid,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def _procedure(pid: str, *, refs: tuple[str, ...], success: int = 2) -> ProcedureRecord:
    return ProcedureRecord(
        id=pid, name=f"Workflow {pid}", workflow_key=f"tools:{pid}",
        trigger_tags=(), steps=("Run tool",), source_episode_ids=refs,
        success_count=success, failure_count=0, confidence=0.75, status="active",
    )


def _seed(tmp_path: Path, episodes: list[EpisodeRecord],
          procedures: list[ProcedureRecord] | None = None) -> Path:
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    store = EpisodicMemoryStore(data / "episodic_memory.jsonl")
    for episode in episodes:
        store.save(episode)
    ProceduralMemoryStore(data / "procedural_memory.jsonl").rewrite(procedures or [])
    return tmp_path


def _fingerprints(workspace: Path) -> dict[str, tuple[str, int]]:
    """Content hash plus mtime for every file under the store directory."""
    out: dict[str, tuple[str, int]] = {}
    for path in sorted((workspace / "data").rglob("*")):
        if path.is_file():
            out[path.name] = (
                hashlib.sha256(path.read_bytes()).hexdigest(),
                path.stat().st_mtime_ns,
            )
    return out


# ==========================================================================
# Read-only, proved on bytes.
# ==========================================================================
def test_running_the_report_changes_no_byte(tmp_path: Path, capsys) -> None:
    workspace = _seed(
        tmp_path,
        [_episode("ep-1"), _episode("ep-2", tags=("lesson",), outcome="failed")],
        [_procedure("p1", refs=("ep-1",))],
    )
    before = _fingerprints(workspace)

    assert main(["--workspace", str(workspace)]) == 0

    assert _fingerprints(workspace) == before, "a diagnostic must not write"
    assert "stores unchanged" in capsys.readouterr().out


def test_empty_stores_do_not_crash(tmp_path: Path, capsys) -> None:
    (tmp_path / "data").mkdir()

    assert main(["--workspace", str(tmp_path)]) == 0
    assert "episodes: 0" in capsys.readouterr().out


def test_absent_stores_do_not_crash(tmp_path: Path) -> None:
    assert main(["--workspace", str(tmp_path)]) == 0


def test_the_report_is_deterministic() -> None:
    episodes = [_episode("ep-1"), _episode("ep-2", completion="achieved")]

    assert build_report(episodes, []) == build_report(episodes, [])


# ==========================================================================
# The gates are reported apart, and the lesson arm is not an anomaly.
# ==========================================================================
def test_a_legacy_lesson_is_an_expected_admission_not_an_anomaly() -> None:
    report = build_report([_episode("les", tags=("lesson",), outcome="failed")], [])

    assert report["gates"]["lesson context eligibility"] == 1
    assert report["gates"]["positive episodic reuse"] == 0
    assert report["gates"]["episodic fast-path"] == 0
    assert report["anomalies"] == {}, (
        "a curated lesson ignores completion by design; calling that an "
        "anomaly would train the reader to skip the section"
    )


def test_the_lesson_arm_reports_what_retrieval_admits_not_what_the_policy_says() -> None:
    """The report models RETRIEVAL, and retrieval reads the STORED bit.

    `decide_usage_eligibility` is the banking-time policy: for a lesson it
    returns True regardless of the stored `usage_eligible`. Retrieval calls
    `is_usage_eligible`, which admits only an explicit True — so a legacy
    lesson, whose bit was never set, is refused. Reporting the policy's answer
    said 108 legacy lessons were admitted when the live agent admits none, and
    a diagnostic that overstates what memory is reachable is worse than no
    diagnostic.
    """
    unclassified = _episode("legacy-lesson", tags=("lesson",), outcome="failed",
                            eligible=None)
    admitted = _episode("live-lesson", tags=("lesson",), outcome="failed",
                        eligible=True)

    report = build_report([unclassified, admitted], [])

    assert report["gates"]["lesson context eligibility"] == 1, (
        "a legacy lesson carries no eligibility bit and retrieval refuses it"
    )
    assert report["anomalies"] == {}


def test_the_stored_eligibility_bit_is_reported() -> None:
    """It is what actually withholds legacy memory, so it has to be visible."""
    report = build_report(
        [_episode("a", eligible=None), _episode("b", eligible=False),
         _episode("c", eligible=True)],
        [],
    )

    assert report["usage_eligible"] == {"<missing>": 1, "False": 1, "True": 1}
    assert "STORED eligibility bit" in render(report)


def test_an_achieved_episode_reaches_the_positive_gates() -> None:
    report = build_report([_episode("ok", completion="achieved")], [])

    assert report["gates"]["positive episodic reuse"] == 1
    assert report["gates"]["procedural credit"] == 1
    assert report["anomalies"] == {}


def test_the_anomaly_detector_actually_fires(monkeypatch) -> None:
    """Proved, not assumed: with the gates as written an unclassified record
    cannot reach them, which is exactly why the tripwire needs its own test."""
    monkeypatch.setattr(
        "scripts.completion_legacy_report._passes_positive_reuse", lambda _e: True
    )

    report = build_report([_episode("leaky")], [])

    assert report["anomalies"]["positive episodic reuse"] == ["leaky"]
    assert "!! positive episodic reuse" in render(report)


# ==========================================================================
# Migration input.
# ==========================================================================
def test_a_proved_writer_signature_is_the_only_backfillable_fact() -> None:
    """`replan_exhausted` is durable but not sufficient — and not implemented.

    The cycle's assembly table needs `aborted_reason` and a declaration as well,
    and neither was ever persisted, so no migration can act on `replan_exhausted`
    alone. Counting it as a candidate would advertise a migration that does not
    exist. What does qualify is a row carrying a writer's full signature.
    """
    writer_row = EpisodeRecord(
        goal="produce self-build patch for cli/intent_bridge.py",
        question="self-task-produce", outcome="success", summary="s",
        tags=("self-build", "lesson", "self-task-produce", "success"),
        id="writer-banked",
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    report = build_report(
        [
            writer_row,
            _episode("replan-only", replan=True, outcome="failed"),
            _episode("plain"),
            _episode("aborted", tags=("aborted:budget",)),
            _episode("settled", completion="achieved"),
        ],
        [],
    )

    assert report["backfill_candidates"] == ["writer-banked"], (
        "only a proved writer signature is recoverable; an abort tag is derived, "
        "a missing declaration is unrecoverable, `replan_exhausted` alone has no "
        "migration behind it, and a record that already carries a verdict is not "
        "a candidate"
    )


def test_the_report_and_the_migration_never_disagree() -> None:
    """One decision, called twice — not two rules that happen to match today."""
    from scripts.completion_backfill import writer_backfill_verdict
    from scripts.completion_legacy_report import _backfill_candidate

    rows = [
        EpisodeRecord(
            goal="produce self-build patch for cli/intent_bridge.py",
            question="self-task-build", outcome="partial", summary="s",
            tags=("self-build", "lesson", "self-task-build", "partial"), id="a",
        ),
        EpisodeRecord(
            goal="repair", question="fix core/loop_methods2.py", outcome="success",
            summary="s", tags=("lesson", "bug-fix", "regression-guard"), id="b",
        ),
        _episode("replan-only", replan=True, outcome="failed"),
        _episode("settled", completion="achieved"),
        _episode("plain"),
    ]

    for row in rows:
        assert _backfill_candidate(row) is (
            writer_backfill_verdict(row.to_dict()) is not None
        ), f"report and migration disagree about {row.id}"


def test_the_live_shape_reports_zero_candidates() -> None:
    """The store's real shape: nothing carries a durable fact to classify."""
    report = build_report([_episode(f"ep-{i}") for i in range(5)], [])

    assert report["backfill_candidates"] == []
    assert "safe episode backfill candidates: 0" in render(report)


# ==========================================================================
# Procedural counters — association, never attribution.
# ==========================================================================
def test_procedures_are_counted_by_legacy_reference_not_by_blame() -> None:
    episodes = [_episode("ep-legacy"), _episode("ep-done", completion="achieved")]
    procedures = [
        _procedure("p-legacy", refs=("ep-legacy",)),
        _procedure("p-clean", refs=("ep-done",)),
        _procedure("p-zero", refs=("ep-legacy",), success=0),
    ]

    report = build_report(episodes, procedures)

    ids = {p["id"] for p in report["procedures_with_legacy_refs"]}
    assert ids == {"p-legacy"}, (
        "only a procedure with BOTH non-zero counters and a legacy reference "
        "qualifies; a zero-counter procedure has nothing unverifiable yet"
    )


def test_references_to_evicted_episodes_are_reported_as_missing() -> None:
    report = build_report(
        [_episode("ep-legacy")],
        [_procedure("p", refs=("ep-legacy", "ep-evicted-1", "ep-evicted-2"))],
    )

    entry = report["procedures_with_legacy_refs"][0]
    assert (entry["refs_total"], entry["refs_legacy"], entry["refs_missing"]) == (3, 1, 2)


def test_the_report_states_the_limit_of_attribution() -> None:
    text = render(build_report([_episode("ep-1")], [_procedure("p", refs=("ep-1",))]))

    assert "NOT attributable to individual episodes" in text
    assert "no per-increment ledger exists" in text


@pytest.mark.parametrize("word", ["contaminat", "corrupt", "poison"])
def test_the_report_does_not_overclaim(word: str) -> None:
    """Proved is "these counters cannot be verified", not "these are bad"."""
    text = render(build_report([_episode("ep-1")], [_procedure("p", refs=("ep-1",))]))

    assert word not in text.lower()


# ==========================================================================
# MIR-058: the instrument must not read clean because the evidence rotted.
# ==========================================================================
#
# Measured 2026-08-22: the live report printed "0 of 34" while 10 procedures
# still carried non-zero counters and 70 of their 75 source references (93%)
# pointed at episodes the FIFO had evicted. `legacy_ids` is built from episodes
# PRESENT in the store, so an evicted episode cannot be legacy by construction,
# and `if not legacy_refs: continue` dropped the procedure from the report
# together with its own `refs_missing` count. The headline converged to zero
# exactly as the credit became LESS verifiable. A sensor whose reading improves
# as the thing it measures gets worse is worse than no sensor.


def test_evicted_evidence_reads_as_unexaminable_not_as_clean() -> None:
    """The witness for the live store's exact state: counters whose every
    source reference points at nothing must appear in the report."""
    episodes: list = []  # the FIFO already evicted everything
    procs = [_procedure("p1", refs=("gone-1", "gone-2", "gone-3"), success=5)]

    report = build_report(episodes, procs)

    flagged = report["procedures_with_legacy_refs"]
    assert flagged, (
        "a procedure with success=5 and every reference evicted vanished from "
        "the report — zero-because-evicted read as zero-because-clean"
    )
    assert flagged[0]["refs_missing"] == 3
    assert flagged[0]["refs_legacy"] == 0


def test_a_clean_procedure_is_still_not_flagged() -> None:
    """Zero-because-clean must stay zero: classified, resolvable evidence is
    exactly what the policy wants, and flagging it would cry wolf."""
    episodes = [_episode("e1", completion="achieved")]
    procs = [_procedure("p1", refs=("e1",), success=2)]

    report = build_report(episodes, procs)

    assert report["procedures_with_legacy_refs"] == []


def test_the_two_zeros_are_distinguishable_in_the_rendered_text(capsys) -> None:
    """The number is read by a human; the words must carry the split. A reader
    of the old report could not tell 'nothing unverifiable' from 'the evidence
    is gone' — that is the difference the live store fell into."""
    procs = [_procedure("p1", refs=("gone-1",), success=1)]

    text = render(build_report([], procs))

    # The static prose always mentions "missing", so that word alone proves
    # nothing (this test first passed unfixed for exactly that reason). The
    # procedure's own line must be printed, and the headline must carry the
    # evicted split.
    assert "tools:p1" in text, (
        "the affected procedure is not in the rendered report — the reader "
        "cannot tell zero-because-clean from zero-because-evicted"
    )
    assert "evicted" in text.lower()


def test_a_mixed_procedure_reports_both_counts() -> None:
    episodes = [_episode("e1", completion=None)]  # present, unclassified
    procs = [_procedure("p1", refs=("e1", "gone-1"), success=3)]

    report = build_report(episodes, procs)

    flagged = report["procedures_with_legacy_refs"]
    assert flagged and flagged[0]["refs_legacy"] == 1
    assert flagged[0]["refs_missing"] == 1


def test_zero_counter_procedures_stay_out_even_with_missing_refs() -> None:
    """No credit, nothing to verify: the report is about unverifiable STANDING,
    not about reference hygiene."""
    procs = [_procedure("p1", refs=("gone-1",), success=0)]

    report = build_report([], procs)

    assert report["procedures_with_legacy_refs"] == []
