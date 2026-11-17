"""Хвост «Чего я не проверил» в разговоре: одна форма, без служебного шума, без потери оговорок."""
import pytest

from core.answer_format import (
    CHAT_TAIL_ADDENDUM,
    SYSTEM_ANSWER,
    chat_contract,
    format_human_response,
)
from core.output_policy import REPLAN_WARNINGS, apply_ranker_output_policy

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


def test_one_caveat_is_one_sentence_and_a_real_limit_is_kept(chat):
    answer = _answer(
        "\n- Содержимое файла я не перечитывал после записи",
        "Проверка: подтверждено 2 из 3 утверждений (1 — ответ сам назвал непроверенными); "
        "без подтверждения: 1; уверенность: средняя.",
    )
    human = format_human_response(answer)
    assert human.endswith("Чего я не проверил: Содержимое файла я не перечитывал после записи.")


def test_a_caveat_wrapped_over_lines_stays_one_item_and_repeats_are_dropped(chat):
    human = format_human_response(_answer(
        "\n- Запись не перечитывалась\n  после сохранения.\n* Запись не перечитывалась после сохранения.\n"
    ))
    assert human.endswith("Чего я не проверил: Запись не перечитывалась после сохранения.")


def test_nothing_to_say_means_no_tail(chat):
    assert format_human_response(_answer(" nothing")) == "Код склада в файле notes.txt — 7391."
    assert format_human_response(_answer("\n- Ничего.")) == "Код склада в файле notes.txt — 7391."


def test_the_service_output_is_unchanged(monkeypatch):
    monkeypatch.delenv("AGENT_HUMAN_CHAT", raising=False)
    human = format_human_response(_MISSING)
    assert (
        "⚠️ Не подтверждено: - Существует ли файл secret_plan.txt под другим именем — "
        f"не подтверждено.; {_REPLAN_RU} - Другие папки я не просматривал."
    ) in human


def test_the_policy_still_writes_the_same_replan_warning():
    def said(question: str) -> tuple[str, ...]:
        return apply_ranker_output_policy(
            answer="Conclusion: x", ranking=None, question=question, replan_exhausted=True,
        ).warnings

    assert said("что в файле?") == (_REPLAN_RU,)
    assert said("what is in the file?") == (REPLAN_WARNINGS[1],)
    assert "replan-бюджета" in _REPLAN_RU and "replan budget" in REPLAN_WARNINGS[1]


def test_the_chat_contract_asks_only_for_caveats_that_bear_on_the_turn(chat):
    prompt = chat_contract(SYSTEM_ANSWER)
    assert prompt == SYSTEM_ANSWER + CHAT_TAIL_ADDENDUM
    assert "did not ask" in CHAT_TAIL_ADDENDUM and '"nothing"' in CHAT_TAIL_ADDENDUM
    assert "read back" in CHAT_TAIL_ADDENDUM, "настоящая оговорка названа примером, а не запрещена"


def test_the_chat_rule_leaves_other_contracts_and_the_service_mode_alone(chat, monkeypatch):
    assert chat_contract("Reply with a table only.") == "Reply with a table only."
    monkeypatch.delenv("AGENT_HUMAN_CHAT")
    assert chat_contract(SYSTEM_ANSWER) == SYSTEM_ANSWER


def test_synthesis_takes_the_chat_contract(chat):
    import core.loop  # noqa: F401 — регистрирует договор синтеза
    from core.loop_synthesis import AgentLoopSynthesis

    assert AgentLoopSynthesis._resolve_synthesis_contract(object()) == SYSTEM_ANSWER + CHAT_TAIL_ADDENDUM
