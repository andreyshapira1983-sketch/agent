"""Человек получает ответ, а кухня проверки остаётся в журнале (оператор 26.09, Телеграм).

Живой случай: агент пообещал «без списков улик и без „подтверждено N из M“», и
край показа тут же приклеил к обещанию и то и другое.
"""
import pytest

from core.answer_format import format_human_response


@pytest.fixture(autouse=True)
def _chat(monkeypatch):
    monkeypatch.setenv("AGENT_HUMAN_CHAT", "1")


_LIVE = (
    "Conclusion: Принято: в группе отвечаю как собеседник, коротко "
    "[topic-only:memory:mem_1]. Имя: Архив [цитата-не-подтверждает]\n\n"
    "Facts:\n"
    "- В рабочей копии один untracked-файл [file:tests/x.py]\n"
    "- Тот же файл виден и в git status [claim-figure-unverified]\n\n"
    "Unverified: что самосборка стоит именно из-за этого файла\n"
    "Sources: file:tests/x.py\n"
    "Проверка: подтверждено 4 из 10 утверждений; без подтверждения: 6; уверенность: низкая."
)


def test_the_answer_reaches_the_human_without_the_evidence_list_and_score():
    human = format_human_response(_LIVE)
    assert human.startswith("Принято: в группе отвечаю как собеседник")
    assert "untracked-файл" not in human
    assert "подтверждено 4 из 10" not in human
    assert "Проверка:" not in human


def test_a_warning_is_said_once_in_plain_words_not_as_tags():
    """Оператор 27.09: «(не проверял)» после каждой фразы — тоже кухня; одна оговорка в конце."""
    human = format_human_response(_LIVE)
    assert "(не проверял)" not in human
    assert "[цитата-не-подтверждает]" not in human
    assert "источник по теме" not in human
    assert "Чего я не проверил: что самосборка стоит" in human


def test_a_conclusion_that_announces_a_list_keeps_the_list():
    human = format_human_response("Conclusion: Вот файлы:\nFacts:\n- a.py [file:a.py]\n- b.py [file:b.py]\n")
    assert "• a.py" in human and "• b.py" in human


def test_the_off_topic_warning_is_said_in_words():
    answer = ("Conclusion: Ответ.\nПроверка: подтверждено 1 из 3 утверждений; уверенность: низкая. "
              "Соответствие вопросу: 0.20 — ответ может отвечать не на заданный вопрос.")
    human = format_human_response(answer)
    assert "Возможно, я ответил не на тот вопрос." in human
    assert "0.20" not in human


def test_outside_the_chat_the_task_answer_keeps_its_facts(monkeypatch):
    monkeypatch.delenv("AGENT_HUMAN_CHAT")
    assert "untracked-файл" in format_human_response(_LIVE)


_LIVE_2709 = (
    "Conclusion: Правка готова и проверена: в `tools/convert_file.py` добавлена операция "
    "`pdf_text` [topic-only:tool:patch_check]. Файла нет [tool:find_in_files]."
)


def test_code_quotes_and_tool_tags_do_not_reach_the_chat():
    """Живой случай 27.09, Телеграм: `кавычки кода` и [tool:find_in_files] приходили как есть."""
    human = format_human_response(_LIVE_2709)
    assert "`" not in human and "[" not in human, human
    assert "tools/convert_file.py" in human
    assert human.endswith("Часть этого я не проверял."), human
