"""Задание, названное по пунктам, обязано дойти до конца пунктами.

ЖАЛОБА ОПЕРАТОРА, и она точна: «даёшь ему задачу — он её дробит, и никто это
не чинит». Мои прежние правки чинили ПОЛЯРНОСТЬ (запрет перестал становиться
долгом) и ВИДИМОСТЬ (`coverage=partial`). Само дробление они не трогали.

ЗАМЕР 2026-08-10 на живом задании: четырнадцать единиц названы заголовками —
восемь нумерованных разделов работы и шесть разделов отчёта A–F. Контракт нёс
`unsupported_deliverables=['report_sections', 'prohibition']`: две записи по
КЛАССАМ и ни одной по единицам. `partial` говорит «часть не представлена» и не
говорит какая, поэтому в конце хода сверять нечего.

ЧТО ЗДЕСЬ ЗАВОДИТСЯ. Перечисление: каждая названная единица сохраняется
отдельной строкой и доживает до проверки ответа. Это не оценка качества —
только присутствие: назвали шесть разделов отчёта, в ответе нашлось четыре,
значит два не адресованы, и они названы поимённо.

ЧЕГО ЗДЕСЬ НЕ ЗАВОДИТСЯ. Никакого суждения о том, ХОРОШО ли раздел раскрыт:
это требует понимания, а не сверки строк, и выдуманный судья здесь был бы тем
же дефектом, что и выдуманный долг.
"""
from __future__ import annotations

from core.completion_contract import derive_completion_contract

_LIVE = """Perform a live causal audit of your memory system.

## 1. Establish the actual memory anatomy
Some text.

## 2. Audit the repeated imagined-API failure
More text.

## 3. Find the quarantine boundary
Text.

### A. Memory anatomy
### B. Failed-experience specimen
### C. Causal break
"""


def test_the_missing_units_reach_the_completion_verdict() -> None:
    """КОНЕЦ ЦЕПИ: перечисление доходит до вердикта, а не умирает в контракте.

    Прежние правки останавливались на надписи `coverage=partial`. Здесь
    проверяется, что непокрытые единицы названы поимённо ИМЕННО ТАМ, где
    вердикт решает, считать ли ход выполненным.
    """
    from core.completion_obligation import evaluate_completion_obligations

    contract = derive_completion_contract(_LIVE)
    answer = "## 1. Establish the actual memory anatomy\nразобрано\n"
    result = evaluate_completion_obligations(
        question=_LIVE, answer=answer, contract=contract, artifacts={}
    )
    joined = " ".join(result.unaddressed_units).lower()
    assert "quarantine boundary" in joined
    assert "causal break" in joined
    assert any("unaddressed" in n for n in result.notes), result.notes
    assert result.to_log_payload()["unaddressed_units"]


def test_the_signal_lowers_a_claim_of_achieved() -> None:
    """Зубы: потерянная часть задания не удостоверяется как выполненная."""
    from core.smart_memory import assemble_completion_verdict

    verdict = assemble_completion_verdict(
        aborted_reason="", replan_exhausted=False, declared="achieved",
        user_contract_partial=True,
    )
    assert verdict.state == "partially_achieved"
