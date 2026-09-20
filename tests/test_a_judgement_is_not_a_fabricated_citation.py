"""Решение — не выдуманная цитата.

Живой разговор 2026-09-21. Агента спросили о том, чего нет ни в одном файле:
должен ли его собственный дефект когда-нибудь побеждать цель, поставленную
человеком. Он ответил лучшим рассуждением за вечер — и назвал признак,
проверяемый со стороны: дефект берёт верх тогда, когда молчащий прибор
искажает сам замер, которым агент отчитывается перед оператором.

А его собственный судья вырезал из этого ответа утверждение. В трассе
дословно: `fabricated_citations=1; excised=1`, заметка
`fabricated_claims_excised`. Всему ответу досталась низкая уверенность:
подтверждено 2 из 8. За рассуждение, в котором подтверждать нечего.

Причина проста. У суждения НЕТ строки в файле. Ссылка к нему не
разрешается — и `cited_unmatched` превращается в `fabricated_citations`.
Наказание пришло ровно за то, чего от агента добивались весь вечер: за
принятое решение.

Различитель предложил сам агент, и он точнее, чем «есть ссылка или нет»:
важно, что предложение ОБЕЩАЕТ. Суждение обещает «вот моё решение и вот на
чём оно стоит»; фабрикация обещает «вот факт и вот его источник». Прецедент
уже стоял рядом: `general-knowledge` давно помечает честное «это из
обучения» и ложью не считается. Теперь рядом стоит `judgement`.

Верификатор при этом НЕ начинает судить о правильности решения — он и не
должен. Он лишь перестаёт называть его ложью.
"""
from __future__ import annotations

from core.evidence import ProvenanceChain
from core.verifier import verify
from core.verifier_patterns import CITATION_PREFIXES, SELF_DECLARED_PREFIXES

_JUDGEMENT = (
    "Цель человека должна побеждать почти всегда, но не всегда "
    "[judgement: дефект портит сам замер, которым я отчитываюсь]."
)
_FABRICATION = (
    "Счётчик растёт только при did_work=false [file:core/campaign.py#L9999]."
)


def _verify(answer: str):
    """Пустая цепочка улик нарочно: у суждения источника нет по природе."""
    return verify(answer=answer, chain=ProvenanceChain(),
                  expects_contract_headers=False)


def test_the_vocabulary_knows_a_judgement() -> None:
    assert "judgement" in CITATION_PREFIXES
    assert "judgement" in SELF_DECLARED_PREFIXES


def test_a_judgement_is_not_counted_as_a_fabrication() -> None:
    report = _verify(_JUDGEMENT)
    assert report.self_declared_chunks >= 1, report.to_log_payload()
    assert report.cited_but_unmatched_chunks == 0, report.to_log_payload()


def test_a_judgement_does_not_count_as_fully_unverified() -> None:
    """Честно названное решение не роняет весь ответ в «ничем не подтверждён»."""
    report = _verify(_JUDGEMENT)
    assert not report.fully_unverified, report.to_log_payload()


def test_a_missing_file_is_still_a_fabrication() -> None:
    """Послабление узкое: выдуманный АДРЕС остаётся выдуманным адресом."""
    report = _verify(_FABRICATION)
    assert report.self_declared_chunks == 0, report.to_log_payload()
