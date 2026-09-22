"""Свой сбой становится дефектом реестра, а не только наблюдением.

Замер 2026-09-22: за день в реестре не появилось ни одной записи ОТ АГЕНТА —
все завёл Claude, — хотя его детекторы сработали десятки раз и легли в
data/causal_observations.jsonl. Сборщик смотрел только на эпизоды про
самоприменение. Без этой связи самопочинке не из чего брать работу.
"""
from __future__ import annotations

from pathlib import Path

from core.causal_lesson import Observation
from core.causal_store import CausalObservationStore
from core.defect_intake import intake_observations
from core.self_improvement_issues import DEFAULT_ISSUE_PATH, SelfImprovementIssueRegistry


def _store(tmp_path: Path) -> CausalObservationStore:
    return CausalObservationStore(tmp_path / "data" / "causal_observations.jsonl")


def _seen(tmp_path: Path, episode: str, times: int) -> str:
    """Записать наблюдение столько раз, сколько оно случилось."""
    store = _store(tmp_path)
    for _ in range(times):
        record = store.record(Observation(
            episode_id=episode, trace_id="t", run_id="r",
            defect_signals=("action_report_mismatch",),
            evidence_refs=("logs/trace_x.jsonl",),
            observed_mismatch="отчёт говорит «записал», file_write в ходе ноль"))
    return record.fingerprint


def test_a_repeated_signal_becomes_a_defect(tmp_path: Path) -> None:
    fingerprint = _seen(tmp_path, "ep-1", times=3)

    filed = intake_observations(tmp_path)

    assert filed == [fingerprint]
    issues = SelfImprovementIssueRegistry(tmp_path / DEFAULT_ISSUE_PATH).unresolved()
    assert len(issues) == 1
    assert "action_report_mismatch" in issues[0].title
    assert any("file_write" in e or fingerprint in e for e in issues[0].evidence)


def test_a_single_signal_is_not_a_defect_yet(tmp_path: Path) -> None:
    _seen(tmp_path, "ep-2", times=1)

    assert intake_observations(tmp_path) == []
    assert SelfImprovementIssueRegistry(tmp_path / DEFAULT_ISSUE_PATH).unresolved() == []


def test_the_same_signal_is_not_filed_twice(tmp_path: Path) -> None:
    _seen(tmp_path, "ep-3", times=3)
    intake_observations(tmp_path)

    intake_observations(tmp_path)

    assert len(SelfImprovementIssueRegistry(tmp_path / DEFAULT_ISSUE_PATH).unresolved()) == 1
