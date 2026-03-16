"""
Task state: pending, running, done, failed.
"""
from __future__ import annotations

from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class Task:
    def __init__(self, id: str, payload: dict[str, Any]):
        self.id = id
        self.payload = payload
        self.status = TaskStatus.PENDING
        self.result: Any = None
