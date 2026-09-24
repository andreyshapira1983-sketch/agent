"""Сравнение двух источников судится по объединению процитированного (24.09).

Живой прогон приёмки урока 1: «в DOCX 48 750 руб., в скане 47 250 руб.
[file:…docx][file:…png]» — верно, ссылки нашли обе улики, но каждое число есть
только в одной, а сверки судили каждую улику порознь: «число не подтверждено»
навсегда для любого сравнения. ALCE (Gao et al., 2023): утверждение подтверждено,
если его влечёт ОБЪЕДИНЕНИЕ процитированных отрывков.
"""
from __future__ import annotations

from core.evidence import ProvenanceChain, make_evidence
from core.verifier_core import verify


def _chain() -> ProvenanceChain:
    chain = ProvenanceChain()
    chain.add(make_evidence(kind="file", source_id="file:inbox/a.docx", obtained_via="convert_file",
                            claim="text", excerpt="Счёт 318\nДоставка 1 500\nИТОГО: 48 750 руб."))
    chain.add(make_evidence(kind="file", source_id="file:inbox/a_scan.png", obtained_via="convert_file",
                            claim="text", excerpt="Счёт 318 (копия клиента)\nИТОГО: 47 250 руб."))
    return chain


def _verdict(answer: str) -> str:
    return verify(answer=answer, chain=_chain(), llm=None, expects_contract_headers=False).chunks[0].verdict


def test_a_comparison_citing_both_sources_is_verified() -> None:
    assert _verdict("В DOCX итог 48 750 руб., а в скане 47 250 руб. "
                    "[file:inbox/a.docx][file:inbox/a_scan.png]") == "verified"


def test_a_figure_neither_source_has_is_still_not_verified() -> None:
    assert _verdict("В DOCX итог 48 750 руб., а в скане 46 000 руб. "
                    "[file:inbox/a.docx][file:inbox/a_scan.png]") != "verified"


def test_one_source_alone_still_suffices_for_its_own_figure() -> None:
    assert _verdict("В скане итог 47 250 руб. [file:inbox/a_scan.png]") == "verified"
