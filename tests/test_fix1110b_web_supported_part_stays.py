"""Усечение за нехватку улик оставляет человеку подтверждённую часть — и только её."""
from __future__ import annotations

import pytest

from core.answer_format import format_human_response
from core.evidence import ProvenanceChain, evidence_from_tool_result
from core.low_evidence_policy import evaluate_low_evidence_policy
from core.verifier import verify
from tests.test_fix1110b_web_claim_in_page import _BODY, _NAV, _TAIL

_URL = "https://www.python.org/downloads/"
_QUESTION = "Найди в интернете последнюю стабильную версию Python и назови источник."


def _truncated(monkeypatch: pytest.MonkeyPatch, *, true_version: bool) -> tuple[str, bool]:
    monkeypatch.setenv("AGENT_HUMAN_CHAT", "1")
    ev = evidence_from_tool_result(
        tool_name="web_fetch", arguments={"url": _URL}, status="success",
        output={"url": _URL, "text": _NAV + _BODY + _TAIL})
    assert ev is not None
    invented = "\n".join(f"- Python 9.{n}.7 вышел в июле [web:{_URL}]" for n in range(1, 8))
    first = "3.15.2" if true_version else "9.0.7"
    answer = (f"Conclusion: ответ ниже [general-knowledge]\nFacts:\n"
              f"- На странице стоит Download Python {first} [web:{_URL}]\n{invented}\nUnverified: nothing\n")
    report = verify(answer=answer, chain=ProvenanceChain([ev]))
    result = evaluate_low_evidence_policy(answer=report.annotated_answer, report=report, question=_QUESTION)
    return format_human_response(result.answer), result.triggered


def test_the_verified_fact_survives_the_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    shown, triggered = _truncated(monkeypatch, true_version=True)
    assert triggered
    assert "Download Python 3.15.2" in shown, shown
    assert "9.1.7" not in shown and "9.7.7" not in shown, shown
