"""У процедуры должен быть НЕЗАВИСИМЫЙ след происхождения.

Замер, отвергнутые варианты и границы: MIR-165 в docs/audit/MASTER_ISSUE_REGISTRY.md.
"""
from __future__ import annotations

from core.smart_memory import (
    EpisodeRecord,
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
