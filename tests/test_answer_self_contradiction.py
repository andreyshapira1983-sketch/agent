"""Утверждение не может быть Фактом и Непроверенным в одном ответе.

ЖИВОЙ СЛУЧАЙ 2026-08-10. Ответ доказывал существование системы и писал в
разделе Facts: «В ходе текущего выполнения (trace_id run_860e…) записано 92
события», — а в разделе Unverified: «Не доказано, что trace_id run_860e…
соответствует текущему исполнению». Оба раздела прошли, никто их не сверил.

Дальше — цена. Верификация считает разрешимость ССЫЛКИ, а не истинность
референта (MIR-060), поэтому 14 из 16 подтвердились, качество вышло 1.0,
эпизод стал пригоден к воспроизведению и породил новую процедуру. Ложь попала
в обучение не в обход проверок, а ЧЕРЕЗ них.

Поэтому чинится не финальный балл, а причинная цепь: обнаружение
противоречия -> рубеж принятия ответа -> сигнал дефекта -> допуск в обучение.
"""
from __future__ import annotations

import pytest

from core.answer_contradiction import contradicted_claims
from core.smart_memory import EpisodeRecord, decide_usage_eligibility
from core.unsupported_claims import apply_answer_enforcement

_CONTRADICTORY = (
    "Conclusion: система исполняется. [log]\n"
    "Facts:\n"
    "- В ходе текущего выполнения (trace_id run_860e7ef) записано 92 события. [log]\n"
    "- Пользователь andre на хосте andre. [shell]\n"
    "Sources:\n1. log - журнал\n2. shell - оболочка\n"
    "Confidence: high\n"
    "Unverified:\n"
    "- Не доказано, что trace_id run_860e7ef соответствует текущему исполнению.\n"
)
_CLEAN = (
    "Conclusion: система исполняется. [log]\n"
    "Facts:\n- Пользователь andre на хосте andre. [shell]\n"
    "Sources:\n1. shell - оболочка\n"
    "Confidence: high\n"
    "Unverified:\n- Сознание не доказано и доступными средствами не измеряется.\n"
)


class _Report:
    total_chunks = 16
    verified_chunks = 14
    unverified_chunks = 0
    cited_but_unmatched_chunks = 0
    self_declared_chunks = 2
    structural_chunks = 101
    topic_supported_but_claim_unverified_chunks = 0
    subagent_asserted_chunks = 0
    receipt_missing_chunks = 0
    dialogue_supported_chunks = 0
    user_asserted_chunks = 0
    chain_was_empty = False
    fully_unverified = False
    malformed_output = False
    chunks: tuple = ()


def test_the_contradiction_is_found() -> None:
    """ГЛАВНОЕ: тот самый ответ, на том самом предмете."""
    found = contradicted_claims(_CONTRADICTORY)
    assert found, "утверждение стоит и в Facts, и в Unverified — и это не замечено"
    assert any("run_860e7ef" in c.subject for c in found)


def test_a_clean_answer_is_not_accused() -> None:
    """Защита от вырождения: раздел Unverified — норма, а не улика.

    Честный ответ обязан называть, чего он не доказал. Если бы детектор
    срабатывал на само наличие раздела, он запретил бы честность.
    """
    assert not contradicted_claims(_CLEAN)


@pytest.mark.parametrize("answer", ["", "просто текст без разделов", None])
def test_a_answer_without_sections_is_not_adjudicated(answer) -> None:
    """Нет разделов — нет очной ставки; выдумывать предмет спора нельзя."""
    assert not contradicted_claims(answer)


def test_enforcement_names_the_contradiction_before_acceptance() -> None:
    """Рубеж принятия: ответ не проходит молча."""
    result = apply_answer_enforcement(
        answer=_CONTRADICTORY, report=_Report(), question="докажи что ты существуешь"
    )
    assert result.outcome == "self_contradicted"
    assert result.contradictions
    assert "run_860e7ef" in result.answer, "предмет спора обязан быть назван оператору"


def test_enforcement_does_not_delete_the_answer() -> None:
    """Противоречие — повод пометить, а не уничтожить работу.

    Удаление ответа лишило бы оператора и верной его части, и самого предмета
    спора; заметка сохраняет обе.
    """
    result = apply_answer_enforcement(
        answer=_CONTRADICTORY, report=_Report(), question="докажи"
    )
    assert "92 события" in result.answer
    assert len(result.answer) > len(_CONTRADICTORY)


def test_a_clean_answer_passes_enforcement_unchanged() -> None:
    result = apply_answer_enforcement(
        answer=_CLEAN, report=_Report(), question="докажи что ты существуешь"
    )
    assert result.outcome != "self_contradicted"
    assert not result.contradictions


def _episode(*, defect_signals: tuple[str, ...]) -> EpisodeRecord:
    """Эпизод, который БЕЗ сигнала дефекта был бы допущен по всем осям."""
    return EpisodeRecord(
        goal="докажи что ты существуешь",
        question="докажи что ты существуешь",
        outcome="success",
        summary="доказательство существования",
        full_answer=_CONTRADICTORY,
        completion_state="achieved",
        verified_chunks=14,
        unverified_chunks=0,
        answer_quality_score=1.0,
        tools_used=["shell_exec"],
        source_labels=["shell:whoami"],
        defect_signals=list(defect_signals),
    )


def test_an_episode_without_the_signal_is_still_admitted() -> None:
    """ПРЕДУСЛОВИЕ: иначе следующий тест зелен по постороннему поводу."""
    assert decide_usage_eligibility(_episode(defect_signals=()))


def test_a_self_contradicted_episode_never_becomes_reusable() -> None:
    """Конец причинной цепи: противоречие не имеет права стать опытом.

    Все прежние оси у эпизода зелёные — outcome success, completion achieved,
    verified_chunks 14, качество 1.0. Ровно так ложь и прошла в обучение:
    не в обход правил, а по ним.
    """
    assert not decide_usage_eligibility(
        _episode(defect_signals=("self_contradiction",))
    )
