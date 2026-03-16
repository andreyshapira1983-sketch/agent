"""
Interrupt handler: stop, change priority. MVP: placeholder.
"""
from __future__ import annotations

_interrupt_requested = False


def request_interrupt() -> None:
    global _interrupt_requested
    _interrupt_requested = True


def clear_interrupt() -> None:
    global _interrupt_requested
    _interrupt_requested = False


def is_interrupted() -> bool:
    return _interrupt_requested
