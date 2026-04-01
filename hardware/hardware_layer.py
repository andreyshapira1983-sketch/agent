# Hardware Interaction Layer (взаимодействие с оборудованием) — Слой 44
# Архитектура автономного AI-агента
# Мониторинг и управление ресурсами хоста: CPU, RAM, диск, GPU, сеть.
# pylint: disable=broad-except,import-error


import time
import threading
import os
import importlib


class HardwareMetrics:
    """Снимок текущего состояния аппаратных ресурсов."""

    def __init__(self):
        self.cpu_percent: float = 0.0
        self.memory_percent: float = 0.0
        self.memory_used_mb: float = 0.0
        self.memory_total_mb: float = 0.0
        self.disk_percent: float = 0.0
        self.disk_free_gb: float = 0.0
        self.gpu_percent: float | None = None
        self.gpu_memory_percent: float | None = None
        self.net_sent_mb: float = 0.0
        self.net_recv_mb: float = 0.0
        self.timestamp: float = time.time()

    def to_dict(self):
        return {
            'timestamp': self.timestamp,
            'cpu_percent': self.cpu_percent,
            'memory_percent': self.memory_percent,
            'memory_used_mb': round(self.memory_used_mb, 1),
            'memory_total_mb': round(self.memory_total_mb, 1),
            'disk_percent': self.disk_percent,
            'disk_free_gb': round(self.disk_free_gb, 2),
            'gpu_percent': self.gpu_percent,
            'net_sent_mb': round(self.net_sent_mb, 2),
            'net_recv_mb': round(self.net_recv_mb, 2),
        }


class ResourceThreshold:
    """Порог ресурса, при превышении которого срабатывает предупреждение."""

    def __init__(self, resource: str, warn_at: float, critical_at: float):
        self.resource = resource
        self.warn_at = warn_at
        self.critical_at = critical_at


