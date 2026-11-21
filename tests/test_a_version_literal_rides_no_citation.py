"""Версия с точками — отличительный литерал: зайцем на чужой цитате не ездит.

Background: docs/CODE_NOTES.md, "The stowaway claim".
"""
from __future__ import annotations

from core.evidence import ProvenanceChain, make_evidence
from core.verifier import verify

#: Живая проба 2026-08-16 (trace_3bb22486): составное утверждение «в доках
#: “Added in version 3.12” И у меня Python 3.11.9» подтвердилось целиком по
#: первой, проверяемой половине — а «3.11.9» не было ни в одной улике и
#: оказалось правдой случайно. Версии с точками не входили в салиентные
#: литералы, четвёртому гейту нечего было проверять.
_DOCS = (
    "itertools.batched(iterable, n, strict=False). "
    "Added in version 3.12. Roughly equivalent to the code below."
)


def _chain(*evs) -> ProvenanceChain:
    chain = ProvenanceChain()
    for ev in evs:
        chain.add(ev)
    return chain


def _web(source_id: str, excerpt: str):
    return make_evidence(kind="web_page", source_id=source_id,
                         obtained_via="web_fetch", claim="",
                         excerpt=excerpt, confidence=0.9)


def test_the_stowaway_version_is_not_verified():
    """Форма живой пробы: цитируемая улика знает про 3.12, но ничего не знает
    про 3.11.9 — утверждение с невиданным литералом не получает verified.
    """
    answer = (
        "Conclusion: batched недоступен локально. [web:docs]\n"
        "Facts:\n"
        "- В документации сказано “Added in version 3.12”, а в этой среде "
        "используется Python 3.11.9 [web:docs]\n"
        "Sources:\n1. web:docs - itertools docs\n"
        "Confidence: high\nUnverified: nothing\n"
    )
    report = verify(
        answer=answer,
        chain=_chain(_web("docs", _DOCS)),
        user_question="будет ли batched работать здесь",
    )

    stowaway = [c for c in report.chunks if "3.11.9" in c.text]
    assert stowaway, "кусок с версией вообще не рассматривался"
    assert all(c.verdict != "verified" for c in stowaway), [
        (c.verdict, c.text[:80]) for c in stowaway
    ]
