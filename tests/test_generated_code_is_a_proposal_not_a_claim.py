"""Рождённый код — предложение, не утверждение: его судья — тесты, не цитаты.

Болезнь найдена серией «учимся программировать себя» (2026-08-29, прогоны
run_e4d9a8e8 и далее): агент спроектировал свой первый тест — и его же страж
улик задушил код («27 непроверенных утверждений… скрыто»). У нового кода
улик нет ПО ОПРЕДЕЛЕНИЮ — он новый; стена, построенная для ответов на
вопросы о мире, душила творчество.

Три разреза (слово оператора «начни лечить»):
1. резак держит кодовый забор атомарным куском — блок не шинкуется на
   «строки-заявления»;
2. классификатор знает код: забор — вне арифметики улик;
3. подавитель бедных на улики ответов ПРОПУСКАЕТ код-предложения отдельной
   секцией — гасится только проза без опоры.

Красные свидетели: до починки резак шинковал забор, классификатор считал
код заявлением, подавитель хоронил его вместе с прозой.
"""
from __future__ import annotations

from types import SimpleNamespace

from core.low_evidence_policy import evaluate_low_evidence_policy
from core.verifier_utils import is_structural_chunk, split_into_chunks

_FENCED = (
    "```python\n"
    "def test_refusal_reaches_episode():\n"
    "    episode = build_episode(outcome='success', product='declined')\n"
    "    assert episode.product == 'declined'\n"
    "```"
)


def test_a_fenced_block_stays_one_atomic_chunk() -> None:
    answer = "Вот тест, который я предлагаю.\n" + _FENCED + "\nОн падает сегодня."
    chunks = split_into_chunks(answer)

    assert _FENCED in chunks, (
        "кодовый забор нашинкован на строки-заявления — "
        "живое удушение run_e4d9a8e8 вернулось")
    assert "Вот тест, который я предлагаю." in chunks
    assert "Он падает сегодня." in chunks


def test_code_is_outside_the_evidence_arithmetic() -> None:
    assert is_structural_chunk(_FENCED) is True


def test_prose_is_still_a_claim() -> None:
    """Граница: проза вокруг кода осталась под проверкой."""
    assert is_structural_chunk("Этот модуль уже течёт в проде.") is False


def test_suppression_spares_the_code_proposal() -> None:
    """Ядро: при усечении за бедность улик код переживает чистку."""
    answer = (
        "Планировщик стал медленнее из-за кэша.\n"
        "Это видно по замерам за неделю.\n"
        "Виноват модуль планировщика.\n"
        "Ниже тест, который это докажет.\n"
        "Он должен падать до починки.\n"
        "После починки он зеленеет.\n" + _FENCED
    )
    report = SimpleNamespace(
        total_chunks=9, verified_chunks=0, dialogue_supported_chunks=0,
        unverified_chunks=9, cited_but_unmatched_chunks=0,
        topic_supported_but_claim_unverified_chunks=0,
        subagent_asserted_chunks=0, user_asserted_chunks=0, refuted_chunks=0,
        chunks=(),
    )

    result = evaluate_low_evidence_policy(answer=answer, report=report)

    assert result.triggered, "фикстура обязана включать подавитель"
    assert "```" in result.answer and "declined" in result.answer, (
        "код-предложение похоронено вместе с прозой — стена снова душит "
        "творчество")
    assert "Планировщик стал медленнее" not in result.answer, (
        "граница: бездоказательная проза обязана гаситься как прежде")


def test_an_answer_without_code_suppresses_exactly_as_before() -> None:
    """Граница: у ответа без забора ничего не меняется."""
    answer = ("Одно. Второе. Третье. Четвёртое. Пятое. Шестое. "
              "Седьмое. Восьмое. Девятое.")
    report = SimpleNamespace(
        total_chunks=9, verified_chunks=0, dialogue_supported_chunks=0,
        unverified_chunks=9, cited_but_unmatched_chunks=0,
        topic_supported_but_claim_unverified_chunks=0,
        subagent_asserted_chunks=0, user_asserted_chunks=0, refuted_chunks=0,
        chunks=(),
    )

    result = evaluate_low_evidence_policy(answer=answer, report=report)

    assert result.triggered
    assert "```" not in result.answer
