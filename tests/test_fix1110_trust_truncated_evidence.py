"""A cut excerpt neither certifies nor refutes what its gates could not check."""
from __future__ import annotations

import pytest

from core.evidence import MAX_EXCERPT_CHARS, ProvenanceChain, make_evidence
from core.verifier import verify
from core.verifier_absence import (
    absent_literal_reason,
    off_topic_reason,
    restated_number_reason,
)

URL = "https://example.org/bench/report"
_PAD = "Дальше идут служебные строки отчёта без новых сведений. " * 30
_PAD_EN = "More filler lines of the same listing follow here. " * 30
_BUDGET_TAILS = (
    "\n...[INTENT-BUDGET: 60 of 12000 chars; head only]",
    "\n...[TOTAL-BUDGET: trimmed to 60 of 9000 chars to fit 30000-char total evidence budget]",
)


def _file_report(claim: str, excerpt: str):
    chain = ProvenanceChain()
    chain.add(make_evidence(kind="file", source_id="file:notes/long.txt",
                            obtained_via="file_read", claim="", excerpt=excerpt))
    return verify(answer=f"{claim} [file:notes/long.txt]", chain=chain, llm=None,
                  expects_contract_headers=False)


def _web_verdict(claim: str, excerpt: str) -> str:
    chain = ProvenanceChain()
    chain.add(make_evidence(kind="web_page", source_id=URL, obtained_via="web_fetch",
                            claim="fetched page", excerpt=excerpt))
    report = verify(answer=f"{claim} [web:{URL}]", chain=chain, llm=None,
                    expects_contract_headers=False)
    return report.chunks[0].verdict


def test_the_fixture_is_really_cut() -> None:
    ev = make_evidence(kind="file", source_id="file:x", obtained_via="file_read",
                       claim="", excerpt="The listing names only core/loop.py here. " + _PAD_EN)
    assert len(_PAD_EN) > MAX_EXCERPT_CHARS and ev.excerpt.endswith("...[truncated]")


def test_a_literal_past_the_cut_is_neither_verified_nor_refuted() -> None:
    report = _file_report("The gap lives in core/loop_attempt.py.",
                          "The listing names only core/loop.py here. " + _PAD_EN)
    assert report.verified_chunks == 0, [c.verdict for c in report.chunks]
    assert report.refuted_chunks == 0
    assert report.chunks[0].verdict == "topic_supported_but_claim_unverified"


@pytest.mark.parametrize("tail", _BUDGET_TAILS)
def test_a_budget_trim_downgrades_the_same_way(tail: str) -> None:
    report = _file_report("The gap lives in core/loop_attempt.py.",
                          "The listing names only core/loop.py here." + tail)
    assert report.verified_chunks == 0 and report.refuted_chunks == 0


def test_a_literal_inside_the_visible_part_is_still_verified() -> None:
    report = _file_report("The gap lives in core/loop_attempt.py.",
                          "The gap lives in core/loop_attempt.py, swallowed whole. " + _PAD_EN)
    assert report.verified_chunks == 1


def test_a_complete_excerpt_still_refutes() -> None:
    report = _file_report("The gap lives in core/loop_attempt.py.",
                          "The listing names only core/loop.py here.")
    assert report.refuted_chunks == 1


def test_a_cut_page_about_something_else_is_not_verified() -> None:
    page = "Средняя задержка обработки запроса составила 20 миллисекунд. " + _PAD
    assert _web_verdict("Курс акций компании вырос на 20 процентов.", page) != "verified"


def test_a_cut_page_on_the_same_subject_is_still_verified() -> None:
    page = "Средняя задержка обработки запроса составила 20 миллисекунд. " + _PAD
    assert _web_verdict("Задержка обработки запроса составила 20 миллисекунд.", page) == "verified"


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
