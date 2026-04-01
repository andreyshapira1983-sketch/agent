# Monitoring & Logging (мониторинг и логирование) — Слой 17
# Архитектура автономного AI-агента
# Наблюдение за системой: логи, метрики, трассировки, анализ ошибок.


import time
import traceback
from enum import Enum
from collections import defaultdict


class LogLevel(Enum):
    DEBUG   = 'DEBUG'
    INFO    = 'INFO'
    WARNING = 'WARNING'
    ERROR   = 'ERROR'
    CRITICAL = 'CRITICAL'


class LogEntry:
    """Одна запись в логе."""

    def __init__(self, level: LogLevel, message: str, source: str | None = None, data: dict | None = None):
        self.timestamp = time.time()
        self.level = level
        self.message = message
        self.source = source      # имя компонента (слоя), который пишет лог
        self.data = data or {}

    def to_dict(self):
        return {
            'timestamp': self.timestamp,
            'time_str': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.timestamp)),
            'level': self.level.value,
            'source': self.source,
            'message': self.message,
            'data': self.data,
        }

    def __str__(self):
        ts = time.strftime('%H:%M:%S', time.localtime(self.timestamp))
        src = f"[{self.source}] " if self.source else ""
        return f"{ts} {self.level.value:8s} {src}{self.message}"


class Metric:
    """Одна метрика с историей значений."""

    def __init__(self, name: str, unit: str = ''):
        self.name = name
        self.unit = unit
        self._values: list[tuple[float, float]] = []  # (timestamp, value)

    def record(self, value: float):
        self._values.append((time.time(), value))
        # Храним последние 2000 значений чтобы избежать утечки памяти
        if len(self._values) > 2000:
            self._values = self._values[-2000:]

    def last(self) -> float | None:
        return self._values[-1][1] if self._values else None

    def average(self) -> float | None:
        if not self._values:
            return None
        return sum(v for _, v in self._values) / len(self._values)

    def total(self) -> float:
        return sum(v for _, v in self._values)

    def count(self) -> int:
        return len(self._values)

    def to_dict(self):
        return {
            'name': self.name,
            'unit': self.unit,
            'count': self.count(),
            'last': self.last(),
            'average': self.average(),
            'total': self.total(),
        }


