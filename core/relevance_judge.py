"""Судья относимости: отвечает ли ответ на заданный вопрос — по утверждениям.

Прежняя мера (`core/confidence_vector.relevance_score`) — доля слов вопроса,
встреченных в ответе. Длинный разговорный вопрос несёт десятки слов
обращения («тебе», «сегодня», «значит»), и точный ответ получает 0.2–0.4.
Замер 2026-09-21 на 126 ответах мостика: предупреждение «может отвечать не на
заданный вопрос» — на 65 (52 %), справедливых около 16; и тот же порог
закрывал этим ответам вход в опыт (`core/smart_memory.py`).

Способ взят у DeepEval (AnswerRelevancy): модель выделяет утверждения ответа
и о каждом говорит yes / no / idk — относится ли оно к тому, что спрошено;
оценка = (yes + idk) / всего, «idk» не штрафуется. Тот же замер: ниже порога
0.35 — 13 ответов, все 13 верно (7 встречных уточнений вместо работы, 2
неотправленных черновика, 2 сбоя, 2 ответа на ПРЕДЫДУЩИЙ вопрос). Цена —
один вызов модели на ответ.

Нет оценки (None) — работает прежняя мера. Здесь None значит: выключен,
пусто, ответ судьи не по форме. Сбой САМОГО вызова (поставщик недоступен)
этот модуль не перехватывает — его ловит цикл
(`core/loop_verification._judged_relevance`: журнал `sensor_failed`, None),
чтобы сбой был виден, а не проглочен. Поправка по находке самого агента
2026-09-22: прежний текст обещал здесь то, чего в этом файле нет.
Включается `AGENT_RELEVANCE_JUDGE=1` (в `.env` рабочей папки); без него —
ни одного вызова, как в тестах.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

#: Сколько текста вопроса и ответа видит судья. Ответы мостика — до ~8 000
#: знаков; дальше обычно хвосты проверки, а не суть.
_QUESTION_CHARS = 6_000
_ANSWER_CHARS = 8_000
_MAX_TOKENS = 3_000

_SYSTEM = """You are an evaluation judge. Output JSON only.
Task: judge whether an assistant's ANSWER addresses the user's QUESTION.
Step 1: split the ANSWER into its distinct statements (max 15; merge trivia).
Step 2: for each statement give verdict "yes" (relevant to what the question asks),
"no" (irrelevant / answers something else), or "idk" (supporting remark, ambiguous).
A statement that says the request could not be done and why IS relevant.
Return JSON: {"statements":[{"s":"...","verdict":"yes|no|idk"}]}"""


@dataclass(frozen=True)
class RelevanceVerdict:
    score: float
    statements: int
    irrelevant: tuple[str, ...]

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 3),
            "statements": self.statements,
            "irrelevant": list(self.irrelevant[:4]),
        }


def judge_enabled() -> bool:
    return (os.getenv("AGENT_RELEVANCE_JUDGE", "") or "").strip().lower() in {
        "1", "true", "yes", "on"}


def parse_verdict(raw: str) -> RelevanceVerdict | None:
    """Ответ судьи → оценка; всё, что не по форме, — None."""
    try:
        data = json.loads(raw or "")
    except json.JSONDecodeError:
        return None
    items = data.get("statements") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None
    verdicts = [(str(i.get("verdict", "")).strip().lower(), str(i.get("s", "")))
                for i in items if isinstance(i, dict)]
    verdicts = [(v, s) for v, s in verdicts if v in {"yes", "no", "idk"}]
    if not verdicts:
        return None
    kept = sum(1 for v, _ in verdicts if v != "no")
    return RelevanceVerdict(
        score=kept / len(verdicts),
        statements=len(verdicts),
        irrelevant=tuple(s[:160] for v, s in verdicts if v == "no"),
    )


def judge_relevance(llm: Any, question: str | None, answer: str | None) -> RelevanceVerdict | None:
    """Один вызов судьи. None — оценки нет (выключен, пусто, сбой, не по форме)."""
    if llm is None or not judge_enabled():
        return None
    question, answer = (question or "").strip(), (answer or "").strip()
    if not question or not answer:
        return None
    from core.llm import accepted_flags

    user = f"QUESTION:\n{question[:_QUESTION_CHARS]}\n\nANSWER:\n{answer[:_ANSWER_CHARS]}"
    raw = llm.complete(
        system=_SYSTEM, user=user, max_tokens=_MAX_TOKENS, temperature=0.0,
        **accepted_flags(llm.complete, {"json_object": True, "allow_continuation": False}),
    )
    return parse_verdict(raw)
