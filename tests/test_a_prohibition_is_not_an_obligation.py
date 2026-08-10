"""Запрет не имеет права становиться долгом, который он запрещает.

ЖИВОЙ ЗАМЕР 2026-08-10, прогон `run_08de35f9`. Задание на 6823 знака дошло до
`observe` целиком — вход не терял ничего. Контракт завершения сохранил из него
ОДНО обязательство при четырнадцати названных единицах (восемь нумерованных
разделов плюс шесть разделов отчёта A–F).

И это одно обязательство извлечено ИЗ ЗАПРЕТА. В тексте стояло:

    Do not manually add a lesson to persistent memory merely
    to make the test pass.

`_TESTS_PASS_RE` увидел «make the test pass» и завёл долг `tests_green`.
Единственное, к чему система себя обязала, было ровно тем, что оператор
запретил.

Класс общий, а не про одну фразу — измерено шесть из шести:
`Do not create X` -> «X обязан существовать»; `Не исправляй Y` -> «Y обязан
быть изменён». Отрицание не входит в область видимости извлекателя.

ПОСЛЕДСТВИЕ ПОВЕДЕНЧЕСКОЕ, а не косметическое. Такой долг попадает в
`unmet_obligations`, оттуда в `evaluate_completion_obligations` как
`triggered=True`, оттуда сигналом `obligation_silently_missing` в
`assemble_completion_verdict`, который понижает `achieved` до
`partially_achieved` — и снимает кредит процедуре и допуск эпизода.

То есть: ПОСЛУШАНИЕ ЗАПРЕТУ ЗАПИСЫВАЛОСЬ КАК ПРОВАЛ.

Модуль сам объявляет это правило в комментарии к списку глаголов: «ложное
заявление выдумывает обязательство, которого оператор не давал». Здесь оно
пинится.
"""
from __future__ import annotations

import pytest

from core.completion_contract import derive_completion_contract, unmet_obligations
from core.completion_obligation import evaluate_completion_obligations

_ANSWER = (
    "Conclusion: измерение выполнено, файл не изменялся. [file]\n"
    "Facts:\n- core/loop.py не изменялся. [file]\n"
    "Sources:\n1. file - core/loop.py\nConfidence: high\nUnverified: nothing\n"
)


@pytest.mark.parametrize("request_text", [
    "Do not create core/probe_demo.py under any circumstances.",
    "Не создавай core/probe_demo.py ни при каких условиях.",
    "Do not modify core/loop.py during this investigation.",
    "Не исправляй core/verifier.py — только измерь.",
    "Never write tests/scratch.py; report instead.",
    "Do not change anything merely to make the tests pass.",
    "Не меняй конфигурацию и не трогай core/model_router.py.",
])
def test_a_prohibition_creates_no_obligation(request_text: str) -> None:
    """ГЛАВНОЕ: шесть из шести случаев замера, плюс седьмой."""
    contract = derive_completion_contract(request_text)
    assert not contract.obligations, (
        f"запрет породил долг, который запрещает: "
        f"{[(o.deliverable, o.target) for o in contract.obligations]}"
    )


@pytest.mark.parametrize("request_text", [
    "Создай core/probe_demo.py и прогони тесты.",
    "Исправь core/loop.py, чтобы тесты проходили.",
    "Напиши tests/test_probe.py.",
])
def test_a_real_request_still_produces_its_obligation(request_text: str) -> None:
    """ПРЕДОХРАНИТЕЛЬ: извлекатель не заклинен в положении «долгов нет»."""
    contract = derive_completion_contract(request_text)
    assert contract.obligations, f"настоящая просьба потеряла долг: {request_text!r}"


def test_a_mixed_request_keeps_only_the_asked_half() -> None:
    """Одно предложение просит, другое запрещает — обязано выжить только первое."""
    contract = derive_completion_contract(
        "Создай core/probe_demo.py. Не трогай core/loop.py."
    )
    targets = {o.target for o in contract.obligations}
    assert "core/probe_demo.py" in targets
    assert "core/loop.py" not in targets, "запрещённый путь стал долгом"


def test_the_prohibition_is_still_visible_as_unsupported() -> None:
    """Запрет не исчезает: он остаётся видимым как непроверяемый.

    Иначе починка обменяла бы выдуманный долг на молчание, а оператор должен
    видеть, что запрет распознан и механически не проверялся.
    """
    contract = derive_completion_contract("Do not modify core/loop.py. Only measure.")
    assert not contract.obligations
    assert any(u.kind == "prohibition" for u in contract.unsupported_deliverables)
    assert contract.coverage == "partial"


def test_obedience_is_no_longer_recorded_as_a_missing_duty() -> None:
    """КОНЕЦ ЦЕПИ: то самое поведенческое последствие."""
    question = "Do not modify core/loop.py during this investigation. Only measure."
    contract = derive_completion_contract(question)
    assert not list(unmet_obligations(contract, artifacts={}))

    result = evaluate_completion_obligations(
        question=question, answer=_ANSWER, contract=contract, artifacts={}
    )
    assert not result.triggered, (
        "послушание запрету всё ещё поднимает `obligation_silently_missing`"
    )


def test_the_live_specimen_no_longer_manufactures_tests_green() -> None:
    """Дословная фраза из задания 2026-08-10."""
    contract = derive_completion_contract(
        "Do not manually add a lesson to persistent memory merely to make the "
        "test pass. Do not treat documentation as proof of runtime behavior."
    )
    assert not any(o.deliverable == "tests_green" for o in contract.obligations), (
        "запрет на подгонку теста снова прочитан как требование зелёных тестов"
    )
