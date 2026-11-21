"""Честно объявленная частичная работа с уликой — не мусор.

Замер, отвергнутые варианты и границы: MIR-169 в docs/audit/MASTER_ISSUE_REGISTRY.md.
"""
from __future__ import annotations

from core.smart_memory import (
    EpisodeRecord,
    decide_usage_eligibility,
)

_QUERY = "проследить пути замены записей в модуле памяти и его местах вызова"


def _episode(
    *,
    completion: str | None = "partially_achieved",
    outcome: str = "success",
    verified: int = 6,
    tags: tuple[str, ...] = ("episode", "success"),
    goal: str = _QUERY,
) -> EpisodeRecord:
    return EpisodeRecord(
        goal=goal,
        question=goal,
        outcome=outcome,
        summary="прочитал места вызова, дефект не воспроизвёл",
        tools_used=("file_read",),
        verified_chunks=verified,
        tags=tags,
        completion_state=completion,
    )


def test_a_demoted_run_is_not_an_honest_partial(tmp_path) -> None:
    """«Частично» бывает двух происхождений, и различитель уже в записи.

    Собственная честная оценка — одно. Принудительное понижение, когда
    авторитетный сигнал вытеснил утверждение прогона (`completion_override`),
    — совсем другое: так решил оператор, и впустить такую запись как честную
    частичность значило бы отменить его правило.

    Поймано не мной: этот изъян в первой версии правки нашёл существующий тест
    `test_answer_enforcement_failure`.
    """
    demoted = _episode()
    object.__setattr__(demoted, "completion_override", "answer_enforcement_failed")

    assert decide_usage_eligibility(demoted) is False
    assert decide_usage_eligibility(_episode()) is True
