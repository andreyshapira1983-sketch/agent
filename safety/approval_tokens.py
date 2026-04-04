# Approval Token System — формальные контракты §7-§8
# Архитектура автономного AI-агента
#
# Signed, single-use, bound approval tokens для dangerous actions.
# Интегрируется с HumanApprovalLayer (Слой 22) для получения человеческого решения.
# Используется Tool Broker / Executor для валидации перед исполнением.

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable


# ── Approval Request (formal_contracts_spec §7) ──────────────────────────────

@dataclass
class ApprovalRequest:
    """Формальный запрос на одобрение dangerous action."""
    approval_id: str
    task_id: str
    step_id: str
    action_hash: str          # sha256 хеш действия
    risk_class: str           # 'dangerous' | 'guarded' | ...
    summary: str              # человекочитаемое описание
    impact_scope: dict        # {"resource_type": ..., "resource_ids": [...]}
    rollback_plan: str
    expires_at: float         # unix timestamp

    def to_dict(self) -> dict:
        return {
            'approval_id': self.approval_id,
            'task_id': self.task_id,
            'step_id': self.step_id,
            'action_hash': self.action_hash,
            'risk_class': self.risk_class,
            'summary': self.summary,
            'impact_scope': self.impact_scope,
            'rollback_plan': self.rollback_plan,
            'expires_at': self.expires_at,
        }


# ── Approval Token (formal_contracts_spec §8) ────────────────────────────────

@dataclass
class ApprovalToken:
    """Signed single-use approval token, привязанный к конкретному действию."""
    token: str                # HMAC-подписанный токен
    approval_id: str
    task_id: str
    step_id: str
    action_hash: str
    subject: str              # worker_id — не переносим между workers
    expires_at: float         # unix timestamp
    single_use: bool = True

    def to_dict(self) -> dict:
        return {
            'approval_token': self.token,
            'approval_id': self.approval_id,
            'task_id': self.task_id,
            'step_id': self.step_id,
            'action_hash': self.action_hash,
            'subject': self.subject,
            'expires_at': self.expires_at,
            'single_use': self.single_use,
        }


# ── Ошибки валидации токенов ──────────────────────────────────────────────────

class ApprovalTokenError(Exception):
    """Базовая ошибка валидации approval token."""


class TokenExpiredError(ApprovalTokenError):
    """Токен с истёкшим сроком."""


class TokenReusedError(ApprovalTokenError):
    """Повторное использование single-use токена."""


class TokenSignatureError(ApprovalTokenError):
    """Невалидная подпись токена."""


class TokenMismatchError(ApprovalTokenError):
    """Несовпадение action_hash, task_id, step_id или subject."""


# ── Approval Service ──────────────────────────────────────────────────────────

def compute_action_hash(action_type: str, parameters: dict) -> str:
    """Вычисляет SHA-256 хеш действия.

    Каноническая сериализация: sorted keys, ensure_ascii, separators без пробелов.
    """
    canonical = json.dumps(
        {'action_type': action_type, 'parameters': parameters},
        sort_keys=True, ensure_ascii=True, separators=(',', ':'),
    )
    return 'sha256:' + hashlib.sha256(canonical.encode()).hexdigest()


