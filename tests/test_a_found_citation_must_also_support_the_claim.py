"""Найденная ссылка — ещё не подтверждение: процитированное должно ВЛЕЧЬ утверждение.

Замер 2026-09-24: агент написал «второй свидетель op=office даёт улику» — теста
не существовало, — а проверка поставила «подтверждено 8 из 8»: ссылка на вывод
patch_check нашлась, и этого хватало. Проверка по смыслу шла лишь для
утверждений, чья ссылка НЕ нашлась (MIR-060). ALCE (Gao et al., 2023):
утверждение подтверждено, если процитированный отрывок его влечёт.
"""
from __future__ import annotations

from core.evidence import ProvenanceChain, make_evidence
from core.verifier_core import verify

_CLAIM = "The patch added a second witness test for the office conversion"


class _Judge:
    def __init__(self, answer: str) -> None:
        self.answer, self.calls = answer, 0

    def complete(self, **_kw) -> str:
        self.calls += 1
        return self.answer


def _verdict(llm) -> str:
    chain = ProvenanceChain()
    chain.add(make_evidence(kind="tool_output", source_id="tool_output:patch_check",
                            obtained_via="patch_check", claim="patch_check result",
                            excerpt="verdict: green; tests: 107 passed; witness test_converted_text_is_evidence"))
    report = verify(answer=f"{_CLAIM} [tool:patch_check]", chain=chain, llm=llm,
                    expects_contract_headers=False)
    return report.chunks[0].verdict


def test_a_resolved_citation_that_does_not_entail_is_not_verified() -> None:
    judge = _Judge("no")
    assert _verdict(judge) != "verified"
    assert judge.calls >= 1, "the entailment check was never consulted for a resolved citation"


def test_a_resolved_citation_that_entails_stays_verified() -> None:
    assert _verdict(_Judge("yes")) == "verified"


def test_without_a_judge_the_old_behaviour_holds() -> None:
    assert _verdict(None) == "verified"


class _SeesOnly:
    """Отвечает «yes», только если в подсказке видна подтверждающая строка."""

    def __init__(self, needle: str) -> None:
        self.needle = needle

    def complete(self, *, user: str, **_kw) -> str:
        return "yes" if self.needle in user else "no"


def test_support_further_than_six_hundred_chars_is_still_seen() -> None:
    """Улика хранит до 800 знаков (MAX_EXCERPT_CHARS), а окно общей проверки —
    600: подтверждение между 600-м и 800-м знаком оно не видело бы."""
    chain = ProvenanceChain()
    chain.add(make_evidence(kind="tool_output", source_id="tool_output:patch_check",
                            obtained_via="patch_check", claim="patch_check result",
                            excerpt="log line\n" * 72 + "witness test_office_conversion added"))
    report = verify(answer=f"{_CLAIM} [tool:patch_check]", chain=chain,
                    llm=_SeesOnly("witness test_office_conversion added"),
                    expects_contract_headers=False)
    assert report.chunks[0].verdict == "verified"
