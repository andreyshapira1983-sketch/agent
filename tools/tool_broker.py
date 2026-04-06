# Tool Broker — единственный путь к инструментам (formal_contracts_spec §5-§6)
# Архитектура автономного AI-агента
#
# Proxy перед ToolLayer: валидация capability_scope, approval tokens,
# request/response contracts, audit logging, receipt generation.

from __future__ import annotations

import hashlib
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from validation.contracts import (
    ToolRequestContract as _PydanticToolRequest,
    ToolResponseContract as _PydanticToolResponse,
    AuditEventContract as _PydanticAuditEvent,
    AuditActor as _PydanticAuditActor,
    ErrorEnvelope,
    RiskClass as _RiskClass,
    ToolResultStatus as _ToolResultStatus,
)


# ── Tool Request / Response контракты ─────────────────────────────────────────

@dataclass
class ToolRequest:
    """Формальный запрос к инструменту (formal_contracts_spec §5)."""
    request_id: str
    task_id: str
    step_id: str
    worker_id: str
    tool_name: str
    action: str
    parameters: dict
    risk_class: str             # 'safe' | 'guarded' | 'dangerous' | 'prohibited'
    approval_token: Any | None  # ApprovalToken или None
    capability_scope: str       # напр. 'repo:test', 'fs:read', 'net:off'
    trace_id: str = ''          # §10 — trace correlation id

    def to_dict(self) -> dict:
        return {
            'request_id': self.request_id,
            'task_id': self.task_id,
            'step_id': self.step_id,
            'worker_id': self.worker_id,
            'tool_name': self.tool_name,
            'action': self.action,
            'parameters': self.parameters,
            'risk_class': self.risk_class,
            'approval_token': (
                self.approval_token.to_dict()  # type: ignore[union-attr]
                if hasattr(self.approval_token, 'to_dict') else None
            ),
            'capability_scope': self.capability_scope,
            'trace_id': self.trace_id,
        }


@dataclass
class ToolResponse:
    """Формальный ответ от инструмента (formal_contracts_spec §6)."""
    request_id: str
    task_id: str
    step_id: str
    status: str                 # 'ok' | 'error' | 'denied'
    receipt_id: str
    structured_result: Any
    timing_ms: float
    error: str | None = None
    trace_id: str = ''          # §10 — trace correlation id

    def to_dict(self) -> dict:
        return {
            'request_id': self.request_id,
            'task_id': self.task_id,
            'step_id': self.step_id,
            'status': self.status,
            'receipt_id': self.receipt_id,
            'structured_result': self.structured_result,
            'timing_ms': self.timing_ms,
            'error': self.error,
            'trace_id': self.trace_id,
        }


# ── Ошибки брокера ────────────────────────────────────────────────────────────

class BrokerError(Exception):
    """Базовая ошибка Tool Broker."""


class CapabilityDeniedError(BrokerError):
    """Worker не имеет capability для данного tool+action."""


class ApprovalRequiredError(BrokerError):
    """Dangerous action требует approval token."""


class ProhibitedActionError(BrokerError):
    """Действие запрещено policy (prohibited risk class)."""


# ── Capability Matrix ─────────────────────────────────────────────────────────

# Матрица по умолчанию: worker_id → set допустимых tool_name.
# Deny-by-default: если worker не в матрице или tool не в его set → отказ.
_DEFAULT_CAPABILITY_MATRIX: dict[str, set[str]] = {
    # Базовые роли агентов
    'researcher': {'search', 'browser', 'vector_store'},
    'coder': {'terminal', 'filesystem', 'python_runtime', 'github', 'search', 'package_manager'},
    'analyst': {'search', 'database', 'python_runtime', 'vector_store'},
    'executor': {'terminal', 'filesystem', 'python_runtime', 'docker', 'package_manager'},
    'reviewer': {'search', 'filesystem', 'github'},
    'planner': {'search'},
    # System-level callers
    'autonomous_loop': {'search', 'filesystem', 'python_runtime', 'terminal',
                        'github', 'browser', 'database', 'process_manager'},
    'cognitive_core': {'search', 'python_runtime', 'filesystem'},
    'web_interface': {'process_manager', 'terminal', 'search', 'filesystem'},
    # Wildcard — можно добавить конкретных worker'ов
}

# Tool risk classification
_TOOL_RISK: dict[str, str] = {
    'search': 'safe',
    'browser': 'safe',
    'vector_store': 'safe',
    'filesystem': 'guarded',
    'python_runtime': 'guarded',
    'database': 'guarded',
    'terminal': 'dangerous',
    'github': 'guarded',
    'docker': 'dangerous',
    'package_manager': 'guarded',
    'process_manager': 'dangerous',
    'cloud_api': 'dangerous',
}


