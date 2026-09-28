"""У процедуры должен быть НЕЗАВИСИМЫЙ след происхождения.

Замер, отвергнутые варианты и границы: MIR-165 в docs/audit/MASTER_ISSUE_REGISTRY.md.
"""
from __future__ import annotations

from core.smart_memory import (
    EpisodeRecord,
    ProceduralMemoryStore,
    ProcedureRecord,
    procedure_from_episode,
)

_QUESTION = (
    "почему подбор процедур впускает посторонние записи и что с этим делать"
)


def _episode(question: str = _QUESTION) -> EpisodeRecord:
    return EpisodeRecord(
        goal=question,
        question=question,
        outcome="success",
        summary="разобрано",
        tools_used=("file_read",),
        verified_chunks=2,
        tags=("episode", "success"),
        usage_eligible=True,
        completion_state="achieved",
    )


def test_a_procedure_names_the_question_it_was_minted_from() -> None:
    """Красный свидетель: происхождение переживает уборку эпизодов.

    Живой замер 2026-08-26: 29 процедур из 34 указывают `source_episode_ids`
    на эпизоды, которых в хранилище уже нет. Разметка, которой MIR-105 ждёт,
    чтобы выбрать порог, вымывается гигиеной.
    """
    proc = procedure_from_episode(_episode())

    assert proc is not None
    assert _QUESTION in proc.source_questions, (
        "вопрос-происхождение не сохранён отдельно — после вытеснения эпизода "
        "спросить, из чего сделана процедура, будет нечем"
    )


def test_the_origin_question_is_not_what_retrieval_scores(tmp_path) -> None:
    """Подбор не видит `source_questions`: слова только оттуда процедуру не находят.

    Иначе след — эхо стога подбора, и мерить подбор им значит мерить подбор им самим.
    """
    store = ProceduralMemoryStore(tmp_path / "procedural_memory.jsonl")
    store.rewrite([ProcedureRecord(
        name="workflow", workflow_key="tools:file_read",
        trigger_tags=("file_read",), steps=("Run tool: file_read",),
        source_questions=(_QUESTION,),
    )])
    assert store.search_with_report("file_read").procedures, "процедура вообще не находится"

    found = store.search_with_report(_QUESTION)

    assert found.procedures == [], (
        "происхождение попало в стог подбора — независимого свидетеля не стало"
    )
    assert found.rejected_by == {"no_overlap": 1}


def test_the_origin_survives_a_round_trip_through_the_store(tmp_path) -> None:
    """Поле бесполезно, если не переживает запись на диск."""
    store = ProceduralMemoryStore(tmp_path / "procedural_memory.jsonl")

    store.upsert_from_episode(_episode())
    loaded = store.load()

    assert loaded and _QUESTION in loaded[0].source_questions


def test_folding_in_episodes_does_not_grow_the_field_without_bound() -> None:
    """Запись уходит в подсказки планировщика, поэтому всё в ней ограничено."""
    proc = procedure_from_episode(_episode())
    assert proc is not None
    for i in range(40):
        proc = proc.merged_from_episode(_episode(f"совсем другой вопрос номер {i}"))

    assert len(proc.source_questions) <= 5, len(proc.source_questions)
    assert _QUESTION in proc.source_questions, (
        "первый вопрос — тот, из которого процедура и родилась; вытеснять "
        "надо поздние, иначе теряется само происхождение"
    )
