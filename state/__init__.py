# state — State & Session Management (Слой 23)
# Контекст сессии, checkpoint/resume, идемпотентность, персистентность.
# Task state machine с валидацией переходов (formal_contracts_spec §11).
from .state_manager import (
    StateManager, Session, Checkpoint, SessionStatus,
    TaskState, TaskStateMachine, InvalidTransitionError,
)

__all__ = [
    'StateManager', 'Session', 'Checkpoint', 'SessionStatus',
    'TaskState', 'TaskStateMachine', 'InvalidTransitionError',
]
