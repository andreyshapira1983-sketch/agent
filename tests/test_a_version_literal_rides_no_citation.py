"""Версия с точками — отличительный литерал: зайцем на чужой цитате не ездит.

Background: docs/CODE_NOTES.md, "The stowaway claim".
"""
from __future__ import annotations

from core.evidence import ProvenanceChain, make_evidence
from core.verifier import verify
from core.verifier_utils import literals_absent_from_excerpt, salient_literals

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


def test_a_version_triplet_is_a_salient_literal():
    assert "3.11.9" in salient_literals("используется Python `3.11.9` здесь")


def test_bare_decimals_stay_with_the_arithmetic_judge():
    """Улов не отдан: голые числа и десятичные — территория
    `evaluate_claim_arithmetic`; второй судья над той же областью не заводится.
    """
    lits = salient_literals("score 0.795 at threshold 0.45, version 3.12")
    assert "0.795" not in lits
    assert "3.12" not in lits


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


def test_a_version_the_evidence_does_name_is_still_verified():
    """Ломка наоборот: версия, которая В улике есть, подтверждается как раньше."""
    answer = (
        "Conclusion: batched появился в 3.12. [web:docs]\n"
        "Facts:\n"
        "- Функция добавлена в версии 3.12 [web:docs]\n"
        "Sources:\n1. web:docs - itertools docs\n"
        "Confidence: high\nUnverified: nothing\n"
    )
    report = verify(
        answer=answer,
        chain=_chain(_web("docs", _DOCS)),
        user_question="когда появился batched",
    )

    assert report.refuted_chunks == 0
    assert report.verified_chunks >= 1


def test_the_gate_function_names_the_missing_triplet():
    absent = literals_absent_from_excerpt(
        "в этой среде используется Python 3.11.9", _DOCS,
    )
    assert "3.11.9" in absent
