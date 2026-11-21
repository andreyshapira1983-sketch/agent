"""Цикл, чью работу отвергли, не смеет зваться полезным — и не съедает попытку.

Замер, перепись потребителей и нормы: MIR-117 в docs/audit/MASTER_ISSUE_REGISTRY.md.

Норма A (ратифицирована 2026-08-22): «обработка закончилась» и «работа
сделана» — разные факты, и агент обязан их различать. Живой случай: прогон,
чью единственную задачу отверг ценовой конверт, записал `result=completed`,
`useful_cycles=1`, а повтор был запрещён со словами «прежний проход не снял
сигнал» — при том что прежний проход НЕ ЗАПУСКАЛСЯ.

Границы решений здесь:
- полезный цикл = работа сделана (продукт или слово производителя), не «очередь
  дочерпана»;
- подпись действия банится ПОПЫТКОЙ: запуск, который потратил хоть что-то или
  дал продукт. Отказ до старта (0 трат, 0 продукта) попыткой не является;
- «строка результата сменилась» — не прогресс (был третьей ногой productive).

Сжигание одноразового одобрения и списание стоячего гранта НЕ трогаются:
переживает ли «да» неудавшуюся попытку — вопрос, оставленный оператором за
собой (запись MIR-117, его слова).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from core.autonomous_runtime_types import (
    AutonomousQueuedTaskReport,
    AutonomousQueueRunReport,
)
from core.best_next_action import BestNextAction
from core.campaign import CampaignActionOutcome, CampaignConfig, run_campaign


def _task(status: str) -> AutonomousQueuedTaskReport:
    return AutonomousQueuedTaskReport(
        task_id="t1", goal="g", status=status, run_status=status)


def _useful() -> BestNextAction:
    return BestNextAction(
        action="propose_minimal_test_repair",
        title="Propose one minimal fix",
        severity="high", priority=80,
        reason="tests are failing with concrete names",
        risk="reversible",
    )


class _Gather:
    def __call__(self, agent, workspace, approval_inbox):
        return {"action": _useful()}


class _ScriptedExecute:
    """Возвращает исходы по очереди; последний — навсегда."""

    def __init__(self, outcomes: list[CampaignActionOutcome]):
        self._outcomes = outcomes
        self.calls = 0

    def __call__(self, *, agent, workspace, action, config, approval_inbox=None):
        idx = min(self.calls, len(self._outcomes) - 1)
        self.calls += 1
        return self._outcomes[idx]


def _run(tmp_path: Path, execute, max_cycles: int = 4):
    return run_campaign(
        CampaignConfig(goal="g", max_cycles=max_cycles, max_unproductive_streak=2),
        agent=SimpleNamespace(log=None),
        workspace=str(tmp_path),
        gather_signals=_Gather(),
        execute_action=execute,
        now_fn=lambda: datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc),
    )


def test_a_hollow_queue_report_mints_failed_not_completed() -> None:
    """Красный свидетель самой чеканки: отвергнутая работа ≠ completed."""
    hollow = AutonomousQueueRunReport(status="completed", processed=[_task("failed")])
    done = AutonomousQueueRunReport(status="completed", processed=[_task("done")])
    stopped_mid = AutonomousQueueRunReport(
        status="stopped", processed=[_task("done")], stop_reason="budget")
    empty = AutonomousQueueRunReport(status="empty", processed=[])

    assert hollow.semantic_result() == ("failed", False)
    assert done.semantic_result() == ("completed", True)
    assert stopped_mid.semantic_result() == ("stopped", True)
    assert empty.semantic_result() == ("empty", False)


def test_the_live_run_report_mints_the_same_vocabulary() -> None:
    """Живая артерия кампании несёт ДРУГОЙ тип отчёта — MIR-116 шёл через него.

    Вскрыто заглушкой в соседнем тесте: первый заход чеканил словарь только на
    отчёте очереди, и сквозной свидетель прошёл мимо runtime.run().
    """
    from types import SimpleNamespace as NS

    from core.autonomous_runtime_types import AutonomousRunReport

    def _report(status: str, task_statuses: list[str]) -> AutonomousRunReport:
        return AutonomousRunReport(
            status=status, dry_run=True, goal="g",
            tasks=[NS(status=s) for s in task_statuses],
            budget={}, circuit={}, approvals={},
        )

    assert _report("completed", ["failed"]).semantic_result() == ("failed", False)
    assert _report("completed", ["done"]).semantic_result() == ("completed", True)
    assert _report("stopped", ["done", "failed"]).semantic_result() == ("stopped", True)
    assert _report("completed", ["skipped"]).semantic_result() == ("empty", False)


def test_a_cycle_that_did_no_work_is_not_useful(tmp_path: Path) -> None:
    """Пустой «completed» больше не покупает useful_cycles."""
    hollow = CampaignActionOutcome(result="completed")

    result = _run(tmp_path, _ScriptedExecute([hollow]))

    assert result.totals["useful_cycles"] == 0


def test_a_failed_attempt_that_spent_is_banked(tmp_path: Path) -> None:
    """Запуск, который потратил и упал, — попытка: повтор в эту кампанию запрещён."""
    spent_and_failed = CampaignActionOutcome(result="failed", cost_units_spent=5)
    execute = _ScriptedExecute([spent_and_failed])

    result = _run(tmp_path, execute)

    assert execute.calls == 1
    assert any(r.result == "repeat" for r in result.records)
