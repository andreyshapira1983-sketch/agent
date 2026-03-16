"""
Защита от task explosion и evolution drift.

Task explosion:
- MAX_TASK_QUEUE, MAX_TASKS_PER_CYCLE, TASK_TTL.

Evolution drift (бесконечные «улучшения» без реальной пользы):
- MAX_EVOLUTION_PATCHES_PER_CYCLE: максимум accept_patch за один цикл (по умолчанию 1).

Оркестратор в начале run_cycle вызывает advance_cycle() (patch_guard и task_guard).
"""
from __future__ import annotations

import os

# Env: 0 отключает соответствующий лимит.
MAX_TASK_QUEUE = int(os.environ.get("MAX_TASK_QUEUE", "20"))
MAX_TASKS_PER_CYCLE = int(os.environ.get("MAX_TASKS_PER_CYCLE", "2"))
TASK_TTL = int(os.environ.get("TASK_TTL", "50"))
MAX_EVOLUTION_PATCHES_PER_CYCLE = int(os.environ.get("MAX_EVOLUTION_PATCHES_PER_CYCLE", "1"))

_current_cycle: int = 0
_tasks_added_this_cycle: int = 0
_evolution_accepts_this_cycle: int = 0


def advance_cycle() -> None:
    """Вызвать в начале каждого цикла оркестратора. Сбрасывает счётчики задач и эволюции за цикл."""
    global _current_cycle, _tasks_added_this_cycle, _evolution_accepts_this_cycle
    _current_cycle += 1
    _tasks_added_this_cycle = 0
    _evolution_accepts_this_cycle = 0


def get_current_cycle() -> int:
    """Текущий цикл (для enqueue_cycle и purge_aged)."""
    return _current_cycle


def can_enqueue(current_queue_size: int) -> tuple[bool, str]:
    """
    Можно ли добавить ещё одну задачу?
    Проверяет лимит очереди и лимит задач за цикл.
    """
    if MAX_TASK_QUEUE <= 0 and MAX_TASKS_PER_CYCLE <= 0:
        return (True, "")
    if MAX_TASK_QUEUE > 0 and current_queue_size >= MAX_TASK_QUEUE:
        return (
            False,
            f"Task explosion guard: queue full ({current_queue_size} >= {MAX_TASK_QUEUE}). Complete or drop tasks before adding more.",
        )
    if MAX_TASKS_PER_CYCLE > 0 and _tasks_added_this_cycle >= MAX_TASKS_PER_CYCLE:
        return (
            False,
            f"Task explosion guard: max tasks per cycle ({MAX_TASKS_PER_CYCLE}). Wait for next cycle.",
        )
    return (True, "")


def record_enqueue() -> None:
    """Записать, что одна задача добавлена в очередь (вызывать после успешного enqueue)."""
    global _tasks_added_this_cycle
    _tasks_added_this_cycle += 1


def can_accept_evolution_patch() -> tuple[bool, str]:
    """
    Можно ли применить ещё один эволюционный патч в этом цикле (accept_patch_to_stable)?
    Предотвращает evolution drift: бесконечную череду «улучшений» (logging, refactor), которые проходят тесты, но не дают пользы.
    """
    if MAX_EVOLUTION_PATCHES_PER_CYCLE <= 0:
        return (True, "")
    if _evolution_accepts_this_cycle >= MAX_EVOLUTION_PATCHES_PER_CYCLE:
        return (
            False,
            f"Evolution budget: max patches per cycle ({MAX_EVOLUTION_PATCHES_PER_CYCLE}) reached. Wait for next cycle to accept more.",
        )
    return (True, "")


def record_evolution_accept() -> None:
    """Записать, что один эволюционный патч принят (вызывать после успешного accept_patch_to_stable)."""
    global _evolution_accepts_this_cycle
    _evolution_accepts_this_cycle += 1
