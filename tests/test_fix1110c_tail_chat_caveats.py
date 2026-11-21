"""Хвост «Чего я не проверил» в разговоре: одна форма, без служебного шума, без потери оговорок."""
import pytest

from core.answer_format import (
    format_human_response,
)
from core.output_policy import REPLAN_WARNINGS

_REPLAN_RU = REPLAN_WARNINGS[0]


@pytest.fixture
def chat(monkeypatch):
    monkeypatch.setenv("AGENT_HUMAN_CHAT", "1")


def _answer(unverified: str, tail: str = "") -> str:
    return (
        "Conclusion: Код склада в файле notes.txt — 7391 [file:notes.txt].\n\n"
        "Facts:\n- Код склада: 7391 [file:notes.txt]\n\n"
        "Sources:\n1. file:notes.txt\n\nConfidence: medium\n\n"
        f"Unverified:{unverified}\n\nSafety: nothing\n" + (f"\n{tail}\n" if tail else "")
    )


_MISSING = _answer(
    "\n- Существует ли файл secret_plan.txt под другим именем — не подтверждено.; "
    f"{_REPLAN_RU}\n- Другие папки я не просматривал.",
    "Проверка: подтверждено 1 из 9 утверждений (3 — ответ сам назвал непроверенными); "
    "без подтверждения: 8; уверенность: низкая.",
)


def test_several_caveats_are_one_list_with_one_marker(chat):
    human = format_human_response(_MISSING)
    tail = human[human.index("Чего я не проверил"):]
    assert tail.splitlines()[:3] == [
        "Чего я не проверил:",
        "— Существует ли файл secret_plan.txt под другим именем — не подтверждено.",
        "— Другие папки я не просматривал.",
    ]
    assert ": -" not in tail and ".;" not in tail and "\n- " not in tail


def test_the_replan_remark_is_said_once_in_plain_words(chat):
    human = format_human_response(_MISSING)
    assert "replan" not in human and "бюджет" not in human
    assert human.count("попытки на этот вопрос закончились") == 1


def test_the_replan_remark_is_dropped_when_every_claim_was_confirmed(chat):
    answer = _answer(
        f" {_REPLAN_RU}",
        "Проверка: подтверждено 3 из 3 утверждений; без подтверждения: 0; уверенность: высокая.",
    )
    assert format_human_response(answer) == "Код склада в файле notes.txt — 7391."


def test_the_replan_remark_stays_when_only_the_rejected_draft_was_counted(chat):
    answer = _answer(
        f" {_REPLAN_RU}",
        "Проверка: отклонённый черновик — подтверждено 3 из 3 утверждений; "
        "без подтверждения: 0; уверенность: высокая.",
    )
    assert "попытки на этот вопрос закончились" in format_human_response(answer)


def test_the_replan_remark_stays_when_the_count_is_unknown(chat):
    human = format_human_response(_answer(f" {_REPLAN_RU}"))
    assert human.endswith("Перепроверить это ещё одной попыткой я не смог: попытки на этот вопрос закончились.")
    assert "Чего я не проверил" not in human


def test_a_caveat_wrapped_over_lines_stays_one_item_and_repeats_are_dropped(chat):
    human = format_human_response(_answer(
        "\n- Запись не перечитывалась\n  после сохранения.\n* Запись не перечитывалась после сохранения.\n"
    ))
    assert human.endswith("Чего я не проверил: Запись не перечитывалась после сохранения.")
