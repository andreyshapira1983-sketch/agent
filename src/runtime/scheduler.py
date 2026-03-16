"""
Scheduler: delayed and periodic tasks. MVP: placeholder.
"""
from __future__ import annotations

from typing import Callable, Any


def run_after(seconds: float, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
    # MVP: no actual delay
    fn(*args, **kwargs)
