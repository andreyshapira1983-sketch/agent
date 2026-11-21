"""A cut excerpt neither certifies nor refutes what its gates could not check."""
from __future__ import annotations

from core.evidence import ProvenanceChain, make_evidence
from core.verifier import verify
from core.verifier_absence import (
    absent_literal_reason,
    off_topic_reason,
    restated_number_reason,
)

URL = "https://example.org/bench/report"
_PAD = "Дальше идут служебные строки отчёта без новых сведений. " * 30


def _web_verdict(claim: str, excerpt: str) -> str:
    chain = ProvenanceChain()
    chain.add(make_evidence(kind="web_page", source_id=URL, obtained_via="web_fetch",
                            claim="fetched page", excerpt=excerpt))
    report = verify(answer=f"{claim} [web:{URL}]", chain=chain, llm=None,
                    expects_contract_headers=False)
    return report.chunks[0].verdict


def test_a_changed_number_against_a_cut_page_is_unverified_not_refuted() -> None:
    page = "Выручка за квартал составила 20 миллионов рублей. " + _PAD
    verdict = _web_verdict("Выручка за квартал составила 999 миллионов рублей.", page)
    assert verdict == "topic_supported_but_claim_unverified"


def test_a_changed_number_against_a_complete_page_is_still_refuted() -> None:
    verdict = _web_verdict("Выручка за квартал составила 999 миллионов рублей.",
                           "Выручка за квартал составила 20 миллионов рублей.")
    assert verdict == "refuted"


def test_the_gates_only_lower_on_a_cut_excerpt() -> None:
    """On a cut excerpt each gate answers with a no-accusation reason or nothing."""
    ev = make_evidence(kind="web_page", source_id=URL, obtained_via="web_fetch", claim="",
                       excerpt="Выручка за квартал составила 20 миллионов рублей. " + _PAD)
    literal = absent_literal_reason("Сбой сидит в core/loop_attempt.py.", ev, "web")
    restated = restated_number_reason("Выручка за квартал составила 999 миллионов рублей.", ev, "web")
    assert literal is not None and literal.code == "cited_support_missing"
    assert restated is not None and restated.code == "cited_support_missing"
    assert off_topic_reason("Погода в Мурманске завтра испортится.", ev, "web") is not None
