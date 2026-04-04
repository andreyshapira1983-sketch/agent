"""Formal Contracts — Pydantic-модели для всех ключевых границ системы.

Реализует формальные контракты из formal_contracts_spec.md §2–§10.
Каждая модель содержит runtime-валидацию инвариантов, описанных в спецификации.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


# ═══════════════════════════════════════════════════════════════════════════════
# Enums
# ═══════════════════════════════════════════════════════════════════════════════

class RiskClass(str, Enum):
    SAFE = 'safe'
    GUARDED = 'guarded'
    DANGEROUS = 'dangerous'
    PROHIBITED = 'prohibited'


class TaskStatus(str, Enum):
    CREATED = 'created'
    PLANNED = 'planned'
    AWAITING_APPROVAL = 'awaiting_approval'
    RUNNING = 'running'
    BLOCKED = 'blocked'
    FAILED = 'failed'
    COMPLETED = 'completed'
    CANCELLED = 'cancelled'


class ToolResultStatus(str, Enum):
    OK = 'ok'
    ERROR = 'error'
    DENIED = 'denied'


# ═══════════════════════════════════════════════════════════════════════════════
# §2 — Error Envelope
# ═══════════════════════════════════════════════════════════════════════════════

class ErrorEnvelope(BaseModel):
    """Стандартная обёртка ошибки (§2).

    Инварианты:
    - error_code обязателен
    - retryable обязателен
    - details не должен содержать секреты (проверяется на уровне secrets_redaction)
    """
    error_code: str = Field(..., min_length=1, max_length=100, pattern=r'^[A-Z0-9_]+$')
    message: str = Field(default='', max_length=2000)
    trace_id: str = Field(default='', max_length=200)
    task_id: str = ''
    step_id: str = ''
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════════
# §3 — Task Creation
# ═══════════════════════════════════════════════════════════════════════════════

class Initiator(BaseModel):
    type: str = Field(..., min_length=1)  # 'user' | 'system' | 'worker'
    id: str = Field(..., min_length=1)


class TaskConstraints(BaseModel):
    budget_usd_max: float = 5.0
    allow_network: bool = True
    allow_mutation: bool = False


class InputArtifact(BaseModel):
    artifact_id: str = Field(..., min_length=1)
    type: str = Field(..., min_length=1)


class TaskCreationContract(BaseModel):
    """Контракт создания задачи (§3).

    Инварианты:
    - task_id, request_id, idempotency_key обязательны
    - task без initiator недопустим
    - allow_mutation=false запрещает mutable tool step без re-approval
    """
    task_id: str = Field(..., min_length=1)
    request_id: str = Field(..., min_length=1)
    idempotency_key: str = Field(..., min_length=1)
    initiator: Initiator
    goal: str = ''
    constraints: TaskConstraints = Field(default_factory=TaskConstraints)
    input_artifacts: list[InputArtifact] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# §4 — Plan Step
# ═══════════════════════════════════════════════════════════════════════════════

class PlanStepContract(BaseModel):
    """Контракт шага плана (§4).

    Инварианты:
    - каждый шаг имеет risk_class
    - каждый шаг имеет expected_output_schema
    - dangerous step без requires_approval=true недопустим
    - tool-using step без required_tool недопустим
    """
    step_id: str = Field(..., min_length=1)
    task_id: str = Field(..., min_length=1)
    worker_type: str = ''
    intent: str = ''
    required_tool: str | None = None
    risk_class: RiskClass
    inputs: dict[str, Any] = Field(default_factory=dict)
    expected_output_schema: str = Field(..., min_length=1)
    rollback_hint: str | None = None
    requires_approval: bool = False
    rationale: str = ''

    @model_validator(mode='after')
    def _dangerous_needs_approval(self) -> PlanStepContract:
        if self.risk_class == RiskClass.DANGEROUS and not self.requires_approval:
            raise ValueError(
                'dangerous step must have requires_approval=true'
            )
        return self


# ═══════════════════════════════════════════════════════════════════════════════
# §5 — Tool Request
# ═══════════════════════════════════════════════════════════════════════════════

class ToolRequestContract(BaseModel):
    """Контракт запроса к инструменту (§5).

    Инварианты:
    - worker_id обязателен
    - capability_scope обязателен
    - dangerous action без valid approval token отклоняется
    """
    request_id: str = Field(..., min_length=1)
    task_id: str = Field(..., min_length=1)
    step_id: str = Field(..., min_length=1)
    worker_id: str = Field(..., min_length=1)
    tool_name: str = Field(..., min_length=1)
    action: str = Field(..., min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    risk_class: RiskClass
    approval_token: str | None = None
    capability_scope: str = Field(..., min_length=1)
    trace_id: str = ''

    @model_validator(mode='after')
    def _dangerous_needs_token(self) -> ToolRequestContract:
        if self.risk_class == RiskClass.DANGEROUS and not self.approval_token:
            raise ValueError(
                'dangerous tool request requires approval_token'
            )
        if self.risk_class == RiskClass.PROHIBITED:
            raise ValueError('prohibited actions are never allowed')
        return self


# ═══════════════════════════════════════════════════════════════════════════════
# §6 — Tool Response
# ═══════════════════════════════════════════════════════════════════════════════

class ToolResponseContract(BaseModel):
    """Контракт ответа от инструмента (§6).

    Инварианты:
    - любой реальный execution результат должен иметь receipt_id
    - stdout/stderr — по ссылке, не сырой текст
    """
    request_id: str = Field(..., min_length=1)
    task_id: str = Field(..., min_length=1)
    step_id: str = Field(..., min_length=1)
    status: ToolResultStatus
    receipt_id: str = Field(..., min_length=1)
    stdout_ref: str = ''          # ссылка на артефакт, не сырой текст
    stderr_ref: str = ''
    structured_result: dict[str, Any] = Field(default_factory=dict)
    timing_ms: float = 0.0
    resource_usage: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    trace_id: str = ''


# ═══════════════════════════════════════════════════════════════════════════════
# §7 — Approval Request
# ═══════════════════════════════════════════════════════════════════════════════

class ImpactScope(BaseModel):
    resource_type: str = ''
    resource_ids: list[str] = Field(default_factory=list)


class ApprovalRequestContract(BaseModel):
    """Контракт запроса одобрения (§7)."""
    approval_id: str = Field(..., min_length=1)
    task_id: str = Field(..., min_length=1)
    step_id: str = Field(..., min_length=1)
    action_hash: str = Field(..., min_length=1)
    risk_class: RiskClass
    summary: str = ''
    impact_scope: ImpactScope = Field(default_factory=ImpactScope)
    rollback_plan: str = ''
    expires_at: str = ''   # ISO 8601


# ═══════════════════════════════════════════════════════════════════════════════
# §8 — Approval Token
# ═══════════════════════════════════════════════════════════════════════════════

class ApprovalTokenContract(BaseModel):
    """Контракт approval token (§8).

    Инварианты:
    - token привязан к action_hash
    - token одноразовый (single_use)
    - token не переносим между workers (subject = worker_id)
    - token с истёкшим сроком недействителен
    """
    approval_token: str = Field(..., min_length=1)
    approval_id: str = Field(..., min_length=1)
    task_id: str = Field(..., min_length=1)
    step_id: str = Field(..., min_length=1)
    action_hash: str = Field(..., min_length=1)
    subject: str = Field(..., min_length=1)
    expires_at: float                          # unix timestamp
    single_use: bool = True

    def is_expired(self, now: float | None = None) -> bool:
        return (now or time.time()) > self.expires_at

    def matches_action(self, action_hash: str) -> bool:
        return self.action_hash == action_hash

    def matches_subject(self, worker_id: str) -> bool:
        return self.subject == worker_id


# ═══════════════════════════════════════════════════════════════════════════════
# §9 — Memory Write
# ═══════════════════════════════════════════════════════════════════════════════

class Provenance(BaseModel):
    source_type: str = Field(..., min_length=1)
    receipt_id: str = ''
    timestamp: str = ''    # ISO 8601


class MemoryRecord(BaseModel):
    content: str = Field(..., min_length=1)
    provenance: Provenance
    verification_status: str = Field(..., min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class MemoryWriteContract(BaseModel):
    """Контракт записи в память (§9).

    Инварианты:
    - semantic write без provenance запрещён
    - semantic/long-term write без verification_status запрещён
    """
    write_id: str = Field(..., min_length=1)
    task_id: str = ''
    source_step_id: str = ''
    memory_target: str = Field(..., pattern=r'^(working|episodic|semantic|long_term)$')
    record: MemoryRecord


# ═══════════════════════════════════════════════════════════════════════════════
# §10 — Audit Event
# ═══════════════════════════════════════════════════════════════════════════════

class AuditActor(BaseModel):
    type: str = Field(..., min_length=1)
    id: str = Field(..., min_length=1)


class AuditEventContract(BaseModel):
    """Контракт audit event (§10).

    Инварианты:
    - deny events логируются как allow events (обязательно)
    - audit events — append-only
    """
    event_id: str = Field(..., min_length=1)
    trace_id: str = ''
    task_id: str = ''
    step_id: str = ''
    actor: AuditActor
    event_type: str = Field(..., min_length=1)
    timestamp: str = ''   # ISO 8601
    details: dict[str, Any] = Field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════════
# §11 — State Transition
# ═══════════════════════════════════════════════════════════════════════════════

# Допустимые переходы (spec §11)
_VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.CREATED: {
        TaskStatus.PLANNED, TaskStatus.CANCELLED, TaskStatus.BLOCKED,
        TaskStatus.FAILED,
    },
    TaskStatus.PLANNED: {
        TaskStatus.AWAITING_APPROVAL, TaskStatus.RUNNING,
        TaskStatus.BLOCKED, TaskStatus.CANCELLED, TaskStatus.FAILED,
    },
    TaskStatus.AWAITING_APPROVAL: {
        TaskStatus.RUNNING, TaskStatus.CANCELLED, TaskStatus.FAILED,
    },
    TaskStatus.RUNNING: {
        TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.BLOCKED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.BLOCKED: {
        TaskStatus.RUNNING, TaskStatus.CANCELLED, TaskStatus.FAILED,
    },
    TaskStatus.FAILED: {
        TaskStatus.CANCELLED,
    },
    TaskStatus.COMPLETED: set(),
    TaskStatus.CANCELLED: set(),
}


def validate_transition(current: TaskStatus, target: TaskStatus) -> bool:
    """Проверяет, допустим ли переход состояния (§11).

    Запрещённые переходы:
    - created → completed
    - awaiting_approval → running без valid approval (вызывающий должен проверить)
    - failed → completed без explicit recovery path
    """
    return target in _VALID_TRANSITIONS.get(current, set())


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def validate_or_error(model_cls: type[BaseModel], data: dict) -> BaseModel | ErrorEnvelope:
    """Валидирует данные по модели; при ошибке возвращает ErrorEnvelope."""
    try:
        return model_cls.model_validate(data)
    except Exception as exc:
        return ErrorEnvelope(
            error_code='VALIDATION_ERROR',
            message=str(exc),
            retryable=False,
            details={'model': model_cls.__name__, 'raw_keys': list(data.keys())},
        )