class ToolBroker:
    """Tool Broker — единственный авторизованный путь к инструментам.

    Proxy перед ToolLayer:
        - deny-by-default capability matrix (worker × tool)
        - approval token validation для dangerous actions
        - request/response contracts с receipt_id
        - audit logging каждого вызова (allow и deny)
        - timing measurement

    Thread-safe: все мутации под lock.
    """

    def __init__(
        self,
        tool_layer,
        approval_service=None,
        capability_matrix: dict[str, set[str]] | None = None,
        tool_risk: dict[str, str] | None = None,
        monitoring=None,
        audit_journal=None,
    ):
        """
        Args:
            tool_layer         — ToolLayer (Слой 5) с зарегистрированными инструментами
            approval_service   — ApprovalService для валидации токенов
            capability_matrix  — worker_id → set допустимых tool_name (None = default)
            tool_risk          — tool_name → risk_class (None = default)
            monitoring         — Monitoring (Слой 17)
            audit_journal      — AuditJournal (Слой 24) для записи событий
        """
        self._tool_layer = tool_layer
        self._approval_service = approval_service
        # Deep copy: каждый broker получает свои set'ы (revoke не мутирует default)
        _src = capability_matrix or _DEFAULT_CAPABILITY_MATRIX
        self._capability_matrix = {k: set(v) for k, v in _src.items()}
        self._tool_risk = dict(tool_risk or _TOOL_RISK)
        self._monitoring = monitoring
        self._audit_journal = audit_journal
        self._lock = threading.Lock()

        # Статистика
        self._stats = {'total': 0, 'allowed': 0, 'denied': 0}

    # ── Основной метод: формальный request ────────────────────────────────────

    def request(
        self,
        task_id: str,
        step_id: str,
        worker_id: str,
        tool_name: str,
        action: str = '',
        parameters: dict | None = None,
        approval_token=None,
        capability_scope: str = '',
        trace_id: str = '',
    ) -> ToolResponse:
        """Выполняет инструмент через полный контракт request/response.

        Lifecycle:
            1. Генерирует request_id
            2. Проверяет capability_matrix (deny-by-default)
            3. Проверяет risk_class: prohibited → блокировка
            4. Проверяет approval_token для dangerous actions
            5. Вызывает tool через ToolLayer
            6. Генерирует receipt_id, формирует ToolResponse
            7. Логирует в audit journal

        Args:
            task_id         — ID задачи
            step_id         — ID шага
            worker_id       — ID вызывающего worker'а
            tool_name       — имя инструмента
            action          — конкретное действие (для audit; '' = default)
            parameters      — параметры вызова
            approval_token  — ApprovalToken для dangerous actions
            capability_scope— строковый scope (для audit)
            trace_id        — trace correlation id (§10)

        Returns:
            ToolResponse с receipt_id (при success или error).

        Raises:
            CapabilityDeniedError  — worker не имеет доступа к tool
            ProhibitedActionError  — tool/action запрещён policy
            ApprovalRequiredError  — dangerous action без approval token
        """
        params = parameters or {}
        request_id = f"toolreq_{uuid.uuid4().hex[:12]}"
        risk_class = self._tool_risk.get(tool_name, 'guarded')

        req = ToolRequest(
            request_id=request_id,
            task_id=task_id,
            step_id=step_id,
            worker_id=worker_id,
            tool_name=tool_name,
            action=action,
            parameters=params,
            risk_class=risk_class,
            approval_token=approval_token,
            capability_scope=capability_scope,
            trace_id=trace_id,
        )

        with self._lock:
            self._stats['total'] += 1

        # 1. Capability check (deny-by-default)
        allowed_tools = self._capability_matrix.get(worker_id)
        if allowed_tools is None or tool_name not in allowed_tools:
            self._deny(req, 'capability_denied',
                       f"Worker '{worker_id}' не имеет доступа к '{tool_name}'")
            raise CapabilityDeniedError(
                f"Worker '{worker_id}' is not allowed to use tool '{tool_name}'"
            )

        # 2. Prohibited check
        if risk_class == 'prohibited':
            self._deny(req, 'prohibited_action',
                       f"Tool '{tool_name}' имеет risk_class=prohibited")
            raise ProhibitedActionError(
                f"Tool '{tool_name}' is prohibited by policy"
            )

        # 3. Approval token for dangerous actions
        if risk_class == 'dangerous' and approval_token is None:
            self._deny(req, 'approval_missing',
                       f"Dangerous tool '{tool_name}' требует approval token")
            raise ApprovalRequiredError(
                f"Dangerous tool '{tool_name}' requires approval token"
            )

        # Pydantic contract validation (§5 formal_contracts_spec)
        # Выполняем после проверки approval_missing, чтобы не засорять лог
        # ожидаемым CONTRACT WARNING в auto-approve сценарии.
        self._validate_request_contract(req)

        if risk_class == 'dangerous' and approval_token is not None:
            if self._approval_service is not None:
                from safety.approval_tokens import compute_action_hash
                expected_hash = compute_action_hash(action or tool_name, params)
                # validate_token raises on failure — let it propagate
                self._approval_service.validate_token(
                    approval_token,
                    expected_task_id=task_id,
                    expected_step_id=step_id,
                    expected_action_hash=expected_hash,
                    expected_subject=worker_id,
                )

        # 4. Execute through ToolLayer
        start_time = time.time()
        try:
            result = self._tool_layer.use(tool_name, **params)
            elapsed_ms = (time.time() - start_time) * 1000

            receipt_id = f"rcpt_{uuid.uuid4().hex[:12]}"
            response = ToolResponse(
                request_id=request_id,
                task_id=task_id,
                step_id=step_id,
                status='ok',
                receipt_id=receipt_id,
                structured_result=result,
                timing_ms=round(elapsed_ms, 2),
                trace_id=trace_id,
            )

            with self._lock:
                self._stats['allowed'] += 1

            self._audit_allow(req, response)
            return response

        except Exception as e:
            elapsed_ms = (time.time() - start_time) * 1000
            receipt_id = f"rcpt_{uuid.uuid4().hex[:12]}"
            response = ToolResponse(
                request_id=request_id,
                task_id=task_id,
                step_id=step_id,
                status='error',
                receipt_id=receipt_id,
                structured_result=None,
                timing_ms=round(elapsed_ms, 2),
                error=f"{type(e).__name__}: {e}",
                trace_id=trace_id,
            )

            with self._lock:
                self._stats['allowed'] += 1  # tool was invoked (not denied)

            self._audit_allow(req, response)
            return response

    # ── Simplified use() — proxy для существующих вызовов ─────────────────────

    def use(
        self,
        tool_name: str,
        *,
        worker_id: str = 'autonomous_loop',
        task_id: str = '',
        step_id: str = '',
        approval_token=None,
        trace_id: str = '',
        **kwargs,
    ):
        """Упрощённый proxy с интерфейсом совместимым с ToolLayer.use().

        Для постепенной миграции: существующий код вызывает broker.use()
        вместо tool_layer.use(), получая enforcement без изменения контракта.

        Returns:
            Результат вызова tool (как ToolLayer.use) при success.

        Raises:
            BrokerError при deny.
            KeyError если tool не найден.
        """
        task = task_id or f"task_{uuid.uuid4().hex[:8]}"
        step = step_id or f"step_{uuid.uuid4().hex[:8]}"

        try:
            response = self.request(
                task_id=task,
                step_id=step,
                worker_id=worker_id,
                tool_name=tool_name,
                parameters=kwargs,
                approval_token=approval_token,
                trace_id=trace_id,
            )
        except ApprovalRequiredError:
            # Совместимость для legacy-вызовов broker.use(...):
            # если dangerous tool вызван без токена, пробуем запросить approval token
            # через ApprovalService (auto_approve/callback/interactive), затем повторяем.
            token = self._issue_approval_token_for_use(
                task_id=task,
                step_id=step,
                worker_id=worker_id,
                tool_name=tool_name,
                parameters=kwargs,
            )
            if token is None:
                raise
            response = self.request(
                task_id=task,
                step_id=step,
                worker_id=worker_id,
                tool_name=tool_name,
                parameters=kwargs,
                approval_token=token,
                trace_id=trace_id,
            )

        if response.status == 'error' and response.error:
            # Восстанавливаем оригинальное исключение для совместимости
            raise RuntimeError(response.error)

        return response.structured_result

    def _issue_approval_token_for_use(
        self,
        *,
        task_id: str,
        step_id: str,
        worker_id: str,
        tool_name: str,
        parameters: dict,
    ):
        """Запрашивает approval token для legacy broker.use() dangerous-вызовов."""
        svc = self._approval_service
        if svc is None:
            return None
        try:
            req = svc.create_request(
                task_id=task_id,
                step_id=step_id,
                action_type=tool_name,
                parameters=parameters or {},
                risk_class='dangerous',
                summary=f'auto-approve: {tool_name} for {worker_id}',
                impact_scope={'tool': tool_name},
            )
            return svc.request_and_issue(req, subject=worker_id)
        except Exception as exc:
            self._log(f"[broker] approval auto-issue failed: {type(exc).__name__}: {exc}")
            return None

    # ── Capability management ─────────────────────────────────────────────────

    def grant_capability(self, worker_id: str, tool_name: str):
        """Даёт worker'у доступ к tool."""
        with self._lock:
            if worker_id not in self._capability_matrix:
                self._capability_matrix[worker_id] = set()
            self._capability_matrix[worker_id].add(tool_name)

    def revoke_capability(self, worker_id: str, tool_name: str):
        """Отзывает у worker'а доступ к tool."""
        with self._lock:
            if worker_id in self._capability_matrix:
                self._capability_matrix[worker_id].discard(tool_name)

    def get_capabilities(self, worker_id: str) -> set[str]:
        """Возвращает set допустимых tool для worker'а."""
        return set(self._capability_matrix.get(worker_id, set()))

    def set_tool_risk(self, tool_name: str, risk_class: str):
        """Устанавливает risk_class для tool."""
        self._tool_risk[tool_name] = risk_class

    def get_tool_risk(self, tool_name: str) -> str:
        """Возвращает risk_class для tool."""
        return self._tool_risk.get(tool_name, 'guarded')

    # ── Passthrough к ToolLayer ───────────────────────────────────────────────

    def list(self) -> list[str]:
        """Список имён зарегистрированных инструментов."""
        return self._tool_layer.list()

    def describe(self) -> list[dict]:
        """Описание всех инструментов."""
        return self._tool_layer.describe()

    def get(self, name: str):
        """Возвращает инструмент (для introspection, не для вызова)."""
        return self._tool_layer.get(name)

    def register(self, tool):
        """Регистрирует инструмент через underlying ToolLayer."""
        self._tool_layer.register(tool)

    # ── Статистика ────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Возвращает статистику вызовов."""
        return dict(self._stats)

    # ── Внутренние методы ─────────────────────────────────────────────────────

    def _deny(self, req: ToolRequest, reason: str, detail: str):
        with self._lock:
            self._stats['denied'] += 1
        self._log(f"[broker] DENIED: {reason} — {detail}")
        self._audit_event('TOOL_CALL_DENIED', req, reason=reason, detail=detail)

    def _audit_allow(self, req: ToolRequest, resp: ToolResponse):
        self._log(
            f"[broker] {resp.status.upper()}: {req.tool_name}"
            f" (worker={req.worker_id}, {resp.timing_ms:.0f}ms, receipt={resp.receipt_id})"
        )
        event_type = 'TOOL_CALL_OK' if resp.status == 'ok' else 'TOOL_CALL_ERROR'
        self._audit_event(event_type, req, receipt_id=resp.receipt_id,
                          timing_ms=resp.timing_ms)

    def _audit_event(self, event_type: str, req: ToolRequest, **extra):
        if self._audit_journal is None:
            return
        event = {
            'event_id': f"evt_{uuid.uuid4().hex[:12]}",
            'trace_id': req.trace_id,
            'event_type': event_type,
            'task_id': req.task_id,
            'step_id': req.step_id,
            'actor': {'type': 'worker', 'id': req.worker_id},
            'tool_name': req.tool_name,
            'action': req.action,
            'risk_class': req.risk_class,
            **extra,
        }
        if hasattr(self._audit_journal, 'log'):
            self._audit_journal.log(event)
        elif hasattr(self._audit_journal, 'append'):
            self._audit_journal.append(event)

    def _log(self, message: str):
        if self._monitoring:
            self._monitoring.log(message, source='ToolBroker')

    # ── Pydantic contract validation ──────────────────────────────────────────

    def _validate_request_contract(self, req: ToolRequest) -> None:
        """Валидирует ToolRequest через Pydantic-контракт (§5).

        Не дублирует policy checks (capability/approval) — только структуру.
        При невалидном контракте логируем и пропускаем (не ломаем runtime).
        """
        try:
            _PydanticToolRequest.model_validate({
                'request_id': req.request_id,
                'task_id': req.task_id,
                'step_id': req.step_id,
                'worker_id': req.worker_id,
                'tool_name': req.tool_name,
                'action': req.action or 'default',
                'parameters': req.parameters,
                'risk_class': req.risk_class,
                'approval_token': (
                    getattr(req.approval_token, 'token', None)
                    or (req.approval_token if isinstance(req.approval_token, str) else None)
                ),
                'capability_scope': req.capability_scope or 'unscoped',
                'trace_id': req.trace_id,
            })
        except Exception as exc:
            self._log(f"[broker] CONTRACT WARNING: {exc}")
