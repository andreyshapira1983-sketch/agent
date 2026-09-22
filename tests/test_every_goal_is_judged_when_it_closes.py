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

import json
from datetime import datetime, timedelta, timezone
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


def _verdicts(workspace: Path) -> list[dict]:
    path = workspace / "data" / "campaign_verdicts.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _note(workspace: Path, rel: str) -> Path:
    path = workspace / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("конспект с цитатой и проверкой\n", encoding="utf-8")
    return path


def test_a_goal_that_kept_its_promise_is_recorded_as_verified(tmp_path: Path) -> None:
    rel = "data/notes/20260923T100000_competence_math.md"
    _note(tmp_path, rel)

    verdict = judge_closing_goal(
        _Agent(), workspace=tmp_path, goal="Прочитай раздел",
        success_check=f"Файл {rel} создан", cycle=7, why="goal_switch",
        started_at=_START - timedelta(hours=1), ts=_START,
    )

    assert verdict is not None
    assert verdict["verdict"] == "verified", verdict
    assert _verdicts(tmp_path), "показание не записано в журнал вердиктов"


def test_a_goal_whose_artifact_is_missing_is_not_called_done(tmp_path: Path) -> None:
    verdict = judge_closing_goal(
        _Agent(), workspace=tmp_path, goal="Прочитай раздел",
        success_check="Файл data/notes/nothing_here.md создан", cycle=3,
        why="goal_switch", started_at=_START, ts=_START,
    )

    assert verdict is not None and verdict["verdict"] != "verified"


def test_a_goal_without_a_criterion_is_left_unjudged(tmp_path: Path) -> None:
    """«Критерий не назван» — честное состояние; мерка задним числом хуже."""
    assert judge_closing_goal(
        _Agent(), workspace=tmp_path, goal="что-нибудь полезное",
        success_check="   ", cycle=1, why="goal_switch",
        started_at=_START, ts=_START,
    ) is None
    assert _verdicts(tmp_path) == []


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
