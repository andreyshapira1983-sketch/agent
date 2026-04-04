# TraceContext — structured tracing per formal_contracts_spec §10, §2
# Генерация trace_id, event_id; dataclass для audit events.

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def generate_trace_id() -> str:
    """Генерирует уникальный trace_id: ``trc_<hex12>``."""
    return f"trc_{uuid.uuid4().hex[:12]}"


def generate_event_id() -> str:
    """Генерирует уникальный event_id: ``evt_<hex12>``."""
    return f"evt_{uuid.uuid4().hex[:12]}"


# ── Actor ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class Actor:
    """Актор, инициировавший событие (§10)."""
    type: str     # 'worker' | 'system' | 'human'
    id: str       # e.g. 'executor-worker', 'autonomous_loop'

    def to_dict(self) -> dict:
        return {'type': self.type, 'id': self.id}

    @classmethod
    def from_dict(cls, d: dict) -> Actor:
        return cls(type=str(d.get('type', 'system')),
                   id=str(d.get('id', 'unknown')))


# ── Structured Audit Event ────────────────────────────────────────────────────

@dataclass(slots=True)
class StructuredAuditEvent:
    """Формальный audit event (formal_contracts_spec §10).

    Обязательные поля: event_id, trace_id, task_id, step_id, actor,
    event_type, timestamp.  details — произвольный dict.
    """
    event_id: str
    trace_id: str
    task_id: str
    step_id: str
    actor: Actor
    event_type: str
    timestamp: str
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            'event_id': self.event_id,
            'trace_id': self.trace_id,
            'task_id': self.task_id,
            'step_id': self.step_id,
            'actor': self.actor.to_dict(),
            'event_type': self.event_type,
            'timestamp': self.timestamp,
            'details': self.details,
        }

    @classmethod
    def from_dict(cls, d: dict) -> StructuredAuditEvent:
        return cls(
            event_id=str(d.get('event_id', '')),
            trace_id=str(d.get('trace_id', '')),
            task_id=str(d.get('task_id', '')),
            step_id=str(d.get('step_id', '')),
            actor=Actor.from_dict(d.get('actor', {})),
            event_type=str(d.get('event_type', '')),
            timestamp=str(d.get('timestamp', '')),
            details=dict(d.get('details', {})),
        )


def build_event(
    *,
    trace_id: str,
    task_id: str,
    step_id: str,
    actor: Actor,
    event_type: str,
    details: dict | None = None,
) -> StructuredAuditEvent:
    """Фабрика: создаёт event с автогенерацией event_id и timestamp."""
    return StructuredAuditEvent(
        event_id=generate_event_id(),
        trace_id=trace_id,
        task_id=task_id,
        step_id=step_id,
        actor=actor,
        event_type=event_type,
        timestamp=datetime.now(timezone.utc).isoformat(),
        details=details or {},
    )


# ── Trace Context ─────────────────────────────────────────────────────────────

class TraceContext:
    """Контекст трассировки — переносит trace_id через фазы цикла.

    Создаётся на старте цикла / задачи.  Передаётся в ToolBroker,
    AuditJournal, Monitoring.  Позволяет корреляцию всех событий
    одного прохода.

    Устойчив к retry: если задача перезапускается с тем же trace_id,
    все события коррелируются.
    """

    __slots__ = ('trace_id', 'task_id', '_created_at')

    def __init__(self, trace_id: str | None = None, task_id: str = ''):
        self.trace_id = trace_id or generate_trace_id()
        self.task_id = task_id
        self._created_at = time.monotonic()

    def child(self, task_id: str = '') -> TraceContext:
        """Наследует trace_id, опционально с новым task_id."""
        return TraceContext(
            trace_id=self.trace_id,
            task_id=task_id or self.task_id,
        )

    def event(
        self,
        *,
        step_id: str,
        actor: Actor,
        event_type: str,
        details: dict | None = None,
    ) -> StructuredAuditEvent:
        """Создаёт StructuredAuditEvent, привязанный к этому контексту."""
        return build_event(
            trace_id=self.trace_id,
            task_id=self.task_id,
            step_id=step_id,
            actor=actor,
            event_type=event_type,
            details=details,
        )

    def elapsed_ms(self) -> float:
        return (time.monotonic() - self._created_at) * 1000

    def __repr__(self) -> str:
        return f"TraceContext(trace_id={self.trace_id!r}, task_id={self.task_id!r})"
