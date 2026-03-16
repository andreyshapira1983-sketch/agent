"""Тесты защиты от task explosion: лимит очереди, лимит за цикл, TTL."""
import pytest

from src.governance.task_guard import (
    can_enqueue,
    record_enqueue,
    advance_cycle,
    get_current_cycle,
    can_accept_evolution_patch,
    record_evolution_accept,
    MAX_TASK_QUEUE,
    MAX_TASKS_PER_CYCLE,
    MAX_EVOLUTION_PATCHES_PER_CYCLE,
)
from src.tasks.queue import enqueue, dequeue, size, purge_aged
from src.tasks.task_state import Task


def test_can_enqueue_empty():
    advance_cycle()
    allowed, _ = can_enqueue(0)
    assert allowed is True


def test_max_tasks_per_cycle():
    if MAX_TASKS_PER_CYCLE <= 0:
        pytest.skip("MAX_TASKS_PER_CYCLE disabled")
    advance_cycle()
    allowed, _ = can_enqueue(0)
    assert allowed is True
    record_enqueue()
    record_enqueue()
    allowed, reason = can_enqueue(0)
    assert allowed is False
    assert "per cycle" in reason or "cycle" in reason


def test_max_task_queue():
    if MAX_TASK_QUEUE <= 0:
        pytest.skip("MAX_TASK_QUEUE disabled")
    advance_cycle()
    allowed, reason = can_enqueue(MAX_TASK_QUEUE)
    assert allowed is False
    assert "full" in reason or str(MAX_TASK_QUEUE) in reason


def test_enqueue_records_cycle_and_respects_guard():
    while dequeue():
        pass
    advance_cycle()
    t = Task("t1", {"tool": "get_current_time", "arguments": {}})
    ok = enqueue(t)
    assert ok is True
    assert t.payload.get("enqueue_cycle") == get_current_cycle()
    while dequeue():
        pass


def test_purge_aged_runs():
    while dequeue():
        pass
    dropped = purge_aged(50)
    assert isinstance(dropped, int) and dropped >= 0


def test_evolution_budget_per_cycle():
    if MAX_EVOLUTION_PATCHES_PER_CYCLE <= 0:
        pytest.skip("MAX_EVOLUTION_PATCHES_PER_CYCLE disabled")
    advance_cycle()
    allowed, _ = can_accept_evolution_patch()
    assert allowed is True
    record_evolution_accept()
    if MAX_EVOLUTION_PATCHES_PER_CYCLE == 1:
        allowed, reason = can_accept_evolution_patch()
        assert allowed is False
        assert "budget" in reason or "cycle" in reason
