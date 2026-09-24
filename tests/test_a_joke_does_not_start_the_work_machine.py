"""Шутка не запускает рабочий конвейер (слово оператора 24.09).

Замер того дня: на лёгкую реплику агент делал пять кругов поиска по журналам,
собирал 55 улик и отвечал списком с пометками и строкой «Проверка: …».
Словарь приветствий расти больше не может (урок AIML/ALICE в записке органа
разговора), поэтому болтовню узнаёт дешёвый классификатор — и только там, где
для ответа не нужен ни один факт.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.loop_response_deciders import AgentLoopResponseDeciders
from core.model_usage import ModelBudgetExceeded
from core.response_draft import ResponseDraft
from core.social_turn import ENV_FLAG, is_social_turn


@pytest.fixture(autouse=True)
def _conversation_channel(monkeypatch):
    """Мостик разговора включает классификатор; здесь — как в разговоре."""
    monkeypatch.setenv(ENV_FLAG, "1")


class _Llm:
    def __init__(self, answer):
        self.answer, self.calls = answer, 0

    def complete(self, **_kw):
        self.calls += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def _loop(answer):
    llm = _Llm(answer)
    logged: list = []
    loop = SimpleNamespace(model_router=SimpleNamespace(for_role=lambda _role: llm),
                           log=SimpleNamespace(log=lambda e, p: logged.append((e, p))))
    return loop, llm, logged


def test_a_joke_is_recognised_as_small_talk() -> None:
    loop, llm, logged = _loop('{"social": true}')
    assert is_social_turn(loop, "Расскажи анекдот про роботов") is True
    assert llm.calls == 1 and logged[0][0] == "social_turn_classified"


def test_a_question_needing_facts_stays_on_the_work_path() -> None:
    loop, _llm, _ = _loop('{"social": false}')
    assert is_social_turn(loop, "Какой самый нелепый промах ты сделал сегодня?") is False


def test_no_model_call_when_a_tool_is_obviously_needed() -> None:
    for text, hint in (("Прочитай файл README и пошути", None), ("Как настроение?", "README.md"),
                       ("Как дела? " + "очень " * 80, None)):
        loop, llm, _ = _loop('{"social": true}')
        assert is_social_turn(loop, text, file_hint=hint) is False
        assert llm.calls == 0, text


def test_doubt_or_failure_means_the_normal_path() -> None:
    for answer in ("не знаю", '{"social": "maybe"}', RuntimeError("down")):
        loop, _, _ = _loop(answer)
        assert is_social_turn(loop, "Как настроение?") is False


def test_outside_the_conversation_channel_no_model_is_called(monkeypatch) -> None:
    """Цели кампании болтовнёй не бывают: без переключателя — ни одного вызова."""
    monkeypatch.delenv(ENV_FLAG, raising=False)
    loop, llm, _ = _loop('{"social": true}')
    assert is_social_turn(loop, "Расскажи анекдот про роботов") is False
    assert llm.calls == 0


def test_an_exhausted_budget_stops_the_turn_not_the_classifier() -> None:
    """Первый прогон 24.09: сбой классификатора глотал исчерпанный бюджет."""
    loop, _, _ = _loop(ModelBudgetExceeded("limit"))
    with pytest.raises(ModelBudgetExceeded):
        is_social_turn(loop, "Как настроение?")


def test_small_talk_recognised_by_the_classifier_carries_no_report_tail() -> None:
    loop, _, _ = _loop('{"social": true}')
    assert is_social_turn(loop, "Расскажи анекдот про роботов")
    logged: list = []
    loop.last_verification, loop.last_provenance = object(), None
    loop.last_confidence_vector = loop.last_evidence_support = None
    loop.log = SimpleNamespace(log=lambda e, p: logged.append(e))
    draft = ResponseDraft(body="Робот заходит в бар…")
    AgentLoopResponseDeciders._add_verification_summary(loop, draft, "Расскажи анекдот про роботов")
    assert "verification_tail_skipped" in logged
    assert "Проверка:" not in draft.render()


def test_the_loop_asks_the_classifier_at_the_cheap_path_gate() -> None:
    src = (Path(__file__).resolve().parents[1] / "core" / "loop_attempt.py").read_text(encoding="utf-8")
    assert "or is_social_turn(self, st.user_question, file_hint=st.file_hint))" in src
