# DenyByDefaultPolicy — прозрачный enforcement-прокси перед ToolLayer
# formal_contracts_spec §5-§6, §12; архитектура: Слой 16 / 21
#
# Оборачивает ToolLayer и маршрутизирует каждый .use() через ToolBroker,
# обеспечивая deny-by-default capability matrix, approval tokens,
# risk classification и audit logging.
#
# ActionDispatcher, TaskExecutor и пр. получают PolicyEnforcedToolLayer
# вместо голого ToolLayer — без изменения своего кода.

from __future__ import annotations

import logging
import threading
from typing import Any

_log = logging.getLogger(__name__)


class PolicyEnforcedToolLayer:
    """Прозрачный proxy вокруг ToolLayer + ToolBroker.

    Имеет тот же интерфейс ``.use(tool_name, **kwargs)`` что и ToolLayer,
    но каждый вызов проходит через ToolBroker:
        capability check → risk classification → approval → execute → audit.

    Если ToolBroker не задан — fallback на прямой ToolLayer (graceful degradation).

    Thread-safe.
    """

    def __init__(
        self,
        tool_layer,
        broker=None,
        *,
        worker_id: str = 'autonomous_loop',
        fallback_on_broker_error: bool = False,
    ):
        """
        Args:
            tool_layer              — оригинальный ToolLayer (Слой 5)
            broker                  — ToolBroker (None = прямой проброс)
            worker_id               — роль по умолчанию для capability matrix
            fallback_on_broker_error — при ошибке broker'а пробовать ToolLayer напрямую?
                                       (False = строгий deny, True = graceful degradation)
        """
        self._tool_layer = tool_layer
        self._broker = broker
        self._worker_id = worker_id
        self._fallback = fallback_on_broker_error
        self._lock = threading.Lock()

        # Счётчики (для monitoring / audit)
        self._stats = {
            'total': 0,
            'enforced': 0,
            'fallback': 0,
            'denied': 0,
        }

    # ── Worker context ────────────────────────────────────────────────────────

    def set_worker_id(self, worker_id: str) -> None:
        """Устанавливает worker_id для последующих вызовов."""
        self._worker_id = worker_id

    @property
    def worker_id(self) -> str:
        return self._worker_id

    # ── Main interface — совместим с ToolLayer.use() ──────────────────────────

    def use(self, tool_name: str, **kwargs: Any) -> Any:
        """Вызов инструмента через ToolBroker enforcement.

        Args:
            tool_name — имя инструмента ('terminal', 'filesystem', ...)
            **kwargs  — параметры инструмента

        Returns:
            Результат вызова (dict или иное — то же что ToolLayer.use).

        Raises:
            CapabilityDeniedError  — worker не имеет доступа
            ProhibitedActionError  — tool запрещён policy
            ApprovalRequiredError  — dangerous без approval
        """
        with self._lock:
            self._stats['total'] += 1

        if self._broker is None:
            # Нет broker'а — прямой вызов (legacy / graceful degradation)
            return self._direct_use(tool_name, **kwargs)

        try:
            result = self._broker.use(
                tool_name,
                worker_id=self._worker_id,
                **kwargs,
            )
            with self._lock:
                self._stats['enforced'] += 1
            return result

        except Exception as exc:
            exc_name = type(exc).__name__
            # BrokerError и его потомки — это policy deny, пробрасываем дальше
            if _is_broker_error(exc):
                with self._lock:
                    self._stats['denied'] += 1
                _log.warning(
                    "DENY tool=%s worker=%s reason=%s: %s",
                    tool_name, self._worker_id, exc_name, exc,
                )
                if self._fallback:
                    _log.warning(
                        "Fallback to direct ToolLayer for tool=%s (denied by broker)",
                        tool_name,
                    )
                    with self._lock:
                        self._stats['fallback'] += 1
                    return self._direct_use(tool_name, **kwargs)
                raise

            # Прочие ошибки (ToolLayer, RuntimeError, ...) — пробрасываем
            raise

    # ── Proxy для прочих атрибутов ToolLayer ──────────────────────────────────

    def __getattr__(self, name: str) -> Any:
        """Проксирует все прочие атрибуты к оригинальному ToolLayer.

        Это позволяет использовать PolicyEnforcedToolLayer как drop-in замену:
        tool_layer.get('terminal'), tool_layer.working_dir и т.д. работают.
        """
        return getattr(self._tool_layer, name)

    # ── Statistics ────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        with self._lock:
            return dict(self._stats)

    # ── Internals ─────────────────────────────────────────────────────────────

    def _direct_use(self, tool_name: str, **kwargs: Any) -> Any:
        """Прямой вызов ToolLayer без enforcement."""
        return self._tool_layer.use(tool_name, **kwargs)


def _is_broker_error(exc: BaseException) -> bool:
    """Определяет, является ли исключение BrokerError (без жёсткого импорта)."""
    for cls in type(exc).__mro__:
        if cls.__name__ == 'BrokerError':
            return True
    return False
