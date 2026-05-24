"""
brain/state/recovery.py — Восстановление после краша (Crash Recovery)

Что происходит при падении агента:
    1. Несколько TaskSession остаются в статусе RUNNING/RECOVERING в TaskStore
    2. PlanCheckpointStore содержит последний сохранённый план с шагами
    3. IdempotencyStore помнит какие инструменты уже были вызваны успешно

Что делает RecoveryManager при старте:
    1. Сканирует TaskStore на незавершённые задачи
    2. Для каждой — загружает план из PlanCheckpointStore
    3. Логирует сводку по незавершённым задачам
    4. Помечает их как RECOVERING
    5. Возвращает список RecoveryInfo для обработки (caller решает как именно
       возобновить — через BrainLoop.submit или через интерфейс пользователя)

Почему не автоматическое продолжение:
    Не все задачи нужно автоматически продолжать. Некоторые требуют
    подтверждения пользователя ("я ещё хочу это?"). Некоторые устарели.
    RecoveryManager только ВЫЯВЛЯЕТ прерванные задачи, caller решает что делать.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .task_store import TaskSession, TaskStatus, TaskStore

if TYPE_CHECKING:
    from brain.planner import Plan, PlanCheckpointStore

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Dataclass для результата recovery
# ------------------------------------------------------------------

@dataclass
class RecoveryInfo:
    """
    Информация об одной прерванной задаче.

    Используется вызывающим кодом (LiveLoop / HealthCheck) чтобы
    решить — возобновить задачу, уведомить пользователя, или закрыть.
    """
    task:            TaskSession
    plan:            "Plan | None"          # последний checkpoint плана
    pending_steps:   list[dict]             # шаги ещё не выполненные
    completed_steps: list[dict]             # шаги уже выполненные
    interrupted_at:  datetime              # когда последний раз обновлялась
    plan_progress:   dict = field(default_factory=dict)  # {total, done, percent}

    @property
    def task_id(self) -> str:
        return self.task.task_id

    @property
    def session_id(self) -> str:
        return self.task.session_id

    @property
    def goal(self) -> str:
        return self.task.goal

    @property
    def has_plan(self) -> bool:
        return self.plan is not None

    @property
    def is_resumable(self) -> bool:
        """True если есть незавершённые шаги для продолжения."""
        return bool(self.pending_steps)

    def summary(self) -> str:
        parts = [
            f"task_id={self.task_id[:8]}",
            f"goal='{self.goal[:60]}'",
            f"session={self.session_id}",
            f"interrupted_at={self.interrupted_at.strftime('%Y-%m-%d %H:%M')}",
        ]
        if self.plan:
            p = self.plan_progress
            parts.append(f"progress={p.get('done', 0)}/{p.get('total', 0)} steps")
        else:
            parts.append("no_plan")
        return " | ".join(parts)


# ------------------------------------------------------------------
# RecoveryManager
# ------------------------------------------------------------------

class RecoveryManager:
    """
    Находит прерванные задачи при старте агента и подготавливает их к resumption.

    Использование:
        manager = RecoveryManager(task_store, plan_checkpoints)

        # При старте
        recovered = manager.recover()
        for info in recovered:
            logger.warning("Interrupted: %s", info.summary())
            # Caller решает: submit to BrainLoop or notify user

        # После восстановления
        manager.finalize(info.task_id)   # меняет статус RECOVERING → RUNNING
    """

    def __init__(
        self,
        task_store: TaskStore,
        plan_checkpoints: "PlanCheckpointStore",
    ) -> None:
        self._tasks = task_store
        self._plans = plan_checkpoints

    def recover(self) -> list[RecoveryInfo]:
        """
        Основной метод recovery.

        1. Находит все незавершённые TaskSession
        2. Для каждой загружает план
        3. Помечает как RECOVERING
        4. Возвращает список RecoveryInfo

        Вызывать один раз при старте, до LiveLoop.run_forever().
        """
        active_tasks = self._tasks.list_active()

        if not active_tasks:
            logger.info("[Recovery] No interrupted tasks found — clean start")
            return []

        logger.warning(
            "[Recovery] Found %d interrupted task(s) — agent was not shut down cleanly",
            len(active_tasks),
        )

        results: list[RecoveryInfo] = []
        for task in active_tasks:
            # Помечаем как recovering до построения RecoveryInfo
            self._tasks.mark_recovering(task.task_id)
            task.status = TaskStatus.RECOVERING   # обновляем in-memory объект

            info = self._build_info(task)
            results.append(info)

            logger.warning("[Recovery] %s", info.summary())

        return results

    def finalize(self, task_id: str) -> None:
        """
        После успешного возобновления задачи — переводим из RECOVERING → RUNNING.
        Вызывается когда BrainLoop принял задачу обратно.
        """
        self._tasks.update_status(task_id, TaskStatus.RUNNING)
        logger.info("[Recovery] task_id=%s finalized → RUNNING", task_id[:8])

    def abandon(self, task_id: str, reason: str = "not resumed after recovery") -> None:
        """
        Если задачу решено не возобновлять — закрываем как FAILED.
        """
        self._tasks.fail(task_id, f"abandoned: {reason}")
        logger.info("[Recovery] task_id=%s abandoned", task_id[:8])

    def status_report(self) -> dict[str, Any]:
        """
        Отчёт о состоянии recovery для /status endpoint.
        """
        active = self._tasks.list_active()
        stats = self._tasks.stats()
        return {
            "active_tasks": len(active),
            "task_stats": stats,
            "recovering": [t.to_dict() for t in active if t.status == TaskStatus.RECOVERING],
        }

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _build_info(self, task: TaskSession) -> RecoveryInfo:
        plan = None
        pending_steps: list[dict] = []
        completed_steps: list[dict] = []
        progress: dict = {}

        if task.plan_job_id:
            try:
                plan = self._plans.latest(task.plan_job_id)
                if plan:
                    progress = plan.progress()
                    for step in plan.steps:
                        d = step.to_dict()
                        if step.status.value in ("pending", "running"):
                            pending_steps.append(d)
                        elif step.status.value in ("done", "skipped"):
                            completed_steps.append(d)
            except Exception as exc:
                logger.warning("[Recovery] failed to load plan for task %s: %s", task.task_id[:8], exc)

        return RecoveryInfo(
            task=task,
            plan=plan,
            pending_steps=pending_steps,
            completed_steps=completed_steps,
            interrupted_at=task.updated_at,
            plan_progress=progress,
        )