class HardwareInteractionLayer:
    """
    Hardware Interaction Layer — Слой 44.

    Функции:
        - сбор метрик хоста: CPU, RAM, диск, GPU, сеть (через psutil/GPUtil)
        - мониторинг порогов и генерация предупреждений
        - ограничение задач при нехватке ресурсов
        - управление процессами: список, завершение (с осторожностью)
        - информация о системе: ОС, платформа, Python-версия
        - фоновый мониторинг с интервалом опроса
        - интеграция с Monitoring и Budget Control

    Используется:
        - Budget Control (Слой 26)      — ограничение по памяти/CPU
        - Monitoring (Слой 17)          — отправка метрик
        - Autonomous Loop (Слой 20)     — проверка перед тяжёлыми задачами
        - Reliability (Слой 19)         — реакция на исчерпание ресурсов
    """

    DEFAULT_THRESHOLDS = [
        ResourceThreshold('cpu_percent',    warn_at=88.0,  critical_at=97.0),
        ResourceThreshold('memory_percent', warn_at=88.0,  critical_at=97.0),
        ResourceThreshold('disk_percent',   warn_at=85.0,  critical_at=95.0),
    ]

    def __init__(self, monitoring=None, poll_interval: float = 60.0):
        self.monitoring = monitoring
        self.poll_interval = poll_interval

        self._thresholds = list(self.DEFAULT_THRESHOLDS)
        self._last_metrics: HardwareMetrics | None = None
        self._history: list[HardwareMetrics] = []
        self._alerts: list[dict] = []
        self._running = False
        self._thread: threading.Thread | None = None

    # ── Сбор метрик ───────────────────────────────────────────────────────────

    def collect(self) -> HardwareMetrics:
        """Собирает текущие метрики аппаратных ресурсов."""
        m = HardwareMetrics()
        try:
            psutil = importlib.import_module('psutil')
            m.cpu_percent = psutil.cpu_percent(interval=0.5)
            mem = psutil.virtual_memory()
            m.memory_percent = mem.percent
            m.memory_used_mb = mem.used / 1_048_576
            m.memory_total_mb = mem.total / 1_048_576
            if os.name == 'nt':
                drive = os.path.splitdrive(os.getcwd())[0] or 'C:'
                disk_path = f"{drive}\\"
            else:
                disk_path = '/'
            disk = psutil.disk_usage(disk_path)
            m.disk_percent = disk.percent
            m.disk_free_gb = disk.free / 1_073_741_824
            net = psutil.net_io_counters()
            m.net_sent_mb = net.bytes_sent / 1_048_576
            m.net_recv_mb = net.bytes_recv / 1_048_576
        except ImportError:
            self._log("psutil не установлен — метрики недоступны")
        except Exception as e:
            self._log(f"Ошибка сбора метрик: {e}")

        try:
            import GPUtil  # type: ignore[import-untyped]
            gpus = GPUtil.getGPUs()
            if gpus:
                m.gpu_percent = gpus[0].load * 100
                m.gpu_memory_percent = gpus[0].memoryUtil * 100
        except (ImportError, OSError, RuntimeError, TypeError, ValueError, AttributeError):
            pass

        self._last_metrics = m
        self._history.append(m)
        if len(self._history) > 1000:
            self._history = self._history[-500:]

        self._check_thresholds(m)
        return m

    def get_last(self) -> HardwareMetrics | None:
        return self._last_metrics

    # ── Проверка порогов ──────────────────────────────────────────────────────

    def check_resource(self, resource: str, value: float) -> str:
        """Возвращает 'ok' / 'warn' / 'critical' для заданного ресурса."""
        for th in self._thresholds:
            if th.resource == resource:
                if value >= th.critical_at:
                    return 'critical'
                if value >= th.warn_at:
                    return 'warn'
        return 'ok'

    def is_overloaded(self) -> bool:
        """True если любой ресурс в критическом состоянии."""
        m = self._last_metrics or self.collect()
        return any([
            self.check_resource('cpu_percent', m.cpu_percent) == 'critical',
            self.check_resource('memory_percent', m.memory_percent) == 'critical',
            self.check_resource('disk_percent', m.disk_percent) == 'critical',
        ])

    def has_capacity_for(self, required_memory_mb: float = 0,
                          required_cpu_percent: float = 0) -> bool:
        """Проверяет, есть ли достаточно ресурсов для задачи."""
        m = self._last_metrics or self.collect()
        free_mem = m.memory_total_mb - m.memory_used_mb
        available_cpu = 100 - m.cpu_percent
        return free_mem >= required_memory_mb and available_cpu >= required_cpu_percent

    # ── Системная информация ──────────────────────────────────────────────────

    def system_info(self) -> dict:
        """Возвращает базовую информацию о системе."""
        import platform
        import sys
        info: dict[str, object] = {
            'os': platform.system(),
            'os_version': platform.version(),
            'architecture': platform.machine(),
            'python': sys.version,
            'hostname': platform.node(),
        }
        try:
            psutil = importlib.import_module('psutil')
            info['cpu_count'] = psutil.cpu_count(logical=True)
            info['cpu_count_physical'] = psutil.cpu_count(logical=False)
            info['total_memory_gb'] = round(
                psutil.virtual_memory().total / 1_073_741_824, 2
            )
            info['boot_time'] = psutil.boot_time()
        except ImportError:
            pass
        return info

    def list_processes(self, n: int = 20) -> list[dict]:
        """Возвращает топ-N процессов по использованию CPU."""
        try:
            psutil = importlib.import_module('psutil')
            procs = []
            for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']):
                try:
                    procs.append(p.info)
                except (OSError, RuntimeError, TypeError, ValueError, AttributeError):
                    pass
            procs.sort(key=lambda x: x.get('cpu_percent', 0), reverse=True)
            return procs[:n]
        except ImportError:
            return []

    # ── Фоновый мониторинг ────────────────────────────────────────────────────

    def start_monitoring(self):
        """Запускает фоновый поток сбора метрик."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        self._log(f"Фоновый мониторинг запущен (интервал: {self.poll_interval}с)")

    def stop_monitoring(self):
        self._running = False
        self._log("Фоновый мониторинг остановлен")

    def _poll_loop(self):
        while self._running:
            self.collect()
            time.sleep(self.poll_interval)

    # ── Статистика ────────────────────────────────────────────────────────────

    def get_alerts(self) -> list[dict]:
        return list(self._alerts)

    def summary(self) -> dict:
        m = self._last_metrics
        if not m:
            return {'status': 'no data'}
        return {
            'status': 'critical' if self.is_overloaded() else 'ok',
            'cpu_percent': m.cpu_percent,
            'memory_percent': m.memory_percent,
            'disk_percent': m.disk_percent,
            'alerts': len(self._alerts),
        }

    def resource_trend_summary(self, window: int = 20) -> dict:
        """Анализ трендов ресурсов по последним `window` снимкам.

        Returns:
            {
              'cpu':    {'avg': float, 'max': float, 'trend': 'rising'|'stable'|'falling'},
              'memory': {...},
              'disk':   {'avg': float, 'max': float},
              'breach_events': int,   # сколько раз превышали warn-порог
              'window_size': int,
            }
        """
        recent = self._history[-window:] if self._history else []
        if not recent:
            return {'window_size': 0, 'breach_events': 0}

        def _stats(values: list[float]) -> dict:
            avg = sum(values) / len(values)
            # Тренд: сравниваем первую и вторую половины окна
            half = max(1, len(values) // 2)
            first_half_avg  = sum(values[:half]) / half
            second_half_avg = sum(values[half:]) / max(1, len(values) - half)
            delta = second_half_avg - first_half_avg
            if delta > 5:
                trend = 'rising'
            elif delta < -5:
                trend = 'falling'
            else:
                trend = 'stable'
            return {'avg': round(avg, 1), 'max': round(max(values), 1), 'trend': trend}

        cpu_vals  = [m.cpu_percent    for m in recent]
        mem_vals  = [m.memory_percent for m in recent]
        disk_vals = [m.disk_percent   for m in recent]

        breach_events = sum(
            1 for m in recent
            if self.check_resource('cpu_percent',    m.cpu_percent)    != 'ok'
            or self.check_resource('memory_percent', m.memory_percent) != 'ok'
            or self.check_resource('disk_percent',   m.disk_percent)   != 'ok'
        )

        return {
            'cpu':           _stats(cpu_vals),
            'memory':        _stats(mem_vals),
            'disk':          {k: v for k, v in _stats(disk_vals).items() if k != 'trend'},
            'breach_events': breach_events,
            'window_size':   len(recent),
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _check_thresholds(self, m: HardwareMetrics):
        checks = [
            ('cpu_percent', m.cpu_percent),
            ('memory_percent', m.memory_percent),
            ('disk_percent', m.disk_percent),
        ]
        for resource, value in checks:
            status = self.check_resource(resource, value)
            if status in ('warn', 'critical'):
                alert = {
                    'resource': resource,
                    'value': value,
                    'level': status,
                    'timestamp': time.time(),
                }
                self._alerts.append(alert)
                # Храним только последние 200 алертов
                if len(self._alerts) > 200:
                    self._alerts = self._alerts[-200:]
                self._log(f"[{status.upper()}] {resource}={value:.1f}%")
                if self.monitoring:
                    self.monitoring.warning(
                        f"Аппаратный ресурс {resource}={value:.1f}% [{status}]",
                        source='hardware_layer'
                    )

    def _log(self, message: str):
        if self.monitoring:
            self.monitoring.info(message, source='hardware_layer')
        else:
            print(f"[HardwareLayer] {message}")
