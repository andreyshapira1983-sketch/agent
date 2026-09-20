"""«так и напиши „данных нет“» — это речь, а не правка файла.

Живой разговор 2026-09-20, 19:31. Агента спросили, почему в прошлом ходе он
не открыл тело функции, которую сам же нашёл. Он не ответил — он задал
встречный вопрос:

    «Уточнение перед выполнением: the request mixes reading and changing
     over several paths; which of them must change cannot be read from the
     wording»

Вопрос был чисто читательский. Виновато оказалось правило доказательства,
которое мостик приклеивает к КАЖДОМУ вопросу: «Если данных нет — так и
напиши „данных нет“ и назови, какой файл ты смотрел». Стебель «напиш» стоит
в `_CREATE_STEMS`, действие стало `create`, путей в вопросе три — и контракт
объявил просьбу неоднозначной. Замерено: тот же вопрос без правила
уточнения не требует, с правилом — требует.

Различитель простой и проверяемый: у глагола письма есть ПРЕДМЕТ. «Напиши
„данных нет“» — предмет в кавычках и это фраза; «создай файл „report.md“» —
предмет в кавычках и это имя файла. Первое речь, второе работа. Поэтому
закавыченный кусок, не содержащий пути, из текста для классификации
вырезается, а закавыченное имя файла остаётся.
"""
from __future__ import annotations

from core.completion_contract import derive_completion_contract

_EVIDENCE_RULE = (
    "\nОтвечай ТОЛЬКО тем, что можешь показать: имя файла со строкой "
    "(file:line) или запись журнала с полем и значением. Если данных нет — "
    "так и напиши «данных нет» и назови, какой файл ты смотрел."
)
_READING = ("Прочитай трассу logs/trace_b5a6.jsonl и "
            "core/incremental_splitter.py и ответь, что помешало.")


def test_the_evidence_rule_no_longer_turns_a_read_into_a_change() -> None:
    contract = derive_completion_contract(_READING + _EVIDENCE_RULE)
    assert not contract.needs_clarification, list(contract.ambiguities)


def test_the_same_request_without_the_rule_was_always_fine() -> None:
    assert not derive_completion_contract(_READING).needs_clarification


def test_a_quoted_filename_is_still_work() -> None:
    """Закавыченное ИМЯ ФАЙЛА остаётся работой: вырезается только фраза."""
    contract = derive_completion_contract("Создай файл «report.md» с выводами.")
    owed = [(o.deliverable, o.target) for o in contract.obligations]
    assert ("file_exists", "report.md") in owed, owed


def test_a_plain_change_request_is_untouched() -> None:
    contract = derive_completion_contract(
        "Исправь core/loop.py: убери лишний вызов.")
    owed = [o.target for o in contract.obligations]
    assert any("core/loop.py" in str(p) for p in owed), owed


def test_the_ambiguity_still_fires_when_it_should() -> None:
    contract = derive_completion_contract(
        "Прочитай core/loop.py и исправь core/planner.py и tools/web_fetch.py.")
    assert contract.needs_clarification
