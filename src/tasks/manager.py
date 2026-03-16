"""
Task manager: create tasks from plan, enqueue, status. MVP: create + enqueue.
"""
from __future__ import annotations

import uuid
from typing import Any

from src.tasks.task_state import Task
from src.tasks.queue import enqueue


def create_task(payload: dict[str, Any]) -> Task | None:
    t = Task(id=str(uuid.uuid4())[:8], payload=payload)
    if not enqueue(t):
        return None
    return t
