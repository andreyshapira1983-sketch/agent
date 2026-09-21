"""Ответ, называющий строки кода, — отчёт о прочитанном, а не план.

Сквозная проверка 2026-09-21: на два нумерованных вопроса про свой код агент
ответил «вердикт вычисляется на строке 801 … risk_for() (строки 111–120)».
Нумерация ответа, слово «затем» и «оценк»(у риска) сделали из него «план», улики
перестали требоваться, и хвост сказал «внешнее подтверждение не требовалось» —
хотя всё стояло на прочитанных файлах.
"""
from __future__ import annotations

from core.low_evidence_policy import is_evidence_expected

_QUESTION = "1. Где исход эпизода влияет на процедуру? 2. Требует ли file_write подтверждения?"
_ANSWER = (
    "1. Вердикт вычисляется на строке 801, затем применяется к процедуре.\n"
    "2. Инструмент лишь даёт оценку риска в risk_for() (строки 111–120)."
)


def test_a_code_report_still_owes_evidence() -> None:
    assert is_evidence_expected(role="operator_chat", chain_was_empty=False,
                                realtime_required=False, answer=_ANSWER, question=_QUESTION)


def test_a_real_plan_is_still_a_plan() -> None:
    plan = "1. Сначала прочитаю модуль.\n2. Затем предлагаю разбить его на два."
    assert not is_evidence_expected(role="operator_chat", chain_was_empty=False,
                                    realtime_required=False, answer=plan, question="что будешь делать?")
