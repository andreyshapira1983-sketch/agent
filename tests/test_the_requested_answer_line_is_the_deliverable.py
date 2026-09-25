"""Строка ответа, заданная человеком, доходит последней, чистой и с оговоркой.

GAIA 2026-09-25: 17 из 54 ответов без «FINAL ANSWER: …» — свой контракт
кончался «Safety», метки проверки садились в саму строку, ворота слабых улик
вырезали её вместе с догадками. См. core/requested_format.py.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

from core.answer_format import format_human_response
from core.loop_run_tail import AgentLoopRunTail
from core.requested_format import honor, requested_labels

GAIA = ("You are a general AI assistant. I will ask you a question. Report your thoughts, and finish "
        "your answer with the following template: FINAL ANSWER: [YOUR FINAL ANSWER]. YOUR FINAL "
        "ANSWER should be a number OR as few words as possible.\n\nGAIA Question: How many?")
_GAIA_EXTRACT = re.compile(r"FINAL ANSWER\s*[:：]\s*(.+)", re.IGNORECASE)


def _gaia_value(text: str) -> str | None:
    found = _GAIA_EXTRACT.findall(text)
    return found[-1].strip() if found else None


def _contract(conclusion: str) -> str:
    return (f"Conclusion:\n{conclusion}\nFacts:\n- The page lists it. [web:https://example.org/a]\n"
            "Sources:\n1. web - https://example.org/a\nConfidence: medium\nUnverified:\nnothing\n"
            "Safety:\nnothing\n\nПроверка: подтверждено 1 из 2 утверждений; уверенность: средняя.")


def test_templates_are_found_and_pasted_logs_are_not():
    assert requested_labels(GAIA) == ("FINAL ANSWER",)
    assert requested_labels("Ответь одной строкой: ОТВЕТ: <значение в рублях>") == ("ОТВЕТ",)
    assert requested_labels("Traceback… ERROR: [Errno 2] No such file") == ()
    assert requested_labels("просто вопрос без формы") == ()


def test_a_tagged_line_in_the_conclusion_ends_the_answer_clean_with_its_warning_above():
    answer = _contract("FINAL ANSWER: 34689 [unverified]")
    out = honor(answer, question=GAIA)
    lines = out.text.splitlines()
    assert lines[-1] == "FINAL ANSWER: 34689"
    assert "не подтверждено" in lines[-2]  # метка не пропала — стала словами
    assert _gaia_value(out.text) == "34689"
    assert "Conclusion:" in out.text and out.origins == {"FINAL ANSWER": "answer"}


def test_the_line_inside_the_conclusion_header_keeps_the_header():
    out = honor(_contract("").replace("Conclusion:\n", "Conclusion: FINAL ANSWER: Paris [web:https://x.org]\n"),
                question=GAIA)
    assert out.text.startswith("Conclusion:")
    assert out.text.splitlines()[-1] == "FINAL ANSWER: Paris"


def test_a_line_the_gates_cut_comes_back_from_the_draft_marked_unconfirmed():
    gated = _contract("Недостаточно данных для развёрнутого ответа.")
    draft = _contract("FINAL ANSWER: 7 [general-knowledge]")
    out = honor(gated, question=GAIA, draft=draft)
    assert out.origins == {"FINAL ANSWER": "draft"}
    assert out.text.splitlines()[-1] == "FINAL ANSWER: 7"
    assert "не подтверждено" in out.text.splitlines()[-2]


def test_a_missing_line_is_converted_in_a_separate_step_from_the_answer_text():
    seen = {}

    def convert(label, text):
        seen["label"], seen["text"] = label, text
        return "FINAL ANSWER: 2.0"

    answer = _contract("The value is 2.0 [web:https://example.org/a]")
    out = honor(answer, question=GAIA, convert=convert)
    assert seen["label"] == "FINAL ANSWER" and "2.0" in seen["text"]
    assert out.text.splitlines()[-1] == "FINAL ANSWER: 2.0"
    assert out.origins == {"FINAL ANSWER": "converted"}


def test_no_value_means_no_invented_line_and_the_gap_is_said():
    out = honor(_contract("Не удалось найти."), question=GAIA, convert=lambda _l, _t: "NONE")
    assert _gaia_value(out.text) is None
    assert out.origins == {"FINAL ANSWER": "missing"}
    assert "заполнить нечем" in out.text.splitlines()[-1]


def test_a_failing_converter_does_not_break_the_answer():
    def boom(_l, _t):
        raise RuntimeError("network")

    out = honor(_contract("Не знаю."), question=GAIA, convert=boom)
    assert out.origins == {"FINAL ANSWER": "missing"}


def test_the_human_print_keeps_the_line_after_safety():
    out = honor(_contract("FINAL ANSWER: 34689 [unverified]"), question=GAIA)
    shown = format_human_response(out.text)
    assert shown.splitlines()[-1] == "FINAL ANSWER: 34689"
    assert "не подтверждено" in shown


def test_the_loop_tail_applies_it_and_journals_where_the_line_came_from():
    events = []
    host = SimpleNamespace(log=SimpleNamespace(log=lambda name, payload: events.append((name, payload))),
                           llm=None)
    text = AgentLoopRunTail._honor_requested_format(host, _contract("FINAL ANSWER: 5"), GAIA, "", None)
    assert text.splitlines()[-1] == "FINAL ANSWER: 5"
    assert events == [("requested_format", {"origins": {"FINAL ANSWER": "answer"}})]
    # Без заданной формы ответ не трогается и журнал молчит.
    events.clear()
    plain = _contract("42")
    assert AgentLoopRunTail._honor_requested_format(host, plain, "сколько?", "", None) == plain
    assert events == []
