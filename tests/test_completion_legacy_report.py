"""The legacy report diagnoses and never repairs.

A diagnostic that quietly wrote to a store would be worse than one that never
ran, so the first thing pinned here is that every store file comes out
byte-identical. The rest pins the distinctions the report exists to make:

*   a `lesson` admitted without completion is the AGREED exception, not an
    anomaly — it is counted on its own line and never in the anomaly list;
*   an anomaly is an unclassified record reaching a gate that requires a
    verdict, and the detector is proved to fire rather than assumed to;
*   a "safe backfill candidate" is a record the assembly table could classify
    from durable facts alone, which in practice means `replan_exhausted` and
    nothing else.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.smart_memory import (
    EpisodeRecord,
    EpisodicMemoryStore,
    ProcedureRecord,
    ProceduralMemoryStore,
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
def test_replan_exhaustion_is_the_only_backfillable_fact() -> None:
    report = build_report(
        [
            _episode("recoverable", replan=True, outcome="failed"),
            _episode("plain"),
            _episode("tagged-abort", tags=("aborted", "aborted:TypeError")),
            _episode("already-classified", completion="failed", replan=True),
        ],
        [],
    )

    assert report["backfill_candidates"] == ["recoverable"], (
        "an abort tag is derived and a missing declaration is unrecoverable; "
        "a record that already carries a verdict is not a candidate"
    )


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
