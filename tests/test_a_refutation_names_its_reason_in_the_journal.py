"""Опровержение обязано назвать причину в журнале, а не только счётчик.

Замер 2026-09-20. `content_refuted` — крупнейший барьер на входе в опыт:
из 142 недопущенных эпизодов его несли 105, больше, чем все остальные
причины вместе. А ПОЧЕМУ кусок опровергнут, журнал не писал: событие
`verification` несло счётчик `refuted_chunks` и список вердиктов, и ни
одного кода причины. Установить её удалось только реконструкцией — цепочки
улик десяти эпизодов собирались заново из трасс (`tool_call` +
`tool_result`) и пересуживались верификатором.

Так найдены два дефекта того дня: `sum_mismatch`, сложивший `exit_code` с
`duration_ms`, и `absence_refuted_by_evidence`, взявший предметом
отсутствия перечисленное наличное. Оба стоили эпизодам входа в опыт, и оба
были невидимы в журнале.

Мера, которая не записывает отвергнутое, не даёт себя перемерить.
"""
from __future__ import annotations

from core.evidence import ProvenanceChain, evidence_from_tool_result
from core.verifier_core import verify

_CODE = "print('alpha:', 2)\nprint('beta:', 3)\n"
_OUT = {
    "code": _CODE, "exit_code": 0, "stdout": "alpha: 2\nbeta: 3\n",
    "stderr": "", "inputs": [], "missing_inputs": [], "timed_out": False,
    "duration_ms": 7, "stdout_truncated": False,
}


def _report(fact: str):
    chain = ProvenanceChain()
    chain.add(evidence_from_tool_result(
        tool_name="python_probe", arguments={"code": _CODE}, output=_OUT))
    answer = (
        "Conclusion: ключи посчитаны. [tool:python_probe]\n"
        f"Facts:\n{fact}\n"
        "Sources:\n1. tool - python_probe\n"
        "Confidence: high\n"
        "Unverified:\n- Ничего.\n"
    )
    return verify(answer=answer, chain=chain)


def test_the_journal_carries_the_code_of_each_refutation() -> None:
    payload = _report("- Ключи дают в сумме 99. [tool:python_probe]").to_log_payload()
    assert payload["refuted_chunks"] == 1
    refutations = payload["refutations"]
    assert len(refutations) == 1
    only = refutations[0]
    assert only["code"] == "sum_mismatch"
    assert only["expected"] == "5" and only["actual"] == "99"
    # `computed_from` у арифметики несёт саму работу — пары, которые гейт
    # сложил. Именно её пришлось восстанавливать вручную, чтобы увидеть, что
    # складывались `exit_code` и `duration_ms`.
    assert only["computed_from"] == "alpha=2 beta=3"
    assert "сумме 99" in only["claim"], "по записи должно быть видно, что судили"


def test_a_clean_answer_carries_no_refutations() -> None:
    payload = _report("- Ключи дают в сумме 5. [tool:python_probe]").to_log_payload()
    assert payload["refuted_chunks"] == 0
    assert payload["refutations"] == []


def test_the_record_stays_bounded() -> None:
    facts = "\n".join(
        f"- Ключи дают в сумме {n}. [tool:python_probe]" for n in range(90, 99)
    )
    payload = _report(facts).to_log_payload()
    assert payload["refuted_chunks"] > 5
    assert len(payload["refutations"]) == 5, "журнал не пухнет от повторов"
    assert all(len(r["claim"]) <= 160 for r in payload["refutations"])
