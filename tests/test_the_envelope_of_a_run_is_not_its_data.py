"""`duration_ms` — не слагаемое: конверт прогона не есть его данные.

Воспроизведено 2026-09-20 на живом ответе. Агент посчитал пробой доли и
написал: «total tickets = 400, A = 100/400 = 0.25, B = 50/400 = 0.125,
C = 250/400 = 0.625, сумма вероятностей = 1.0». Верификатор объявил это
ЛОЖЬЮ с кодом `sum_mismatch`: «the sum is 9, not 400».

Девять — это `exit_code: 0` плюс `duration_ms: 9`. Гейт арифметики собирает
пары «ключ: число» по строкам улики, а улика пробы начинается с КОНВЕРТА,
который пишет `core/evidence.py`: код возврата, длительность, признак
усечения. Сложив служебные поля, гейт получил «настоящую сумму» и опроверг
верный расчёт.

Цена: сигнал `content_refuted` дисквалифицирует эпизод из опыта раньше всех
прочих осей (`DISQUALIFYING_DEFECT_SIGNALS`). Замер того же дня: из 142
недопущенных эпизодов 105 несли этот сигнал — больше, чем все остальные
причины вместе.

Конверт называется поимённо и ровно теми именами, которые пишет наш же
сборщик улик: это не эвристика, а список собственных служебных полей.
"""
from __future__ import annotations

from core.claim_arithmetic import parse_pairs
from core.evidence import ProvenanceChain, evidence_from_tool_result
from core.verifier_core import verify

_CODE = (
    "total = 100 + 50 + 250\n"
    "for name, n in (('A', 100), ('B', 50), ('C', 250)):\n"
    "    print(name, n / total)\n"
)
_OUT = {
    "code": _CODE, "exit_code": 0,
    "stdout": "A 0.25\nB 0.125\nC 0.625\nsum 1.0\n",
    "stderr": "", "inputs": [], "missing_inputs": [], "timed_out": False,
    "duration_ms": 9, "stdout_truncated": False,
}
_CLAIM = ("- Расчёт в python_probe: total tickets = 400, A = 100/400 = 0.25, "
          "B = 50/400 = 0.125, C = 250/400 = 0.625, сумма вероятностей = 1.0 "
          "[tool:python_probe]")


def _answer(fact: str) -> str:
    return (
        "Conclusion: доли считаются от общего числа. [tool:python_probe]\n"
        f"Facts:\n{fact}\n"
        "Sources:\n1. tool - python_probe\n"
        "Confidence: high\n"
        "Unverified:\n- Ничего.\n"
    )


def _verified(answer: str, output=None):
    chain = ProvenanceChain()
    chain.add(evidence_from_tool_result(
        tool_name="python_probe", arguments={"code": _CODE},
        output=output or _OUT))
    return verify(answer=answer, chain=chain)


def test_the_envelope_is_not_a_dataset() -> None:
    evidence = evidence_from_tool_result(
        tool_name="python_probe", arguments={"code": _CODE}, output=_OUT)
    pairs = parse_pairs(evidence.excerpt)
    assert "duration_ms" not in pairs, "длительность прогона — не данные"
    assert "exit_code" not in pairs, "код возврата — не данные"


def test_a_correct_calculation_is_not_called_a_lie() -> None:
    report = _verified(_answer(_CLAIM))
    assert report.refuted_chunks == 0, "верный расчёт опровергнут собственным конвертом"


def test_a_real_sum_mismatch_is_still_caught() -> None:
    out = dict(_OUT, stdout="alpha: 2\nbeta: 3\n")
    fact = "- Ключи дают в сумме 99. [tool:python_probe]"
    report = _verified(_answer(fact), output=out)
    assert report.refuted_chunks == 1, "настоящее расхождение обязано ловиться"


def test_a_real_sum_that_holds_is_verified() -> None:
    out = dict(_OUT, stdout="alpha: 2\nbeta: 3\n")
    fact = "- Ключи дают в сумме 5. [tool:python_probe]"
    report = _verified(_answer(fact), output=out)
    assert report.refuted_chunks == 0
