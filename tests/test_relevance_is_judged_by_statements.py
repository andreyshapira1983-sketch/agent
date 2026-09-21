"""Относимость ответа судит модель по утверждениям, а не счёт слов вопроса.

Замер 2026-09-21 на 126 ответах мостика: счёт слов предупреждал на 65 (52 %),
справедливо — около 16; тот же порог 0.35 закрывал этим ответам вход в опыт.
Судья по способу DeepEval (утверждения ответа, yes/no/idk): ниже 0.35 — 13,
все верно, включая два ответа на ПРЕДЫДУЩИЙ вопрос. Порог оператора прежний.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core.confidence_vector import compute_vector
from core.loop_verification import AgentLoopVerification
from core.relevance_judge import judge_relevance, parse_verdict

_REPORT = SimpleNamespace(total_chunks=4, verified_chunks=4, structural_chunks=0,
                          unverified_chunks=0, admitted_unverified_chunks=0)


def _raw(*verdicts: str) -> str:
    return json.dumps({"statements": [{"s": f"s{i}", "verdict": v} for i, v in enumerate(verdicts)]})


class _Judge:
    def __init__(self, raw: str):
        self.raw, self.calls = raw, []

    def complete(self, system, user, max_tokens=1, temperature=0.0, **flags):
        self.calls.append(flags)
        return self.raw


def test_the_score_is_the_share_of_statements_on_the_question() -> None:
    v = parse_verdict(_raw("yes", "idk", "no", "no"))
    assert v and v.score == 0.5 and v.statements == 4 and v.irrelevant == ("s2", "s3")


def test_a_reply_out_of_form_gives_no_score() -> None:
    assert parse_verdict("not json") is None
    assert parse_verdict('{"statements": []}') is None
    assert parse_verdict('{"statements": [{"verdict": "maybe"}]}') is None


def test_switched_off_the_judge_is_never_called(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_RELEVANCE_JUDGE", raising=False)
    judge = _Judge(_raw("yes"))
    assert judge_relevance(judge, "вопрос", "ответ") is None
    assert judge.calls == []


def test_switched_on_it_asks_for_json_and_scores(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_RELEVANCE_JUDGE", "1")
    judge = _Judge(_raw("yes", "no"))
    v = judge_relevance(judge, "вопрос", "ответ")
    assert v and v.score == 0.5
    assert judge.calls[0].get("json_object") is True


def test_a_long_conversational_question_answered_exactly_is_not_off_topic() -> None:
    """Эпизод 2026-09-20 18:06: счёт слов 0.14, ответ отвечал (12 из 13)."""
    question = ("Все три ответа приняты, и они хорошие. Мера у тебя соотношение, а не "
                "ярлык. Посчитай, сколько раз ты сегодня вызвал web_fetch и web_search.")
    answer = ("Мои замеры по журналам дают ноль вызовов обоих инструментов — числа "
              "не совпали с вашими, и вот почему поиск их не видит.")
    words = compute_vector(report=_REPORT, disagreements=[], question=question, answer=answer)
    judged = compute_vector(report=_REPORT, disagreements=[], question=question,
                            answer=answer, judged_relevance=12 / 13)
    assert words.relevance_score < 0.35 <= judged.relevance_score
    assert judged.relevance_applicable


class _Loop(AgentLoopVerification):
    def __init__(self, judge: object):
        self.model_router = SimpleNamespace(for_role=lambda role: judge)
        self.events: list[tuple[str, dict]] = []
        self.failed: list[str] = []
        self.log = SimpleNamespace(log=lambda name, payload: self.events.append((name, payload)))

    def _sensor_failed(self, name, exc):
        self.failed.append(name)


def test_the_loop_logs_the_judge_beside_the_word_count(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_RELEVANCE_JUDGE", "1")
    loop = _Loop(_Judge(_raw("yes", "yes", "no")))
    assert loop._judged_relevance("какой размер файла", "Размер — 10 байт.") == pytest.approx(2 / 3)
    name, payload = loop.events[0]
    assert name == "relevance_judge" and "lexical" in payload and payload["statements"] == 3


def test_a_failing_judge_leaves_the_turn_standing(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_RELEVANCE_JUDGE", "1")

    class _Down:
        def complete(self, **kwargs):
            raise ConnectionError("provider down")

    loop = _Loop(_Down())
    assert loop._judged_relevance("вопрос", "ответ") is None
    assert loop.failed == ["relevance_judge"]
