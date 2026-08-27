"""Честно объявленная частичная работа с уликой — не мусор.

Замер, отвергнутые варианты и границы: MIR-169 в docs/audit/MASTER_ISSUE_REGISTRY.md.
"""
from __future__ import annotations

from core.smart_memory import (
    EpisodeRecord,
    EpisodicMemoryStore,
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


def test_a_verified_partial_run_is_admitted(tmp_path) -> None:
    """Красный свидетель: живой прогон 2026-08-27, 00:31 UTC.

    Первый безнадзорный выход агента. Он прочитал файлы, собрал ШЕСТЬ
    подтверждённых кусков улики, честно объявил цель достигнутой частично — и
    в память допущен НЕ был. Рядом, тем же прогоном, записался эпизод без
    единого подтверждения и вошёл по метке.

    Замер по 144 живым эпизодам: из 65 подтверждённых допущено 9 (14 %), из 79
    неподтверждённых — 65 (82 %). Ворота отбирают обратно сигналу.
    """
    assert decide_usage_eligibility(_episode()) is True


def test_the_signal_still_governs(tmp_path) -> None:
    """Послабление даёт СИГНАЛ, а не слово «частично».

    Отвергнут вариант «пускать всё частичное»: без подтверждённой улики
    частичный прогон — это рассказ о себе, ровно та саморечь, из-за которой
    82 % допущенных записей ничем не подтверждены.
    """
    assert decide_usage_eligibility(_episode(verified=0)) is False


def test_failure_and_blockage_stay_out(tmp_path) -> None:
    """Контроль: смягчена ОДНА оговорка, остальные на месте."""
    assert decide_usage_eligibility(_episode(completion="failed")) is False
    assert decide_usage_eligibility(_episode(completion="blocked")) is False
    assert decide_usage_eligibility(_episode(outcome="partial")) is False


def test_an_unclassified_row_still_steers_nothing(tmp_path) -> None:
    """Незнание остаётся закрытым: `None` — не «частично», а «не измеряли».

    29 из 55 отвергнутых подтверждённых записей — старые строки без оси
    завершённости. Пустить их значило бы принять незнание за суждение.
    """
    assert decide_usage_eligibility(_episode(completion=None)) is False


def test_a_full_run_outranks_a_partial_one_at_equal_relevance(tmp_path) -> None:
    """Впустить — не значит уравнять: завершённое идёт впереди частичного.

    Иначе свежая частичная запись обходила бы старую полную по одной свежести,
    и послабление превратилось бы в вытеснение.
    """
    store = EpisodicMemoryStore(tmp_path / "episodic_memory.jsonl")
    full = _episode(completion="achieved")
    partial = _episode(completion="partially_achieved")
    object.__setattr__(full, "created_at", "2026-08-01T00:00:00+00:00")
    object.__setattr__(partial, "created_at", "2026-08-20T00:00:00+00:00")
    store.save(full)
    store.save(partial)

    top = store.search(_QUERY, limit=1)

    assert top and top[0].completion_state == "achieved"


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
