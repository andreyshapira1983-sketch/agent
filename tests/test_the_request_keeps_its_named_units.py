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

import pytest

from core.completion_contract import derive_completion_contract, unaddressed_units

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

_PLAIN = "перечисли функции в core/loop.py"


def test_the_named_units_are_enumerated() -> None:
    """ГЛАВНОЕ: единицы перестают сливаться в один класс."""
    contract = derive_completion_contract(_LIVE)
    titles = [u.title for u in contract.requested_units]
    assert len(titles) == 6, f"перечислено {len(titles)} из шести: {titles}"
    assert any("memory anatomy" in t.lower() for t in titles)
    assert any(t.startswith("C.") for t in titles), titles


def test_a_plain_request_names_no_units() -> None:
    """ПРЕДОХРАНИТЕЛЬ: обычный вопрос не обрастает выдуманными пунктами."""
    assert not derive_completion_contract(_PLAIN).requested_units


def test_an_answer_covering_everything_leaves_nothing_unaddressed() -> None:
    """Ломка наоборот: полный ответ не обязан выглядеть неполным."""
    contract = derive_completion_contract(_LIVE)
    answer = (
        "## 1. Establish the actual memory anatomy\nда\n"
        "## 2. Audit the repeated imagined-API failure\nда\n"
        "## 3. Find the quarantine boundary\nда\n"
        "### A. Memory anatomy\nда\n### B. Failed-experience specimen\nда\n"
        "### C. Causal break\nда\n"
    )
    assert not unaddressed_units(contract, answer)


def test_the_missing_units_are_named_one_by_one() -> None:
    """ГЛАВНОЕ-2: не «часть не покрыта», а какая именно."""
    contract = derive_completion_contract(_LIVE)
    answer = (
        "## 1. Establish the actual memory anatomy\nразобрано\n"
        "### A. Memory anatomy\nразобрано\n"
    )
    missing = unaddressed_units(contract, answer)
    joined = " ".join(missing).lower()
    assert "quarantine boundary" in joined
    assert "causal break" in joined
    assert "memory anatomy" not in joined, "адресованная единица объявлена пропущенной"


def test_units_survive_into_the_log_payload() -> None:
    """Различение обязано пережить переход в журнал."""
    payload = derive_completion_contract(_LIVE).to_log_payload()
    assert len(payload["requested_units"]) == 6


def test_units_and_coverage_are_different_axes() -> None:
    """Перечисление единиц и `coverage` отвечают на РАЗНЫЕ вопросы.

    `coverage` говорит, встретился ли КЛАСС затребованного, который извлекатель
    не умеет проверять. `requested_units` перечисляет ПРЕДМЕТЫ, названные
    оператором. Задание может состоять из шести разделов и не содержать ни
    одного непредставимого класса — тогда покрытие полное, а сверять в конце
    всё равно есть что. Слить их значило бы снова потерять дробление.
    """
    only_units = derive_completion_contract(_LIVE)
    assert only_units.requested_units
    assert only_units.coverage == "complete"

    both = derive_completion_contract(
        _LIVE + "\nDo not modify anything.\nReport separately.\n"
    )
    assert both.requested_units
    assert both.coverage == "partial"


@pytest.mark.parametrize("heading", [
    "## 1. Установить анатомию памяти",
    "### B. Образец провального опыта",
    "## 7. Ne povtoryay oshibku",
])
def test_headings_are_read_in_every_script(heading: str) -> None:
    """Оператор пишет кириллицей, латиницей и транслитом."""
    contract = derive_completion_contract(f"Задание.\n\n{heading}\nтекст\n")
    assert contract.requested_units, f"единица не распознана: {heading!r}"


def test_prose_numbering_is_not_a_unit() -> None:
    """Перечисление в прозе — не структура задания.

    «Есть 3 причины: …» не объявляет раздел, и считать его единицей значило бы
    выдумать пункт, которого оператор не давал.
    """
    contract = derive_completion_contract(
        "Объясни, почему это ломается. Есть 3 причины и 2 следствия."
    )
    assert not contract.requested_units


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


def test_a_complete_answer_leaves_the_verdict_alone() -> None:
    """ПРЕДОХРАНИТЕЛЬ: покрытый ответ не объявляется раздробленным."""
    from core.completion_obligation import evaluate_completion_obligations

    contract = derive_completion_contract(_LIVE)
    answer = (
        "## 1. Establish the actual memory anatomy\nда\n"
        "## 2. Audit the repeated imagined-API failure\nда\n"
        "## 3. Find the quarantine boundary\nда\n"
        "### A. Memory anatomy\nда\n### B. Failed-experience specimen\nда\n"
        "### C. Causal break\nда\n"
    )
    result = evaluate_completion_obligations(
        question=_LIVE, answer=answer, contract=contract, artifacts={}
    )
    assert not result.unaddressed_units


def test_the_signal_lowers_a_claim_of_achieved() -> None:
    """Зубы: потерянная часть задания не удостоверяется как выполненная."""
    from core.smart_memory import assemble_completion_verdict

    verdict = assemble_completion_verdict(
        aborted_reason="", replan_exhausted=False, declared="achieved",
        user_contract_partial=True,
    )
    assert verdict.state == "partially_achieved"
