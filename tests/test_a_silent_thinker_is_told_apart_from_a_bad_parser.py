"""Три причины «модель не вернула цель» различимы по сырому ответу API.

Замер 2026-09-03 (прогон charter_day4_reasoner, планировщик deepseek-reasoner
= V4 Flash в thinking-режиме): первый выбор цели прошёл, попытка смены цели
кончилась словами «the model returned no parseable goal» после 14 335 выходных
токенов и 112 секунд. Одна фраза на три разных болезни:

  (a) НЕТ ФИНАЛЬНОГО ОТВЕТА — размышление съело весь бюджет. Сырой ответ:
      content пустой, reasoning_content есть, finish_reason=length.
      Замерено сырой пробой на точном промпте выбора цели: при max_tokens=1200
      — 2 раза из 2 (reasoning_tokens=1200=всё); при 8192 (пол реестра
      молчавших, в котором deepseek-reasoner уже стоит) — 1 раз из 3
      (37 667 символов мыслей, ноль ответа). Лотерея, которую видели вживую.
  (b) ПОТЕРЯ ТРАНСПОРТА — API вернул content, но наш клиент его не донёс.
  (c) ОШИБКА РАЗБОРА — content дошёл, но разбор «от первой { до последней }»
      споткнулся о фигурные скобки в прозе перед JSON.

Клиент УЖЕ различает (a): `complete()` возвращает "" и ставит
`last_answer_was_truncated=True`. Различение теряется на шве charter_goal —
там любая пустота и любой сбой разбора называются одним словом.
Свидетели ниже красные ровно там, где различение потеряно, и зелёные там,
где оно живо (транспорт), — чтобы ремонт бил в шов, а не в клиент.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core.charter_goal import CHARTER_RELPATH, propose_charter_goal
from core.llm import LLM

_CHARTER = (
    "# Corporate Model — FUTURE / TARGET\n"
    "The organisation exists only when roles, authority and evidence are "
    "explicit, and approval of escalated actions stays with a human.\n"
)

_GOAL = "Trace one recorded false success end to end and name the missing receipt"
_REPLY = {"goal": _GOAL, "anchor_id": 0, "why_now": "measured", "success_check": "a rule"}


class _RawAPI:
    """OpenAI-совместимый клиент, отдающий заранее заданные СЫРЫЕ ответы."""

    def __init__(self, shapes: list[tuple[str | None, str, str]]):
        self._shapes = list(shapes)
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        content, reasoning, finish = (
            self._shapes.pop(0) if self._shapes else (None, "still thinking", "length")
        )
        message = SimpleNamespace(content=content, reasoning_content=reasoning)
        return SimpleNamespace(
            model="deepseek-v4-flash",
            choices=[SimpleNamespace(message=message, finish_reason=finish)],
            usage=SimpleNamespace(prompt_tokens=8000, completion_tokens=1200),
        )


@pytest.fixture
def thinker(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    # Реестр молчавших — в песочницу: свидетель не должен учить живой реестр.
    monkeypatch.setenv("AGENT_REASONING_ROSTER", str(tmp_path / "roster.jsonl"))
    return LLM(provider="deepseek", model="deepseek-reasoner")


def _workspace(tmp_path):
    charter = tmp_path / CHARTER_RELPATH
    charter.parent.mkdir(parents=True, exist_ok=True)
    charter.write_text(_CHARTER, encoding="utf-8")
    return tmp_path


@pytest.mark.xfail(
    reason=(
        "KNOWN GAP, measured 2026-09-03 and banked rather than fixed (RED witness "
        "by the operator's word): charter_goal names an empty, truncated reply "
        "('' + last_answer_was_truncated=True — reasoning consumed the budget) "
        "with the same words as a parse failure. Raw evidence: deepseek-reasoner "
        "on the exact goal prompt, finish_reason=length, content='' — 2/2 at "
        "max_tokens=1200, 1/3 at the 8192 roster floor. Minimal repair proposed, "
        "not applied: read llm.last_answer_was_truncated at the charter_goal seam "
        "and decline with 'no final output (reasoning consumed the budget)'. "
        "[until: 2026-09-30 — перемерь закреплённую дыру; чини или пере-датируй явным коммитом]"
    ),
    strict=True,
)
def test_a_silent_thinker_is_named_as_silent_not_as_unparseable(thinker, tmp_path):
    """(a) Сырой ответ: content=None, reasoning есть, finish_reason=length —
    на КАЖДОМ плече, включая продолжения с удвоенным бюджетом."""
    api = _RawAPI([(None, "…thinking…", "length")] * 8)
    thinker._client = api

    report = propose_charter_goal(thinker, _workspace(tmp_path))

    # Клиент своё различение сделал: пусто И обрезано.
    assert thinker.last_answer_was_truncated is True
    assert len(api.calls) >= 2, "после пустого обрезанного плеча бюджет должен расти"
    assert api.calls[-1]["max_tokens"] > api.calls[0]["max_tokens"]
    # Шов charter_goal обязан ЭТО различение донести до причины отказа.
    assert report.status == "declined"
    assert "no parseable" not in report.reason, (
        "пустой ответ с finish_reason=length — это «размышление съело бюджет», "
        f"а не ошибка разбора; причина: {report.reason!r}"
    )
    assert any(word in report.reason.lower() for word in ("final output", "reasoning", "truncat")), (
        f"причина обязана назвать отсутствие финального ответа: {report.reason!r}"
    )


def test_a_thinker_whose_answer_arrived_is_proposed(thinker, tmp_path):
    """(b) Контроль транспорта: content пришёл рядом с reasoning_content —
    клиент обязан донести его, и цель предлагается. Зелёный по построению:
    доказывает, что потеря НЕ в транспорте."""
    api = _RawAPI([(json.dumps(_REPLY), "…thinking…", "stop")])
    thinker._client = api

    report = propose_charter_goal(thinker, _workspace(tmp_path))

    assert report.status == "proposed", report.reason
    assert report.goal == _GOAL
    assert len(api.calls) == 1, "полный ответ не требует продолжений"


@pytest.mark.xfail(
    reason=(
        "KNOWN GAP, measured 2026-09-03 and banked rather than fixed (RED witness "
        "by the operator's word): the goal parser takes the FIRST '{' to the "
        "LAST '}' of the reply, so braces in prose before the final JSON turn a "
        "valid answer into 'no parseable goal'. Not the live cause on 2026-09-03 "
        "(that was silence, see the test above) — banked as the third shape the "
        "one error message hides. Minimal repair proposed, not applied: scan for "
        "the last complete JSON object (json.JSONDecoder.raw_decode from each '{'). "
        "[until: 2026-09-30 — перемерь закреплённую дыру; чини или пере-датируй явным коммитом]"
    ),
    strict=True,
)
def test_a_final_json_after_braces_in_prose_is_still_parsed(thinker, tmp_path):
    """(c) Разбор: content дошёл, но перед финальным JSON есть проза с фигурными
    скобками (думающие модели так делают, когда рассуждение просачивается в
    content). Разбор «первая { … последняя }» захватывает мусор и падает —
    и падение называется тем же словом, что и (a)."""
    content = (
        "Considered {draft: split the router} first — rejected as a repeat.\n"
        "Final answer:\n" + json.dumps(_REPLY)
    )
    api = _RawAPI([(content, "…thinking…", "stop")])
    thinker._client = api

    report = propose_charter_goal(thinker, _workspace(tmp_path))

    assert report.status == "proposed", (
        f"последний полный JSON-объект в ответе обязан быть найден; причина: {report.reason!r}"
    )
    assert report.goal == _GOAL
