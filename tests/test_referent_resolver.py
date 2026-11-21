"""Tests for core.referent_resolver (critique plan PR1)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.referent_resolver import (
    ArtifactRef,
    FileHintRef,
    MemoryRef,
    PriorTurnRef,
    ReferentResolver,
)

SESSION = "sess_test"
TURN = "turn_current"
OTHER_SESSION = "sess_other"
OTHER_TURN = "turn_old"


def _resolver(tmp_path: Path | None = None) -> ReferentResolver:
    return ReferentResolver(workspace_root=tmp_path)


def test_multi_artifact_only_relevant_becomes_primary(tmp_path: Path):
    target = tmp_path / "report.md"
    target.write_text("alpha beta gamma findings", encoding="utf-8")
    arts = (
        ArtifactRef(
            id="a_noise",
            session_id=SESSION,
            label="cache:noise.txt",
            tool="file_read",
            path=str(tmp_path / "noise.txt"),
            preview="unrelated weather notes",
        ),
        ArtifactRef(
            id="a_report",
            session_id=SESSION,
            label="cache:report.md",
            tool="file_read",
            path=str(target),
            preview="alpha beta gamma findings",
        ),
    )
    decision = ReferentResolver(workspace_root=tmp_path).resolve(
        "разбери findings в report.md",
        current_session_id=SESSION,
        current_turn_id=TURN,
        artifacts=arts,
    )
    assert decision.status in {"resolved", "needs_tool", "ambiguous"}
    # Noise must not win; report-related candidate must be present / primary.
    ids = [c.id for c in decision.candidates]
    assert "a_report" in ids
    if decision.primary is not None:
        assert decision.primary.id != "a_noise"


def test_stale_file_hint_other_turn_not_candidate(tmp_path: Path):
    f = tmp_path / "task.txt"
    f.write_text("task body", encoding="utf-8")
    decision = ReferentResolver(workspace_root=tmp_path).resolve(
        "выяви слабые стороны",
        current_session_id=SESSION,
        current_turn_id=TURN,
        file_hint=FileHintRef(path=str(f), turn_id=OTHER_TURN, session_id=SESSION),
    )
    assert all(c.kind != "file_hint" for c in decision.candidates)
    assert "stale_file_hint_other_turn" in decision.notes


def test_bare_string_file_hint_rejected_without_turn_scope(tmp_path: Path):
    f = tmp_path / "task.txt"
    f.write_text("x", encoding="utf-8")
    decision = ReferentResolver(workspace_root=tmp_path).resolve(
        "анализ",
        current_session_id=SESSION,
        current_turn_id=TURN,
        file_hint=str(f),
    )
    assert all(c.kind != "file_hint" for c in decision.candidates)


def test_cross_session_artifact_excluded(tmp_path: Path):
    decision = ReferentResolver(workspace_root=tmp_path).resolve(
        "проанализируй report.md",
        current_session_id=SESSION,
        current_turn_id=TURN,
        artifacts=(
            ArtifactRef(
                id="leak",
                session_id=OTHER_SESSION,
                label="report.md",
                path=str(tmp_path / "report.md"),
                preview="secret other session",
            ),
        ),
    )
    assert all(c.id != "leak" for c in decision.candidates)
    assert any(n.startswith("cross_session_artifact_excluded") for n in decision.notes)


def test_stale_artifact_ttl(tmp_path: Path):
    fixed = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    resolver = ReferentResolver(
        workspace_root=tmp_path,
        artifact_ttl_seconds=60,
        now=lambda: fixed,
    )
    decision = resolver.resolve(
        "разбери report.md findings",
        current_session_id=SESSION,
        current_turn_id=TURN,
        artifacts=(
            ArtifactRef(
                id="aged",
                session_id=SESSION,
                label="report.md",
                path=str(tmp_path / "report.md"),
                preview="findings alpha",
                created_at=fixed - timedelta(hours=2),
            ),
        ),
    )
    assert all(c.id != "aged" for c in decision.candidates)
    assert any(n.startswith("stale_artifact") for n in decision.notes)


def test_memory_never_silent_primary():
    decision = _resolver().resolve(
        "покажи слабые стороны этого",
        current_session_id=SESSION,
        current_turn_id=TURN,
        memory_hits=(MemoryRef(id="mem1", excerpt="old profile", relevance_score=0.99),),
        prior_turns=(
            PriorTurnRef(
                turn_id="turn_prev",
                session_id=SESSION,
                answer="Материал предыдущего хода.",
            ),
        ),
    )
    assert decision.primary is None or decision.primary.kind != "memory"
    assert any(n.startswith("memory_hit_ignored_as_primary") for n in decision.notes)


def test_path_traversal_rejected(tmp_path: Path):
    decision = ReferentResolver(workspace_root=tmp_path).resolve(
        "прочитай ../../etc/passwd",
        current_session_id=SESSION,
        current_turn_id=TURN,
        file_hint=FileHintRef(
            path="../../etc/passwd",
            turn_id=TURN,
            session_id=SESSION,
        ),
    )
    assert all(c.kind != "file_hint" for c in decision.candidates)


def test_empty_question_unresolved():
    decision = _resolver().resolve(
        "   ",
        current_session_id=SESSION,
        current_turn_id=TURN,
    )
    assert decision.status == "unresolved"
