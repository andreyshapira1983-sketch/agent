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


def test_the_origin_question_is_not_what_retrieval_scores() -> None:
    """След должен быть НЕЗАВИСИМЫМ, иначе это эхо, а не свидетель.

    Обрезанный вопрос уже лежит в `steps[0]`, а его токены — в `trigger_tags`,
    то есть внутри стога, по которому идёт подбор. Мерить качество подбора по
    такому следу — мерить подбор им самим.
    """
    base = ProcedureRecord(
        name="workflow", workflow_key="tools:file_read",
        trigger_tags=("file_read",), steps=("Run tool: file_read",),
    )
    with_origin = ProcedureRecord(
        name="workflow", workflow_key="tools:file_read",
        trigger_tags=("file_read",), steps=("Run tool: file_read",),
        source_questions=(_QUESTION,),
    )
    haystack_of = lambda p: " ".join(  # noqa: E731
        [p.name, " ".join(p.trigger_tags), " ".join(p.steps)]
    )

    assert haystack_of(base) == haystack_of(with_origin), (
        "происхождение попало в стог подбора — независимого свидетеля не стало"
    )


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
