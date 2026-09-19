"""Ожидание не стирает улики зацикливания.

WHY THIS EXISTS. Аудит автономности 2026-09-17, находка 8. `_wait_for_change`
(core/campaign.py) в конце обнулял ВСЁ, чем кампания измеряет собственную
безуспешность: `idle_streak`, `streak_repeats`, `unproductive_streak`,
`attempted_signatures`, `action_steps` и `goal_switches`.

Ожидание — не событие мира. Когда пробуждение произошло по периодической
перепроверке, то есть НИЧЕГО не изменилось, обнуление превращало потолки в
украшение: двенадцать смен цели, ожидание, ещё двенадцать смен, ожидание —
десять часов перебора тем, и ни один датчик застоя не срабатывал, потому что
каждая пауза возвращала счётчики к нулю.

Граница права на сброс: НАСТОЯЩЕЕ изменение мира (новая строка в
`data/capability_events.jsonl` или новое одобрение в ящике) вправе открыть
новую эру выбора — прежний вердикт «здесь больше нечего делать» вынесен в
другом мире. Пустое ожидание такого права не даёт.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from core.campaign import _MAX_GOAL_SWITCHES, run_campaign
from core.campaign_ledger import CampaignLedger
from core.campaign_types import CampaignActionOutcome, CampaignConfig


class _OneActionGather:
    """Всегда одно и то же действие над одним предметом: топливо для повторов."""

    def __call__(self, agent, workspace, approval_inbox, goal="",
                 exhausted_actions=frozenset()):
        return {"action": SimpleNamespace(
            action="propose_engineering_task", title="one thing", severity="medium",
            priority=59, risk="reversible", grounds="operator_goal",
            decided_by="test", next_check_at=None, reason="",
        )}


class _WorksOnce:
    def __call__(self, *, agent, workspace, action, config, approval_inbox=None):
        return CampaignActionOutcome(
            result="completed", llm_calls_spent=1, cost_units_spent=1,
            subject="the-only-subject", work_done=True,
        )


def _endless_goals() -> Any:
    counter = {"n": 0}

    def _next() -> str:
        counter["n"] += 1
        return f"цель №{counter['n']}"

    return _next


def _run(tmp_path: Path, *, next_goal, max_cycles: int, pace: float = 0.0,
         sleep_fn=lambda _s: None):
    return run_campaign(
        CampaignConfig(goal="цель №0", max_cycles=max_cycles, max_idle_streak=3,
                       dry_run=False, cycle_pause_seconds=pace),
        agent=SimpleNamespace(log=None),
        workspace=str(tmp_path),
        gather_signals=_OneActionGather(),
        execute_action=_WorksOnce(),
        ledger=CampaignLedger(),
        next_goal=next_goal,
        now_fn=lambda: datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc),
        sleep_fn=sleep_fn,
    )


def test_loop_counters_are_not_reset_by_waiting(tmp_path: Path) -> None:
    """Красный свидетель: пустое ожидание возвращало кампании право на новые
    двенадцать смен цели — и так без конца.

    Здесь неиссякаемый источник новых целей и работа, которая всегда упирается
    в повтор. Мир при этом не меняется НИ РАЗУ. Значит число тем, которые
    кампания вправе перебрать, ограничено потолком смен, а не длиной прогона.
    """
    result = _run(tmp_path, next_goal=_endless_goals(), max_cycles=120)

    goals = {r.goal for r in result.records}
    assert len(goals) <= _MAX_GOAL_SWITCHES + 1, (
        f"кампания перебрала {len(goals)} тем при потолке "
        f"{_MAX_GOAL_SWITCHES + 1}: ожидание обнуляет счётчик смен"
    )
    # Исчерпав право на смену, кампания не умирает, а ждёт (блок 8) — но ждёт
    # ОДНУ тему, а не перебирает новые. Хвост прогона это и показывает.
    assert result.records[-1].result == "waiting"
    assert result.records[-1].goal == result.records[-2].goal


def test_waiting_still_happens_and_is_recorded(tmp_path: Path) -> None:
    """Сохранение улик не отменяет само ожидание: блок 8 остаётся в силе."""
    result = _run(tmp_path, next_goal=lambda: "", max_cycles=12)

    assert any(r.result == "waiting" for r in result.records)
    assert result.stop_reason.startswith("shift_limit:"), result.stop_reason


def test_a_real_world_change_may_open_a_new_era(tmp_path: Path) -> None:
    """Обратная сторона: настоящая перемена мира ВПРАВЕ обнулить то, что от
    мира и зависит.

    Вердикт «по этой теме больше нечего делать» вынесен в прежнем мире; когда
    состав доступных инструментов изменился, запирать тему нечестно (MIR о
    журнале перемен, core/capability_events.py). Здесь мир меняется во время
    КАЖДОГО ожидания, и кампания вправе перебрать больше тем, чем даёт один
    потолок смен.
    """
    from core.capability_events import record_capability_snapshot

    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    # Точка отсчёта: первая строка журнала переменой не считается.
    record_capability_snapshot(tmp_path, frozenset({"web_search"}))
    changes = {"n": 0}

    def _sleep(_seconds: float) -> None:
        changes["n"] += 1
        record_capability_snapshot(tmp_path, frozenset({f"tool_{changes['n']}"}))

    result = _run(tmp_path, next_goal=_endless_goals(), max_cycles=120,
                  pace=1.0, sleep_fn=_sleep)

    assert changes["n"] > 0, "ожидание не дошло до сна — тест ничего не проверил"
    goals = {r.goal for r in result.records}
    assert len(goals) > _MAX_GOAL_SWITCHES + 1, (
        "перемена мира обязана открывать новую эру выбора, иначе агент заперт "
        "вердиктами, вынесенными в другом мире"
    )


def test_an_unlimited_switch_budget_keeps_choosing_instead_of_sleeping(tmp_path: Path) -> None:
    """Суточный прогон 2026-09-19: двенадцать смен кончились за час с
    небольшим, дальше кампания спала до «перемены мира». Оператор: «мне не
    надо, чтобы он спал». `max_goal_switches=0` — потолка нет: исчерпав цель,
    агент выбирает следующую сам. По умолчанию потолок прежний (тест выше)."""
    assert CampaignConfig().max_goal_switches == _MAX_GOAL_SWITCHES
    result = run_campaign(
        CampaignConfig(goal="цель №0", max_cycles=120, max_idle_streak=3, dry_run=False,
                       max_goal_switches=0),
        agent=SimpleNamespace(log=None), workspace=str(tmp_path),
        gather_signals=_OneActionGather(), execute_action=_WorksOnce(), ledger=CampaignLedger(),
        next_goal=_endless_goals(),
        now_fn=lambda: datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc), sleep_fn=lambda _s: None,
    )
    goals = {r.goal for r in result.records}
    assert len(goals) > _MAX_GOAL_SWITCHES + 1, "the unlimited budget still stopped at the default ceiling"
    assert not any(r.result == "waiting" for r in result.records), "it slept although it could choose"
