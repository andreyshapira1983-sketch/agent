"""«В улике нет этих слов» — не то же, что «источника не существует».

Продолжение правки от 2026-09-21, и оно исправляет её же первую редакцию.

Час назад `cited_literal_absent` увели из вердикта `refuted` — правильно:
отсутствие слов в вырезке не доказывает лжи. Но приземлили его в счётчик
`cited_but_unmatched_chunks`, а `core/unsupported_claims.py` читает ИМЕННО
этот счётчик как ФАБРИКАЦИЮ цитат:

    fabricated = int(report.cited_but_unmatched_chunks or 0)

Фабрикация терминальна: ответ не отправляется вовсе. Живая цена — два
ответа подряд, уничтоженных целиком. Человек дважды получил канцелярскую
записку «черновик не отправлен» вместо работы, и один раз это случилось
из-за ОДНОЙ ссылки на восемь утверждений.

То есть починка вынула утверждения из одной беды и положила в худшую.

Разница, которую теперь держит код, существенная и простая:

  * выдуманная ссылка НЕ РАЗРЕШАЕТСЯ НИ ВО ЧТО — источника нет, и это
    ложь о происхождении: ответ придержать правильно;
  * улика есть, открыта и прочитана, но дословных слов утверждения в
    вырезке нет — это нехватка подпорки, а не подлог.

Первое остаётся терминальным. Второе помечается и едет к человеку.
"""
from __future__ import annotations

from core.evidence import ProvenanceChain, make_evidence
from core.verifier import verify

_CLAIM = "Прогон завершился с exit_code 1: passed=0, failed=2 [file:core/campaign.py]."


def _chain_with_unrelated_excerpt() -> ProvenanceChain:
    """Улика настоящая и прочитанная — просто про другое место файла."""
    chain = ProvenanceChain()
    chain.add(make_evidence(
        kind="file", source_id="file:core/campaign.py", obtained_via="file_read",
        claim="x", excerpt="счётчик растёт при did_work ложь"))
    return chain


def _verify(answer: str, chain: ProvenanceChain):
    return verify(answer=answer, chain=chain, expects_contract_headers=False)


def test_a_present_source_is_not_a_forgery() -> None:
    """Улика есть — значит подлога нет, и ответ не должен умирать."""
    report = _verify(_CLAIM, _chain_with_unrelated_excerpt())
    assert report.cited_but_unmatched_chunks == 0, report.to_log_payload()


def test_the_claim_is_still_marked_unsupported() -> None:
    """Пометить — да; выдать за подтверждённое — нет."""
    report = _verify(_CLAIM, _chain_with_unrelated_excerpt())
    assert report.topic_supported_but_claim_unverified_chunks == 1
    assert "улика-без-этих-слов" in report.annotated_answer


def test_it_is_not_called_a_lie_either() -> None:
    report = _verify(_CLAIM, _chain_with_unrelated_excerpt())
    assert report.refuted_chunks == 0
    assert "[claim-refuted]" not in report.annotated_answer


def test_a_source_that_does_not_exist_stays_terminal() -> None:
    """Ссылка, за которой нет НИ ОДНОЙ улики, по-прежнему подлог."""
    report = _verify(_CLAIM, ProvenanceChain())
    assert report.cited_but_unmatched_chunks == 1, report.to_log_payload()