class Monitoring:
    """
    Monitoring & Logging System — Слой 17.

    Функции:
        - логирование событий всех уровней (DEBUG → CRITICAL)
        - метрики: счётчики, latency, стоимость, токены
        - трассировка вызовов (trace_id)
        - анализ ошибок: история, частота, последняя ошибка
        - логирование исполнения задач (используется Execution System, Слой 8)

    Используется всеми слоями системы.
    Может писать в консоль, файл или внешнюю систему (подключаемый sink).
    """

    def __init__(
        self,
        min_level: LogLevel = LogLevel.INFO,
        print_logs: bool = True,
        log_file: str | None = None,
        sink=None,
        scrubber=None,
    ):
        """
        Args:
            min_level  — минимальный уровень для вывода (DEBUG/INFO/WARNING/ERROR/CRITICAL)
            print_logs — выводить логи в консоль
            log_file   — путь к файлу для записи логов (None = не писать в файл)
            sink       — внешний обработчик: объект с методом write(entry: dict)
            scrubber   — callable(str) → str, маскирует секреты в тексте перед записью
        """
        self.min_level = min_level
        self.print_logs = print_logs
        self.log_file = log_file
        self.sink = sink
        self._scrubber = scrubber  # SECURITY: авто-маскировка секретов

        self._logs: list[LogEntry] = []
        self._max_logs: int = 10_000   # ограничение в памяти — старые записи обрезаются
        self._metrics: dict[str, Metric] = {}
        self._error_counts: dict[str, int] = defaultdict(int)
        self._active_traces: dict[str, list] = {}  # trace_id → список событий

    # ── Logging ───────────────────────────────────────────────────────────────

    def log(self, message: str, level: LogLevel = LogLevel.INFO, source: str | None = None, data: dict | None = None):
        """Записывает событие в лог. SECURITY: автоматически маскирует секреты."""
        if self._scrubber:
            message = self._scrubber(message)
            if data:
                data = self._scrub_data(data)
        entry = LogEntry(level, message, source=source, data=data)
        self._logs.append(entry)
        # Обрезаем старые записи чтобы не было утечки памяти
        if len(self._logs) > self._max_logs:
            self._logs = self._logs[-self._max_logs:]

        if self._should_print(level):
            try:
                print(str(entry))
            except UnicodeEncodeError:
                print(str(entry).encode('ascii', errors='replace').decode('ascii'))

        if self.log_file:
            self._write_to_file(entry)

        if self.sink:
            self.sink.write(entry.to_dict())

        if level in (LogLevel.ERROR, LogLevel.CRITICAL):
            self._error_counts[source or 'unknown'] += 1

    def debug(self, message, source=None, data=None):
        self.log(message, LogLevel.DEBUG, source=source, data=data)

    def info(self, message, source=None, data=None):
        self.log(message, LogLevel.INFO, source=source, data=data)

    def warning(self, message, source=None, data=None):
        self.log(message, LogLevel.WARNING, source=source, data=data)

    def error(self, message, source=None, data=None):
        self.log(message, LogLevel.ERROR, source=source, data=data)

    def critical(self, message, source=None, data=None):
        self.log(message, LogLevel.CRITICAL, source=source, data=data)

    def log_exception(self, exc: Exception, source: str | None = None):
        """Логирует исключение с полным traceback."""
        tb = traceback.format_exc()
        self.error(f"{type(exc).__name__}: {exc}\n{tb}", source=source, data={'exception': str(exc)})

    # ── Execution logging (используется Execution System, Слой 8) ─────────────

    def log_execution(self, task_dict: dict):
        """Принимает результат задачи от Execution System и записывает в лог."""
        status = task_dict.get('status', 'unknown')
        level = LogLevel.INFO if status in ('success', 'pending', 'running') else LogLevel.ERROR
        self.log(
            message=f"Execution [{task_dict.get('task_id')}] {task_dict.get('command')} → {status}",
            level=level,
            source='execution',
            data=task_dict,
        )

    # ── Metrics ───────────────────────────────────────────────────────────────

    def record_metric(self, name: str, value: float, unit: str = ''):
        """Записывает значение метрики."""
        if name not in self._metrics:
            self._metrics[name] = Metric(name, unit)
        self._metrics[name].record(value)

    def increment(self, name: str, by: float = 1.0):
        """Увеличивает счётчик метрики."""
        self.record_metric(name, by)

    def record_latency(self, name: str, seconds: float):
        """Записывает latency в секундах."""
        self.record_metric(f"latency.{name}", seconds, unit='s')

    def record_tokens(self, prompt_tokens: int, completion_tokens: int, source: str | None = None):
        """Записывает использование токенов (Resource & Budget Control, Слой 26)."""
        self.record_metric('tokens.prompt', prompt_tokens)
        self.record_metric('tokens.completion', completion_tokens)
        self.record_metric('tokens.total', prompt_tokens + completion_tokens)
        self.info(
            f"Токены: prompt={prompt_tokens}, completion={completion_tokens}",
            source=source or 'llm',
        )

    def get_metric(self, name: str) -> Metric | None:
        return self._metrics.get(name)

    def get_all_metrics(self) -> dict:
        return {name: m.to_dict() for name, m in self._metrics.items()}

    # ── Tracing ───────────────────────────────────────────────────────────────

    def start_trace(self, trace_id: str, label: str = ''):
        """Начинает трассировку операции."""
        self._active_traces[trace_id] = [{'event': 'start', 'label': label, 'ts': time.time()}]
        self.debug(f"Trace started: {trace_id} {label}", source='tracing')

    def trace_event(self, trace_id: str, event: str, data: dict | None = None):
        """Добавляет событие в трассировку."""
        if trace_id not in self._active_traces:
            self.start_trace(trace_id)
        self._active_traces[trace_id].append({
            'event': event, 'data': data or {}, 'ts': time.time()
        })

    def end_trace(self, trace_id: str) -> list:
        """Завершает трассировку и возвращает полный список событий."""
        if trace_id not in self._active_traces:
            return []
        events = self._active_traces.pop(trace_id)
        events.append({'event': 'end', 'ts': time.time()})
        duration = events[-1]['ts'] - events[0]['ts']
        self.debug(f"Trace ended: {trace_id}, duration={duration:.3f}s", source='tracing')
        return events

    # ── Error analysis ────────────────────────────────────────────────────────

    def get_error_counts(self) -> dict:
        """Возвращает количество ошибок по источнику."""
        return dict(self._error_counts)

    def get_errors(self) -> list:
        """Возвращает все записи уровня ERROR и CRITICAL."""
        return [e.to_dict() for e in self._logs if e.level in (LogLevel.ERROR, LogLevel.CRITICAL)]

    def get_last_error(self) -> dict | None:
        errors = self.get_errors()
        return errors[-1] if errors else None

    # ── Log access ────────────────────────────────────────────────────────────

    def get_logs(self, level: LogLevel | None = None, source: str | None = None, last_n: int | None = None) -> list:
        """Возвращает записи лога с фильтрацией."""
        logs = self._logs
        if level:
            logs = [e for e in logs if e.level == level]
        if source:
            logs = [e for e in logs if e.source == source]
        if last_n:
            logs = logs[-last_n:]
        return [e.to_dict() for e in logs]

    def clear_logs(self):
        """Очищает все логи (осторожно — необратимо)."""
        self._logs.clear()

    # ── Summary ───────────────────────────────────────────────────────────────

    def summary(self) -> dict:
        """Сводка состояния системы."""
        smoke_passed = self.get_metric('core_smoke_passed')
        smoke_failed = self.get_metric('core_smoke_failed')
        passed_count = smoke_passed.count() if smoke_passed else 0
        failed_count = smoke_failed.count() if smoke_failed else 0

        last_smoke_event = None
        for entry in reversed(self._logs):
            if entry.message in ('core_smoke_passed', 'core_smoke_failed'):
                last_smoke_event = entry.to_dict()
                break

        return {
            'total_logs': len(self._logs),
            'errors': sum(self._error_counts.values()),
            'error_by_source': self.get_error_counts(),
            'metrics': self.get_all_metrics(),
            'active_traces': list(self._active_traces.keys()),
            'core_smoke': {
                'passed': passed_count,
                'failed': failed_count,
                'last_event': last_smoke_event,
                'healthy': failed_count == 0 or passed_count >= failed_count,
            },
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _should_print(self, level: LogLevel) -> bool:
        levels = [lvl for lvl in LogLevel]
        return self.print_logs and levels.index(level) >= levels.index(self.min_level)

    def _scrub_data(self, data: dict) -> dict:
        """Рекурсивно маскирует секреты в data-словаре."""
        if not self._scrubber or not isinstance(data, dict):
            return data
        result = {}
        for k, v in data.items():
            if isinstance(v, str):
                result[k] = self._scrubber(v)
            elif isinstance(v, dict):
                result[k] = self._scrub_data(v)
            else:
                result[k] = v
        return result

    def _write_to_file(self, entry: LogEntry):
        if not self.log_file:
            return
        try:
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(str(entry) + '\n')
        except Exception:  # pylint: disable=broad-except
            pass
