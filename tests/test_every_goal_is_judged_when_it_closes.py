"""Цель судится своим критерием, когда закрывается, а не раз за прогон.

Замер 2026-09-23 на живом сервере: реестр циклов держал 2685 записей, из них
754 со словом «completed», а вердиктов в `data/campaign_verdicts.jsonl` было
ТРИ — по одному на прогон, и все «критерий не назван». То есть «сделано»
семьсот пятьдесят четыре раза держалось на слове исполнителя: судья
(`core/campaign_verdict.py`) стоял только на выходе из прогона и видел лишь
последнюю цель.

Цель закрывается в момент смены. Там её критерий ещё в руках, и там же
известно, когда она началась, — без этой отметки чужой, более старый след
зачёлся бы ей как работа (`against_start`).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from core.campaign import judge_closing_goal


class _Agent:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    class _Log:
        def __init__(self, outer: _Agent) -> None:
            self.outer = outer

        def log(self, event: str, payload: dict) -> None:
            self.outer.events.append((event, payload))

    @property
    def log(self):
        return _Agent._Log(self)


#: Отсчёт берётся от НАСТОЯЩИХ часов: судья сверяет метки файлов на диске,
#: а выдуманная дата сделала бы свежий конспект «лежавшим здесь до цели».
_START = datetime.now(timezone.utc)


def test_the_judge_never_breaks_the_run(tmp_path: Path) -> None:
    """Показание важно, но прогон важнее: сбой судьи попадает в журнал."""
    agent = _Agent()

    assert judge_closing_goal(
        agent, workspace=object(), goal="цель", success_check="Файл x.md создан",
        cycle=2, why="goal_switch", started_at=_START, ts=_START,
    ) is None
    assert any(name == "campaign_goal_verdict_failed" for name, _ in agent.events)


def test_the_switch_closes_the_previous_goal_with_its_own_criterion() -> None:
    """Связь с механизмом: смена цели зовёт судью до подмены критерия."""
    import inspect

    from core import campaign

    source = inspect.getsource(campaign)
    switch = source.split("def _switch_goal", maxsplit=1)[1].split("\n    def ", maxsplit=1)[0]
    judged = switch.index("judge_closing_goal")
    replaced = switch.index("current_success_check = switched_check")
    assert judged < replaced, "цель судится уже новым критерием — мерка чужая"