class ApprovalService:
    """Сервис выдачи и валидации approval tokens.

    Lifecycle:
        1. Caller формирует ApprovalRequest через create_request()
        2. ApprovalService запрашивает human decision (через HumanApprovalLayer)
        3. При approval → генерирует signed ApprovalToken
        4. Caller передаёт токен в Tool Broker / Executor
        5. Broker вызывает validate_token() перед исполнением

    Thread-safe: все мутации под lock.
    """

    def __init__(
        self,
        human_approval=None,
        signing_key: str | None = None,
        default_ttl: float = 300.0,
        monitoring=None,
    ):
        """
        Args:
            human_approval — HumanApprovalLayer (Слой 22) для человеческого решения
            signing_key    — секрет для HMAC-подписи токенов (None = генерирует рандомный)
            default_ttl    — TTL токена в секундах (по умолчанию 300 = 5 минут)
            monitoring     — Monitoring (Слой 17)
        """
        self._human_approval = human_approval
        self._signing_key = (signing_key or secrets.token_hex(32)).encode()
        self._default_ttl = default_ttl
        self._monitoring = monitoring

        self._lock = threading.Lock()
        self._consumed_tokens: set[str] = set()       # использованные token id
        self._pending_requests: dict[str, ApprovalRequest] = {}   # approval_id → request
        self._issued_tokens: dict[str, ApprovalToken] = {}        # token → ApprovalToken

    # ── Создание запроса ──────────────────────────────────────────────────────

    def create_request(
        self,
        task_id: str,
        step_id: str,
        action_type: str,
        parameters: dict,
        risk_class: str = 'dangerous',
        summary: str = '',
        impact_scope: dict | None = None,
        rollback_plan: str = '',
        ttl: float | None = None,
    ) -> ApprovalRequest:
        """Создаёт формальный ApprovalRequest.

        Args:
            task_id       — ID задачи
            step_id       — ID шага
            action_type   — тип действия (для хеша)
            parameters    — параметры действия (для хеша)
            risk_class    — класс риска ('dangerous', 'guarded', ...)
            summary       — описание для человека
            impact_scope  — затрагиваемые ресурсы
            rollback_plan — план отката
            ttl           — TTL в секундах (None = default)
        """
        approval_id = f"appr_{uuid.uuid4().hex[:12]}"
        action_hash = compute_action_hash(action_type, parameters)
        ttl = ttl if ttl is not None else self._default_ttl

        request = ApprovalRequest(
            approval_id=approval_id,
            task_id=task_id,
            step_id=step_id,
            action_hash=action_hash,
            risk_class=risk_class,
            summary=summary,
            impact_scope=impact_scope or {},
            rollback_plan=rollback_plan,
            expires_at=time.time() + ttl,
        )

        with self._lock:
            self._pending_requests[approval_id] = request

        self._log(f"[approval] Запрос {approval_id} создан: {summary}")
        return request

    # ── Запрос решения у человека + выдача токена ─────────────────────────────

    def request_and_issue(
        self,
        request: ApprovalRequest,
        subject: str,
    ) -> ApprovalToken | None:
        """Запрашивает human approval и при одобрении выдаёт signed token.

        Args:
            request — ApprovalRequest (из create_request)
            subject — worker_id исполнителя (к нему привязывается токен)

        Returns:
            ApprovalToken при одобрении, None при отклонении.
        """
        # Проверяем, не истёк ли запрос
        if time.time() > request.expires_at:
            self._log(f"[approval] Запрос {request.approval_id} истёк до решения")
            return None

        # Запрашиваем human decision
        approved = self._ask_human(request)

        self._log(
            f"[approval] Запрос {request.approval_id}: "
            f"{'ОДОБРЕН' if approved else 'ОТКЛОНЁН'}"
        )

        if not approved:
            return None

        return self._issue_token(request, subject)

    # ── Валидация токена (вызывается Broker / Executor) ───────────────────────

    def validate_token(
        self,
        token: ApprovalToken,
        expected_task_id: str,
        expected_step_id: str,
        expected_action_hash: str,
        expected_subject: str,
    ) -> bool:
        """Валидирует approval token.

        Проверяет:
            1. Подпись (HMAC)
            2. Срок действия
            3. Одноразовость (не использовался ранее)
            4. Привязку к task_id + step_id + action_hash + subject

        Raises:
            TokenSignatureError  — невалидная подпись
            TokenExpiredError    — токен истёк
            TokenReusedError     — повторное использование
            TokenMismatchError   — несовпадение параметров

        Returns:
            True — токен валиден и помечен как использованный.
        """
        # 1. Проверка подписи
        expected_sig = self._sign(
            token.approval_id, token.task_id, token.step_id,
            token.action_hash, token.subject, token.expires_at,
        )
        if not hmac.compare_digest(token.token, expected_sig):
            self._log(f"[approval] ОТКАЗ: невалидная подпись для {token.approval_id}")
            raise TokenSignatureError(
                f"Invalid signature for approval {token.approval_id}"
            )

        # 2. Проверка срока
        if time.time() > token.expires_at:
            self._log(f"[approval] ОТКАЗ: токен {token.approval_id} истёк")
            raise TokenExpiredError(
                f"Token {token.approval_id} expired"
            )

        # 3. Привязка к параметрам
        mismatches = []
        if token.task_id != expected_task_id:
            mismatches.append(f"task_id: {token.task_id} != {expected_task_id}")
        if token.step_id != expected_step_id:
            mismatches.append(f"step_id: {token.step_id} != {expected_step_id}")
        if token.action_hash != expected_action_hash:
            mismatches.append("action_hash mismatch")
        if token.subject != expected_subject:
            mismatches.append(f"subject: {token.subject} != {expected_subject}")

        if mismatches:
            detail = '; '.join(mismatches)
            self._log(f"[approval] ОТКАЗ: несовпадение — {detail}")
            raise TokenMismatchError(f"Token mismatch: {detail}")

        # 4. Одноразовость
        with self._lock:
            if token.token in self._consumed_tokens:
                self._log(f"[approval] ОТКАЗ: токен {token.approval_id} уже использован")
                raise TokenReusedError(
                    f"Token {token.approval_id} already consumed"
                )
            self._consumed_tokens.add(token.token)

        self._log(f"[approval] Токен {token.approval_id} валиден и использован")
        return True

    # ── Вспомогательные методы ────────────────────────────────────────────────

    def is_consumed(self, token: ApprovalToken) -> bool:
        """Проверяет, был ли токен уже использован."""
        return token.token in self._consumed_tokens

    def get_pending_requests(self) -> list[dict]:
        """Возвращает список pending approval requests."""
        with self._lock:
            return [r.to_dict() for r in self._pending_requests.values()]

    def cleanup_expired(self) -> int:
        """Удаляет истёкшие pending requests. Возвращает количество удалённых."""
        now = time.time()
        removed = 0
        with self._lock:
            expired_ids = [
                aid for aid, req in self._pending_requests.items()
                if now > req.expires_at
            ]
            for aid in expired_ids:
                del self._pending_requests[aid]
                removed += 1
        return removed

    # ── Внутренние методы ─────────────────────────────────────────────────────

    def _ask_human(self, request: ApprovalRequest) -> bool:
        """Запрашивает решение через HumanApprovalLayer."""
        if self._human_approval is None:
            # Fail-closed: без human approval → отклонение
            return False

        # Формируем payload для HumanApprovalLayer
        payload = {
            'approval_id': request.approval_id,
            'task_id': request.task_id,
            'step_id': request.step_id,
            'risk_class': request.risk_class,
            'summary': request.summary,
            'impact_scope': request.impact_scope,
            'rollback_plan': request.rollback_plan,
        }
        return self._human_approval.request_approval(request.risk_class, payload)

    def _issue_token(self, request: ApprovalRequest, subject: str) -> ApprovalToken:
        """Генерирует signed token для одобренного запроса."""
        signature = self._sign(
            request.approval_id, request.task_id, request.step_id,
            request.action_hash, subject, request.expires_at,
        )

        token = ApprovalToken(
            token=signature,
            approval_id=request.approval_id,
            task_id=request.task_id,
            step_id=request.step_id,
            action_hash=request.action_hash,
            subject=subject,
            expires_at=request.expires_at,
        )

        with self._lock:
            self._issued_tokens[token.token] = token
            # Удаляем из pending
            self._pending_requests.pop(request.approval_id, None)

        return token

    def _sign(self, approval_id: str, task_id: str, step_id: str,
              action_hash: str, subject: str, expires_at: float) -> str:
        """HMAC-SHA256 подпись связки параметров."""
        payload = f"{approval_id}|{task_id}|{step_id}|{action_hash}|{subject}|{expires_at}"
        return hmac.new(
            self._signing_key, payload.encode(), hashlib.sha256,
        ).hexdigest()

    def _log(self, message: str):
        if self._monitoring:
            self._monitoring.log(message, source='ApprovalService')
