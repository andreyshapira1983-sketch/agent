"""Одно имя в двух разделах — не противоречие: суждения-то разные.

Замер 2026-09-20 по 420 живым ответам из трасс: детектор обвинял 154 ответа
(37%), и ни одно обвинение при разборе не оказалось противоречием. Все были
формой ЧЕСТНОГО ответа: Facts утверждает одно свойство предмета («каталог
содержит файл X»), Unverified называет другое («содержимое X не прочитано»).
Раздел Unverified по контракту перечисляет непроверенное — имя, попавшее в
оба раздела, само по себе ничего не снимает.

Цена обвинения измерена там же: оно закрывает эпизоду вход в опыт прежде
всех прочих осей (`_answer_disqualified`), и за четверо суток так отсечены
29 эпизодов из 133, годных по всем трём осям.

Здесь закреплены оба края: ложное обвинение снято, а живой случай
2026-08-10, ради которого детектор построен, по-прежнему ловится.
"""
from __future__ import annotations

from core.answer_contradiction import contradicted_claims

_SCOPE = (
    "Conclusion: файл найден, прочитать не удалось.\n"
    "Facts:\n"
    "- В каталоге math_study/drafts/M11/ лежит файл diff_0-J3_v1.txt.\n"
    "Sources:\n1. tool - list_dir\n"
    "Confidence: medium\n"
    "Unverified:\n"
    "- Точное содержимое diff_0-J3_v1.txt: какие строки добавлены в J4.\n"
)

#: Живой случай 2026-08-10: Facts утверждает, что текущее исполнение — это
#: run_860e7ef, Unverified снимает ровно это отождествление.
_REAL = (
    "Conclusion: система исполняется. [log]\n"
    "Facts:\n"
    "- В ходе текущего выполнения (trace_id run_860e7ef) записано 92 события. [log]\n"
    "Sources:\n1. log - журнал\n"
    "Confidence: high\n"
    "Unverified:\n"
    "- Не доказано, что trace_id run_860e7ef соответствует текущему исполнению.\n"
)

#: Строка Facts, которая сама отрицает: снимать с неё нечего, а до правки
#: её собственный повтор в Unverified приносил обвинение.
_SELF_NEGATING = (
    "Conclusion: прочитать не удалось.\n"
    "Facts:\n"
    "- Содержимое core/charter_goal.py не было прочитано.\n"
    "Sources:\n1. tool - file_read\n"
    "Confidence: low\n"
    "Unverified:\n"
    "- Точное условие остановки в core/charter_goal.py.\n"
)


def test_another_property_of_the_same_file_is_not_a_denial() -> None:
    assert contradicted_claims(_SCOPE) == ()


def test_a_facts_line_that_itself_denies_asserts_nothing() -> None:
    assert contradicted_claims(_SELF_NEGATING) == ()


def test_the_case_the_detector_was_built_for_is_still_caught() -> None:
    found = contradicted_claims(_REAL)
    assert found, "живой случай 2026-08-10 обязан ловиться"
    assert found[0].subject == "run_860e7ef"
    assert found[0].asserted_in == "facts" and found[0].denied_in == "unverified"


def test_a_denial_naming_a_new_object_is_about_something_else() -> None:
    answer = (
        "Conclusion: записи найдены.\n"
        "Facts:\n"
        "- В data/charter_decisions.jsonl найдено 37 строк с reasoning_action_mismatch.\n"
        "Sources:\n1. tool - python_probe\n"
        "Confidence: medium\n"
        "Unverified:\n"
        "- Есть ли в data/charter_decisions.jsonl записи с полем detectors "
        "при outcome_failed — в прочитанных не видно.\n"
    )
    assert contradicted_claims(answer) == ()
