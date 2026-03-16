"""
Task queue: enqueue, dequeue, priorities. MVP: FIFO.
Task explosion guard: MAX_TASK_QUEUE, MAX_TASKS_PER_CYCLE; TTL purge (purge_aged).
"""
from __future__ import annotations

from collections import deque

from src.tasks.task_state import Task

_queue: deque[Task] = deque()


def enqueue(task: Task) -> bool:
    """
    Добавить задачу в очередь. Возвращает True если добавлена, False если отклонена (task guard).
    При добавлении в payload записывается enqueue_cycle для TTL.
    """
    try:
        from src.governance.task_guard import can_enqueue, get_current_cycle, record_enqueue
        allowed, _ = can_enqueue(len(_queue))
        if not allowed:
            return False
        task.payload = task.payload or {}
        task.payload["enqueue_cycle"] = get_current_cycle()
        _queue.append(task)
        record_enqueue()
        return True
    except Exception:
        task.payload = task.payload or {}
        _queue.append(task)
        return True


def dequeue() -> Task | None:
    if not _queue:
        return None
    return _queue.popleft()


def size() -> int:
    return len(_queue)


def purge_aged(max_age_cycles: int | None = None) -> int:
    """
    Удалить из очереди задачи, у которых (current_cycle - enqueue_cycle) > max_age_cycles.
    Возвращает количество удалённых. Если max_age_cycles не задан, используется TASK_TTL из task_guard.
    """
    try:
        from src.governance.task_guard import get_current_cycle, TASK_TTL
        current = get_current_cycle()
        max_age = max_age_cycles if max_age_cycles is not None else TASK_TTL
        if max_age <= 0:
            return 0
        kept: list[Task] = []
        removed = 0
        for t in _queue:
            enq = t.payload.get("enqueue_cycle", 0)
            if current - enq > max_age:
                removed += 1
            else:
                kept.append(t)
        _queue.clear()
        for t in kept:
            _queue.append(t)
        return removed
    except Exception:
        return 0


def peek(max_n: int = 20) -> list[dict]:
    """Снимок очереди без извлечения: список {id, tool, arguments_preview} для отображения в /tasks."""
    out: list[dict] = []
    for t in list(_queue)[:max_n]:
        payload = t.payload or {}
        tool = payload.get("tool", "")
        args = payload.get("arguments") or {}
        preview = str(args)[:60] + ("..." if len(str(args)) > 60 else "")
        out.append({"id": t.id, "tool": tool, "arguments_preview": preview})
    return out


def dequeue_emotion_aware(completed_ids: set[str] | None = None) -> Task | None:
    """
    Извлечь задачу с наивысшим приоритетом (эмоции + явный priority), учитывая зависимости.
    Возвращает только задачу, у которой все depends_on уже в completed_ids (или зависимостей нет).
    """
    if not _queue:
        return None
    completed = completed_ids or set()
    try:
        from src.personality.emotion_task_priority import get_task_priority_score
        items = list(_queue)
        ready: list[tuple[int, Task, float]] = []
        for i, t in enumerate(items):
            payload = t.payload or {}
            deps = payload.get("depends_on") or []
            if deps and not all(d in completed for d in deps):
                continue
            emotional = get_task_priority_score(payload)
            explicit = float(payload.get("priority", 5))
            score = emotional + 0.1 * explicit
            ready.append((i, t, score))
        if not ready:
            return None
        best_idx, best_task, _ = max(ready, key=lambda x: (x[2], -x[0]))
        _queue.clear()
        for i, t in enumerate(items):
            if i != best_idx:
                _queue.append(t)
        return best_task
    except Exception:
        return dequeue()
