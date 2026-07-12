"""Tests for core.referent_resolver (critique plan PR1)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.referent_resolver import (
    FEATURE_FLAG,
    FEATURE_FLAG_DEFAULT,
    ArtifactRef,
    FileHintRef,
    MemoryRef,
    PriorTurnRef,
    ReferentResolver,
    artifacts_from_working_memory,
)


SESSION = "sess_test"
TURN = "turn_current"
OTHER_SESSION = "sess_other"
OTHER_TURN = "turn_old"


def _resolver(tmp_path: Path | None = None) -> ReferentResolver:
    return ReferentResolver(workspace_root=tmp_path)


def test_feature_flag_defaults_off():
    assert FEATURE_FLAG == "referent_resolver_v1"
    assert FEATURE_FLAG_DEFAULT is False


def test_anaphora_resolves_to_prior_turn():
    prior = PriorTurnRef(
        turn_id="turn_prev",
        session_id=SESSION,
        question="длинная формулировка про продукт",
        answer="Вот развёрнутый предыдущий ответ агента про продукт и риски.",
    )
    decision = _resolver().resolve(
        "покажи слабые стороны этого",
        current_session_id=SESSION,
        current_turn_id=TURN,
        prior_turns=(prior,),
    )
    assert decision.status == "resolved"
    assert decision.primary is not None
    assert decision.primary.kind == "prior_turn"
    assert decision.analysis_target_excerpt.startswith("Вот развёрнутый")
    # Directive stays separate from analysis material semantics.
    assert "покажи" in decision.directive_excerpt.casefold()


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


def test_irrelevant_cached_artifact_not_primary(tmp_path: Path):
    decision = ReferentResolver(workspace_root=tmp_path).resolve(
        "покажи слабые стороны формулировки только покажи",
        current_session_id=SESSION,
        current_turn_id=TURN,
        artifacts=(
            ArtifactRef(
                id="old",
                session_id=SESSION,
                label="cache:old.py",
                tool="file_read",
                path=str(tmp_path / "old.py"),
                preview="def hello(): pass",
            ),
        ),
        prior_turns=(
            PriorTurnRef(
                turn_id="turn_prev",
                session_id=SESSION,
                answer="Предыдущий текст для критики про формулировку пользователя.",
            ),
        ),
    )
    assert decision.primary is None or decision.primary.id != "old"
    if decision.status == "resolved":
        assert decision.primary is not None
        assert decision.primary.kind == "prior_turn"


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


def test_current_turn_file_hint_needs_tool(tmp_path: Path):
    f = tmp_path / "task.txt"
    f.write_text("task body", encoding="utf-8")
    decision = ReferentResolver(workspace_root=tmp_path).resolve(
        "выяви слабые стороны",
        current_session_id=SESSION,
        current_turn_id=TURN,
        file_hint=FileHintRef(path=str(f), turn_id=TURN, session_id=SESSION),
    )
    assert decision.status == "needs_tool"
    assert decision.primary is not None
    assert decision.primary.kind == "file_hint"
    # PR1 must not pretend the file was read / verified.
    assert "read_required" in decision.notes


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


def test_ambiguous_when_two_strong_different_kinds(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("body", encoding="utf-8")
    decision = ReferentResolver(workspace_root=tmp_path).resolve(
        "покажи слабые стороны этого и файла a.txt",
        current_session_id=SESSION,
        current_turn_id=TURN,
        file_hint=FileHintRef(path=str(f), turn_id=TURN, session_id=SESSION),
        prior_turns=(
            PriorTurnRef(
                turn_id="turn_prev",
                session_id=SESSION,
                answer="Длинный предыдущий ответ для анализа слабых сторон.",
            ),
        ),
    )
    # Path/hint vs prior_turn both strong → ambiguous, do not pick at random.
    assert decision.status == "ambiguous"
    assert decision.primary is None
    assert decision.conflict_reason


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


def test_user_text_material_is_data_channel_not_evidence_api():
    """Long critique of embedded text → user_text excerpt, no evidence write."""
    question = (
        "выяви слабые стороны: Система проигнорировала контекст и сделала "
        "категоричный вывод при нулевой подтверждённости. Ничего не делай, "
        "а только покажи."
    )
    decision = _resolver().resolve(
        question,
        current_session_id=SESSION,
        current_turn_id=TURN,
    )
    assert decision.status == "resolved"
    assert decision.primary is not None
    assert decision.primary.kind == "user_text"
    assert decision.primary.trust == "user_data"
    assert decision.analysis_target_excerpt
    # Resolver has no evidence-chain side effect by construction.
    assert not hasattr(decision, "evidence")


def test_artifacts_from_working_memory_adapter():
    refs = artifacts_from_working_memory(
        {
            "k1": {
                "tool": "file_read",
                "arguments": {"path": "docs/x.md"},
                "output": "hello world",
                "turn_index": 2,
            }
        },
        session_id=SESSION,
    )
    assert len(refs) == 1
    assert refs[0].session_id == SESSION
    assert refs[0].path == "docs/x.md"
    assert "hello" in refs[0].preview


def test_empty_question_unresolved():
    decision = _resolver().resolve(
        "   ",
        current_session_id=SESSION,
        current_turn_id=TURN,
    )
    assert decision.status == "unresolved"


def test_to_dict_omits_full_excerpt_body():
    decision = _resolver().resolve(
        "выяви слабые стороны: " + ("слово " * 30),
        current_session_id=SESSION,
        current_turn_id=TURN,
    )
    payload = decision.to_dict()
    assert "analysis_target_excerpt_chars" in payload
    assert "analysis_target_excerpt" not in payload
