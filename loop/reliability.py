# Reliability System (система надёжности) — Слой 19
# Архитектура автономного AI-агента
# Retry, timeout, fallback, аварийное восстановление, DLQ, классификация ошибок.


import time
import functools
import threading
import multiprocessing
from collections import deque
from collections.abc import Callable
from typing import Any
from dataclasses import dataclass, field
from enum import Enum


def _timeout_worker(queue, func, args, kwargs):
    """Воркeр для выполнения функции в отдельном процессе с возвратом результата."""
    try:
        result = func(*args, **kwargs)
        queue.put(('ok', result))
    except Exception as e:  # pylint: disable=broad-except
        queue.put(('err', e))


class RetryStrategy(Enum):
    FIXED       = 'fixed'        # фиксированная пауза между попытками
    EXPONENTIAL = 'exponential'  # экспоненциальный backoff
    LINEAR      = 'linear'       # линейный рост паузы


class ErrorClass(Enum):
    """Классификация ошибок для retry-решений."""
    TRANSIENT  = 'transient'   # временная — retry имеет смысл (сеть, rate limit, timeout)
    PERMANENT  = 'permanent'   # постоянная — retry бесполезен (логика, валидация, auth)
    UNKNOWN    = 'unknown'     # не классифицирована — retry по умолчанию, но с осторожностью


# Типы исключений, которые считаются transient по умолчанию
_TRANSIENT_EXCEPTIONS: tuple[type[Exception], ...] = (
    ConnectionError, TimeoutError, OSError,
)

# Подстроки в сообщениях ошибок, указывающие на transient
_TRANSIENT_PATTERNS: tuple[str, ...] = (
    'timeout', 'timed out', 'rate limit', 'too many requests',
    'connection reset', 'connection refused', 'temporary',
    'service unavailable', '503', '429', '502', '504',
    'retry', 'overloaded',
)

# Подстроки, указывающие на permanent ошибку
_PERMANENT_PATTERNS: tuple[str, ...] = (
    'not found', '404', 'unauthorized', '401', 'forbidden', '403',
    'invalid', 'validation', 'syntax error', 'permission denied',
    'does not exist', 'unsupported', 'not allowed',
)


def classify_error(exc: Exception) -> ErrorClass:
    """Классифицирует исключение как transient, permanent или unknown."""
    if isinstance(exc, _TRANSIENT_EXCEPTIONS):
        return ErrorClass.TRANSIENT
    msg = str(exc).lower()
    for pat in _TRANSIENT_PATTERNS:
        if pat in msg:
            return ErrorClass.TRANSIENT
    for pat in _PERMANENT_PATTERNS:
        if pat in msg:
            return ErrorClass.PERMANENT
    return ErrorClass.UNKNOWN


@dataclass
class DLQEntry:
    """Запись в Dead Letter Queue — операция, исчерпавшая retry."""
    func_name: str
    args_repr: str
    last_error: str
    error_class: str
    circuit_name: str
    attempts: int
    exhausted_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            'func_name': self.func_name,
            'args_repr': self.args_repr,
            'last_error': self.last_error,
            'error_class': self.error_class,
            'circuit_name': self.circuit_name,
            'attempts': self.attempts,
            'exhausted_at': self.exhausted_at,
        }


