# Distributed Execution Layer (распределённое выполнение) — Слой 34
# pylint: disable=broad-except
# Архитектура автономного AI-агента
# Распределение задач по узлам, масштабирование, управление вычислительными узлами.


import threading
import time
import uuid
from collections.abc import Callable
from typing import Any
from enum import Enum


class NodeStatus(Enum):
    IDLE     = 'idle'
    BUSY     = 'busy'
    OFFLINE  = 'offline'
    FAILED   = 'failed'


class WorkerNode:
    """Вычислительный узел в распределённой системе."""

    def __init__(self, node_id: str, capacity: int = 4, metadata: dict | None = None):
        self.node_id = node_id
        self.capacity = capacity           # макс. параллельных задач
        self.metadata = metadata or {}
        self.status = NodeStatus.IDLE
        self.active_tasks: list[str] = []
        self.completed = 0
        self.failed = 0
        self.registered_at = time.time()

    @property
    def load(self) -> float:
        return len(self.active_tasks) / self.capacity if self.capacity else 1.0

    @property
    def available(self) -> bool:
        return self.status != NodeStatus.OFFLINE and len(self.active_tasks) < self.capacity

    def to_dict(self):
        return {
            'node_id': self.node_id,
            'status': self.status.value,
            'capacity': self.capacity,
            'active_tasks': len(self.active_tasks),
            'load': round(self.load, 2),
            'completed': self.completed,
            'failed': self.failed,
        }


class DistributedTask:
    """Задача для распределённого выполнения."""

    def __init__(self, task_id: str, func: Callable, args: tuple = (),
                 kwargs: dict | None = None, priority: int = 2):
        self.task_id = task_id
        self.func = func
        self.args = args
        self.kwargs = kwargs or {}
        self.priority = priority
        self.status = 'pending'
        self.result: Any = None
        self.error: str | None = None
        self.node_id: str | None = None
        self.started_at: float | None = None
        self.finished_at: float | None = None

    @property
    def duration(self) -> float | None:
        if self.started_at and self.finished_at:
            return round(self.finished_at - self.started_at, 3)
        return None

    def to_dict(self):
        return {
            'task_id': self.task_id,
            'status': self.status,
            'node_id': self.node_id,
            'priority': self.priority,
            'duration': self.duration,
            'error': self.error,
        }


