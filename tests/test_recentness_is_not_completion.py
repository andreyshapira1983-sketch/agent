"""Двери различают запрет и временную остановку: recentness ≠ completion.

Третье проявление одной болезни «event ≠ outcome», замерено 2026-09-01/02:
сначала «цель выбрана → считалась выполненной» (клинч бюджета и повторов),
теперь «тема упомянута → считается занятой». Живой контрпример: агент сам
написал документ о непрерывности своей идентичности и сам пометил его
незавершённым («вернуться, когда появится веб»). Веб появился. Его же попытка
вернуться — первая цель ночного прогона — была отвергнута стражем повторов:
система наказала агента ровно за правильное планирование незавершённой работы.

Семантика по слову оператора (2026-09-02, дословный грант): «факт выбора или
упоминания темы не должен означать, что работа по ней завершена. Незакрытая
тема должна быть повторно допустима при изменении blocker/capability или при
наличии нового evidence». Окно recent НЕ расширяется (это сделало бы хуже),
лимит длины НЕ меняется; меняется только то, ЧТО страж считает повтором.

Машинный носитель «изменения capability» — журнал data/capability_events.jsonl:
строка появляется, когда состав заблокированных инструментов безнадзорного
пути изменился (так 2026-09-01 открылось чтение веба). Тема, чья последняя
РАБОТА старше последнего такого события, снова допустима: вердикт «здесь
больше нечего делать» выносился в другом мире.
"""
from __future__ import annotations

import json

from core.capability_events import (
    last_capability_change_ts,
    record_capability_snapshot,
)
from core.charter_goal import CHARTER_RELPATH, propose_charter_goal

_CHARTER = (
    "# Corporate Model — FUTURE / TARGET\n"
    "The organisation exists only when roles, authority and evidence are "
    "explicit, and approval of escalated actions stays with a human.\n"
)

_STUDY_GOAL = (
    "Study external literature on agent identity continuity across model "
    "replacement, and record evidence-backed parallels"
)


class _LLM:
    """Отвечает заранее заданной целью; ловит каждый промпт."""

    def __init__(self, replies: list[dict]):
        self._replies = list(replies)
        self.prompts: list[str] = []

    def complete(self, *, system: str, user: str, **_kw) -> str:
        # Причина отказа может прийти и системной частью — ловим обе.
        self.prompts.append(system + "\n" + user)
        reply = self._replies.pop(0) if self._replies else {}
        return json.dumps(reply, ensure_ascii=False)


def _reply(goal: str = _STUDY_GOAL, **overrides) -> dict:
    base = {
        "goal": goal,
        "anchor_id": 0,
        "why_now": "the open question in my own document is still open",
        "success_check": "external claims replaced by verified or refuted ones",
    }
    base.update(overrides)
    return base


def _workspace(tmp_path, *, completed_goal: str | None = None,
               completed_ts: str = "2026-09-01T19:51:16+00:00"):
    charter = tmp_path / CHARTER_RELPATH
    charter.parent.mkdir(parents=True, exist_ok=True)
    charter.write_text(_CHARTER, encoding="utf-8")
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    rows = []
    if completed_goal:
        rows.append({"goal": completed_goal, "result": "completed",
                     "work_done": True, "ts": completed_ts})
    (data / "campaign_ledger.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )
    return tmp_path


def test_a_completed_theme_still_blocks_when_the_world_is_unchanged(tmp_path):
    """Дифференциальная сторона A: без изменения мира повтор запрещён как был."""
    ws = _workspace(tmp_path, completed_goal=_STUDY_GOAL)

    report = propose_charter_goal(_LLM([_reply()]), ws)

    assert report.status == "declined"
    assert "repeats" in report.reason


def test_a_capability_change_reopens_an_earlier_theme(tmp_path):
    """Дифференциальная сторона B: мир изменился ПОСЛЕ завершения — тема снова
    допустима. Это ровно случай его continuity-документа: веб открылся после
    того, как работа была «завершена» без веба."""
    ws = _workspace(tmp_path, completed_goal=_STUDY_GOAL,
                    completed_ts="2026-09-01T19:51:16+00:00")
    record_capability_snapshot(ws, frozenset({"spawn_subagent"}))
    record_capability_snapshot(
        ws, frozenset({"spawn_subagent", "python_probe"}),
    )

    report = propose_charter_goal(_LLM([_reply()]), ws)

    assert report.status == "proposed", (
        "вердикт «здесь больше нечего делать» выносился в другом мире; "
        f"отказ: {report.reason!r}"
    )
    assert report.goal == _STUDY_GOAL


def test_work_done_after_the_change_blocks_again(tmp_path):
    """Повторная допустимость — одноразовая: новая работа по теме в НОВОМ мире
    снова занимает её. Иначе одно событие открывало бы тему навсегда."""
    record_capability_snapshot(tmp_path, frozenset({"spawn_subagent"}))
    change_ts = last_capability_change_ts(tmp_path)
    ws = _workspace(tmp_path, completed_goal=_STUDY_GOAL,
                    completed_ts="2099-01-01T00:00:00+00:00")
    assert change_ts < "2099"

    report = propose_charter_goal(_LLM([_reply()]), ws)

    assert report.status == "declined"


def test_the_snapshot_journal_grows_only_on_change(tmp_path):
    """Журнал событий — не пульс: одинаковый состав не плодит строк."""
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    first = record_capability_snapshot(tmp_path, frozenset({"a", "b"}))
    same = record_capability_snapshot(tmp_path, frozenset({"b", "a"}))
    changed = record_capability_snapshot(tmp_path, frozenset({"a"}))

    assert first is True and same is False and changed is True
    rows = (tmp_path / "data" / "capability_events.jsonl").read_text(
        encoding="utf-8").strip().splitlines()
    assert len(rows) == 2


def test_an_overlong_goal_is_reformulated_not_fatal(tmp_path):
    """Policy цела, recovery добавлена: >300 отвергается, но агент получает
    шанс семантически сократить ТУ ЖЕ цель и попытка повторяется.

    Замер 2026-09-02T09:03: содержательно годная цель в 312 символов привела к
    полной остановке прогона. Правильный исход — не поднять лимит до 400
    (это просто удалило бы препятствие), а: rejected with reason →
    reformulates same semantic goal ≤300 → accepted.
    """
    long_goal = "Проследить " + "очень " * 55 + "длинную цель"  # > 300
    assert len(long_goal) > 300
    short_goal = "Проследить ту же цель коротко и по делу"
    llm = _LLM([_reply(goal=long_goal), {"goal": short_goal}])

    report = propose_charter_goal(llm, _workspace(tmp_path))

    assert report.status == "proposed"
    assert report.goal == short_goal
    assert len(llm.prompts) == 2, "должна быть вторая попытка — сокращение"
    assert "300" in llm.prompts[1], "причина отказа обязана дойти до модели"


def test_a_goal_that_stays_overlong_is_still_declined(tmp_path):
    """Лимит не обходится: если и сокращение длиннее 300 — честный отказ."""
    long_goal = "Проследить " + "очень " * 55 + "длинную цель"
    llm = _LLM([_reply(goal=long_goal), {"goal": long_goal}])

    report = propose_charter_goal(llm, _workspace(tmp_path))

    assert report.status == "declined"
    assert "300" in report.reason