class ReliabilitySystem:
    """
    Reliability System — Слой 19.

    Обеспечивает устойчивость системы:
        - retry: повторные попытки при сбоях
        - timeout: ограничение времени выполнения
        - fallback: резервный результат при полном провале
        - circuit breaker: остановка при превышении порога ошибок
        - аварийное восстановление

    Используется:
        - Autonomous Loop (Слой 20)   — обёртка каждой фазы цикла
        - Execution System (Слой 8)   — надёжное выполнение команд
        - Agent System (Слой 4)       — устойчивые вызовы агентов
    """

    def __init__(
        self,
        default_retries: int = 3,
        default_delay: float = 1.0,
        default_strategy: RetryStrategy = RetryStrategy.EXPONENTIAL,
        circuit_threshold: int = 5,
        dlq_max_size: int = 1000,
        monitoring=None,
    ):
        """
        Args:
            default_retries   — кол-во повторных попыток по умолчанию
            default_delay     — базовая пауза между попытками (сек)
            default_strategy  — стратегия паузы
            circuit_threshold — сколько ошибок подряд открывают circuit breaker
            dlq_max_size      — максимальный размер Dead Letter Queue
            monitoring        — Monitoring (Слой 17) для логирования
        """
        self.default_retries = default_retries
        self.default_delay = default_delay
        self.default_strategy = default_strategy
        self.circuit_threshold = circuit_threshold
        self.monitoring = monitoring

        self._circuit_errors: dict[str, int] = {}    # circuit_name → счётчик ошибок
        self._circuit_open: dict[str, bool] = {}     # circuit_name → открыт?
        self._recovery_handlers: dict[str, Callable] = {}

        # DLQ — операции, исчерпавшие retry
        self._dlq: deque[DLQEntry] = deque(maxlen=dlq_max_size)
        self._dlq_lock = threading.Lock()

        # Callbacks при исчерпании retry (exhaustion notification)
        self._exhaustion_callbacks: list[Callable[[DLQEntry], None]] = []

    # ── Retry ─────────────────────────────────────────────────────────────────

    def retry(
        self,
        func: Callable,
        *args,
        retries: int | None = None,
        delay: float | None = None,
        strategy: RetryStrategy | None = None,
        fallback: Any = None,
        exceptions: tuple = (Exception,),
        circuit_name: str | None = None,
        classify: bool = True,
        **kwargs,
    ):
        """
        Выполняет func с повторными попытками при ошибке.

        Args:
            func          — вызываемая функция
            retries       — количество попыток (None = default)
            delay         — базовая пауза (None = default)
            strategy      — стратегия паузы (None = default)
            fallback      — значение или callable, если все попытки провалились
            exceptions    — какие исключения перехватывать
            circuit_name  — имя circuit breaker (None = не использовать)
            classify      — классифицировать ошибки (transient/permanent);
                            permanent → не повторять

        Returns:
            Результат func или fallback.
        """
        retries = retries if retries is not None else self.default_retries
        delay = delay if delay is not None else self.default_delay
        strategy = strategy or self.default_strategy

        # Circuit breaker — если открыт, сразу возвращаем fallback
        if circuit_name and self._circuit_open.get(circuit_name):
            self._log(f"[circuit_breaker] '{circuit_name}' открыт — пропуск вызова")
            return self._resolve_fallback(fallback)

        last_exc = None
        attempts_done = 0
        for attempt in range(1, retries + 1):
            try:
                result = func(*args, **kwargs)
                # Успех — сбрасываем счётчик circuit breaker
                if circuit_name:
                    self._circuit_errors[circuit_name] = 0
                    self._circuit_open[circuit_name] = False
                return result

            except exceptions as e:
                last_exc = e
                attempts_done = attempt
                err_class = classify_error(e) if classify else ErrorClass.UNKNOWN
                self._log(
                    f"[retry] Попытка {attempt}/{retries} провалилась "
                    f"({err_class.value}): {type(e).__name__}: {e}"
                )

                # Permanent ошибка — retry бесполезен, сразу в DLQ
                if classify and err_class == ErrorClass.PERMANENT:
                    self._log(
                        f"[retry] Ошибка '{type(e).__name__}' классифицирована как permanent "
                        f"— retry прекращён после {attempt} попыток"
                    )
                    break

                # Обновляем circuit breaker
                if circuit_name:
                    self._circuit_errors[circuit_name] = \
                        self._circuit_errors.get(circuit_name, 0) + 1
                    if self._circuit_errors[circuit_name] >= self.circuit_threshold:
                        self._circuit_open[circuit_name] = True
                        self._log(f"[circuit_breaker] '{circuit_name}' открыт после "
                                  f"{self.circuit_threshold} ошибок")
                        break

                if attempt < retries:
                    pause = self._calc_pause(delay, attempt, strategy)
                    self._log(f"[retry] Ожидание {pause:.1f}s перед следующей попыткой...")
                    time.sleep(pause)

        # Все попытки исчерпаны — отправляем в DLQ и нотифицируем
        self._log(f"[retry] Все {attempts_done} попыток провалились. Последняя ошибка: {last_exc}")
        err_cls = classify_error(last_exc) if (classify and last_exc) else ErrorClass.UNKNOWN
        self._send_to_dlq(
            func=func,
            args=args,
            last_exc=last_exc,
            error_class=err_cls,
            circuit_name=circuit_name or '',
            attempts=attempts_done,
        )
        return self._resolve_fallback(fallback)

    # ── Timeout ───────────────────────────────────────────────────────────────

    def with_timeout(self, func: Callable, timeout_seconds: float, *args, fallback: Any = None, **kwargs):
        """
        Выполняет func с ограничением по времени.
        При превышении timeout — возвращает fallback.

        Примечание: использует threading для совместимости с Windows.
        """
        queue = multiprocessing.Queue(maxsize=1)
        process = multiprocessing.Process(
            target=_timeout_worker,
            args=(queue, func, args, kwargs),
            daemon=True,
        )

        try:
            process.start()
            process.join(timeout=timeout_seconds)

            if process.is_alive():
                process.terminate()
                process.join(timeout=1.0)
                self._log(f"[timeout] Функция '{func.__name__}' превысила {timeout_seconds}s")
                return self._resolve_fallback(fallback)

            if not queue.empty():
                status, payload = queue.get_nowait()
                if status == 'err':
                    raise payload
                return payload

            # Процесс завершился без результата (например, аварийное завершение)
            return self._resolve_fallback(fallback)
        except (OSError, RuntimeError, ValueError, TypeError):
            # Fallback для непереносимых/pickle-сложных функций
            result_holder: list[Any] = [None]
            exc_holder: list[Exception | None] = [None]

            def _target():
                try:
                    result_holder[0] = func(*args, **kwargs)
                except Exception as e:  # pylint: disable=broad-except
                    exc_holder[0] = e

            thread = threading.Thread(target=_target, daemon=True)
            thread.start()
            thread.join(timeout=timeout_seconds)
            if thread.is_alive():
                self._log(f"[timeout] Функция '{func.__name__}' превысила {timeout_seconds}s")
                return self._resolve_fallback(fallback)

            exc = exc_holder[0]
            if exc is not None:
                raise exc from exc
            return result_holder[0]

    # ── Circuit Breaker ───────────────────────────────────────────────────────

    def reset_circuit(self, circuit_name: str):
        """Вручную сбрасывает circuit breaker."""
        self._circuit_errors[circuit_name] = 0
        self._circuit_open[circuit_name] = False
        self._log(f"[circuit_breaker] '{circuit_name}' сброшен вручную")

    def is_circuit_open(self, circuit_name: str) -> bool:
        return self._circuit_open.get(circuit_name, False)

    def get_circuit_status(self) -> dict:
        return {
            name: {'open': self._circuit_open.get(name, False),
                   'errors': self._circuit_errors.get(name, 0)}
            for name in set(list(self._circuit_errors.keys()) + list(self._circuit_open.keys()))
        }

    # ── Dead Letter Queue ─────────────────────────────────────────────────────

    def _send_to_dlq(self, func: Callable, args: tuple,
                     last_exc: Exception | None, error_class: ErrorClass,
                     circuit_name: str, attempts: int):
        """Добавляет провалившуюся операцию в DLQ и уведомляет подписчиков."""
        args_repr = repr(args[:3])[:200] if args else ''
        entry = DLQEntry(
            func_name=getattr(func, '__name__', str(func)),
            args_repr=args_repr,
            last_error=f"{type(last_exc).__name__}: {last_exc}" if last_exc else 'unknown',
            error_class=error_class.value,
            circuit_name=circuit_name,
            attempts=attempts,
        )
        with self._dlq_lock:
            self._dlq.append(entry)
        self._log(
            f"[dlq] Операция '{entry.func_name}' добавлена в DLQ "
            f"(класс: {error_class.value}, попыток: {attempts}). "
            f"Размер DLQ: {len(self._dlq)}"
        )
        # Уведомляем всех подписчиков об исчерпании retry
        for cb in self._exhaustion_callbacks:
            try:
                cb(entry)
            except Exception:  # pylint: disable=broad-except
                pass

    def on_exhaustion(self, callback: Callable[[DLQEntry], None]):
        """Подписаться на событие исчерпания retry (exhaustion notification).

        callback получает DLQEntry с информацией о провалившейся операции.
        Используется для: Telegram-алертов, эскалации в human_approval, логирования.
        """
        self._exhaustion_callbacks.append(callback)

    def get_dlq(self) -> list[dict]:
        """Возвращает все записи DLQ (копия)."""
        with self._dlq_lock:
            return [e.to_dict() for e in self._dlq]

    def dlq_size(self) -> int:
        """Текущий размер DLQ."""
        return len(self._dlq)

    def dlq_pop(self) -> DLQEntry | None:
        """Извлекает самую старую запись из DLQ (для ручной обработки)."""
        with self._dlq_lock:
            if self._dlq:
                return self._dlq.popleft()
        return None

    def dlq_clear(self):
        """Очищает DLQ (например, после ручного review)."""
        with self._dlq_lock:
            self._dlq.clear()
        self._log("[dlq] DLQ очищена")

    # ── Recovery handlers ─────────────────────────────────────────────────────

    def register_recovery(self, name: str, handler: Callable):
        """Регистрирует обработчик аварийного восстановления."""
        self._recovery_handlers[name] = handler

    def recover(self, name: str, *args, **kwargs):
        """Вызывает зарегистрированный обработчик восстановления."""
        handler = self._recovery_handlers.get(name)
        if not handler:
            self._log(f"[recovery] Обработчик '{name}' не зарегистрирован")
            return None
        self._log(f"[recovery] Запуск обработчика '{name}'")
        return handler(*args, **kwargs)

    # ── Декоратор ─────────────────────────────────────────────────────────────

    def reliable(self, retries: int | None = None, delay: float | None = None,
                 fallback=None, circuit_name: str | None = None):
        """
        Декоратор: оборачивает функцию в retry + circuit breaker.

        Пример:
            @reliability.reliable(retries=3, fallback='default')
            def my_func():
                ...
        """
        def decorator(func):
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                return self.retry(
                    func, *args,
                    retries=retries,
                    delay=delay,
                    fallback=fallback,
                    circuit_name=circuit_name,
                    **kwargs,
                )
            return wrapper
        return decorator

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _calc_pause(self, base: float, attempt: int, strategy: RetryStrategy) -> float:
        if strategy == RetryStrategy.FIXED:
            return base
        elif strategy == RetryStrategy.LINEAR:
            return base * attempt
        else:  # EXPONENTIAL
            return base * (2 ** (attempt - 1))

    def _resolve_fallback(self, fallback):
        if callable(fallback):
            return fallback()
        return fallback

    def _log(self, message: str):
        if self.monitoring:
            self.monitoring.warning(message, source='reliability')
        else:
            print(f"[Reliability] {message}")