class DistributedExecutionLayer:
    """
    Distributed Execution Layer — Слой 34.

    Функции:
        - пул рабочих узлов (threads / workers)
        - распределение задач по наименее загруженным узлам
        - параллельное выполнение независимых задач
        - масштабирование: добавление/удаление узлов
        - мониторинг нагрузки и балансировка
        - сбор результатов из всех узлов

    Используется:
        - Orchestration System (Слой 18) — передаёт задачи на распределение
        - Execution System (Слой 8)      — исполнение на конкретных узлах
        - Monitoring (Слой 17)           — метрики нагрузки
    """

    def __init__(self, default_workers: int = 4, monitoring=None):
        self.monitoring = monitoring
        self._nodes: dict[str, WorkerNode] = {}
        self._tasks: dict[str, DistributedTask] = {}
        self._lock = threading.Lock()
        self._results: dict[str, Any] = {}

        # Создаём локальные worker-узлы (потоки)
        for i in range(default_workers):
            self._add_local_node(f"worker-{i+1}")

    # ── Управление узлами ─────────────────────────────────────────────────────

    def add_node(self, node_id: str, capacity: int = 4,
                 metadata: dict | None = None) -> WorkerNode:
        """Регистрирует новый узел в кластере."""
        node = WorkerNode(node_id, capacity=capacity, metadata=metadata)
        with self._lock:
            self._nodes[node_id] = node
        self._log(f"Узел добавлен: '{node_id}' (ёмкость: {capacity})")
        return node

    def remove_node(self, node_id: str):
        """Удаляет узел (после завершения текущих задач)."""
        with self._lock:
            node = self._nodes.get(node_id)
            if node:
                node.status = NodeStatus.OFFLINE
                self._nodes.pop(node_id, None)
        self._log(f"Узел удалён: '{node_id}'")

    def list_nodes(self) -> list[dict]:
        return [n.to_dict() for n in self._nodes.values()]

    # ── Отправка задач ────────────────────────────────────────────────────────

    def submit(self, func: Callable, *args, priority: int = 2,
               **kwargs) -> DistributedTask:
        """
        Отправляет задачу на выполнение на наименее загруженный узел.

        Returns:
            DistributedTask — можно отслеживать статус через task.status.
        """
        task = DistributedTask(
            task_id=str(uuid.uuid4())[:8],
            func=func, args=args, kwargs=kwargs, priority=priority,
        )
        with self._lock:
            self._tasks[task.task_id] = task

        node = self._pick_node()
        if not node:
            task.status = 'queued'
            self._log(f"Задача [{task.task_id}] в очереди — нет свободных узлов")
            # Запускаем в отдельном потоке с ожиданием
            threading.Thread(target=self._wait_and_run, args=(task,), daemon=True).start()
        else:
            self._dispatch(task, node)

        return task

    def submit_batch(self, tasks: list[tuple]) -> list[DistributedTask]:
        """
        Отправляет несколько задач параллельно.
        tasks — список (func, args, kwargs).
        """
        results = []
        for item in tasks:
            func = item[0]
            args = item[1] if len(item) > 1 else ()
            kwargs = item[2] if len(item) > 2 else {}
            results.append(self.submit(func, *args, **kwargs))
        return results

    def wait_all(self, tasks: list[DistributedTask],
                 timeout: float = 60.0) -> list[DistributedTask]:
        """Ждёт завершения всех задач (блокирующий вызов)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if all(t.status in ('done', 'failed') for t in tasks):
                break
            time.sleep(0.1)
        return tasks

    def map(self, func: Callable, items: list,
            timeout: float = 60.0) -> list:
        """
        Применяет func к каждому элементу items параллельно.
        Аналог concurrent.futures.map, но через узлы кластера.
        """
        tasks = [self.submit(func, item) for item in items]
        self.wait_all(tasks, timeout=timeout)
        return [t.result for t in tasks]

    # ── Статистика ────────────────────────────────────────────────────────────

    def get_task(self, task_id: str) -> DistributedTask | None:
        return self._tasks.get(task_id)

    def summary(self) -> dict:
        tasks = list(self._tasks.values())
        nodes = list(self._nodes.values())
        return {
            'nodes': len(nodes),
            'avg_load': round(sum(n.load for n in nodes) / max(1, len(nodes)), 2),
            'tasks_total': len(tasks),
            'tasks_done': sum(1 for t in tasks if t.status == 'done'),
            'tasks_failed': sum(1 for t in tasks if t.status == 'failed'),
            'tasks_running': sum(1 for t in tasks if t.status == 'running'),
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _add_local_node(self, node_id: str):
        node = WorkerNode(node_id, capacity=1)
        self._nodes[node_id] = node

    def _pick_node(self) -> WorkerNode | None:
        """Выбирает наименее загруженный доступный узел."""
        with self._lock:
            available = [n for n in self._nodes.values() if n.available]
        if not available:
            return None
        return min(available, key=lambda n: n.load)

    def _dispatch(self, task: DistributedTask, node: WorkerNode):
        """Запускает задачу на узле в отдельном потоке."""
        with self._lock:
            if not node.available:
                task.status = 'queued'
                threading.Thread(target=self._wait_and_run, args=(task,), daemon=True).start()
                return
            node.active_tasks.append(task.task_id)
            node.status = NodeStatus.BUSY
            task.node_id = node.node_id
            task.status = 'running'
            task.started_at = time.time()

        def _run():
            try:
                result = task.func(*task.args, **task.kwargs)
                task.result = result
                task.status = 'done'
                node.completed += 1
            except Exception as e:  # pylint: disable=broad-except
                task.error = str(e)
                task.status = 'failed'
                node.failed += 1
                self._log(f"Задача [{task.task_id}] упала на узле [{node.node_id}]: {e}")
            finally:
                task.finished_at = time.time()
                with self._lock:
                    if task.task_id in node.active_tasks:
                        node.active_tasks.remove(task.task_id)
                    if node.status != NodeStatus.OFFLINE and not node.active_tasks:
                        node.status = NodeStatus.IDLE

        threading.Thread(target=_run, daemon=True).start()
        self._log(f"Задача [{task.task_id}] → узел [{node.node_id}]")

    def _wait_and_run(self, task: DistributedTask, poll: float = 0.2,
                      timeout: float = 120.0):
        """Ждёт свободного узла и запускает задачу."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            node = self._pick_node()
            if node:
                self._dispatch(task, node)
                return
            time.sleep(poll)
        task.status = 'failed'
        task.error = 'Таймаут ожидания свободного узла'

    def _log(self, message: str):
        if self.monitoring:
            self.monitoring.info(message, source='distributed_execution')
        else:
            print(f"[DistributedExecution] {message}")
