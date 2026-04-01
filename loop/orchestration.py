# Orchestration System (система оркестрации) — Слой 18
# Архитектура автономного AI-агента
# Распределение задач, управление агентами, очереди, параллельное выполнение.


import uuid
import threading
from enum import Enum
from collections import deque
from typing import Any


class TaskPriority(Enum):
    LOW    = 1
    NORMAL = 2
    HIGH   = 3
    URGENT = 4


class OrchestratedTask:
    """Единица работы в системе оркестрации."""

    def __init__(self, goal: str, role: str | None = None, priority: TaskPriority = TaskPriority.NORMAL,
                 depends_on: list | None = None, metadata: dict | None = None):
        self.task_id = str(uuid.uuid4())[:8]
        self.goal = goal
        self.role = role                          # какой агент должен взять задачу
        self.priority = priority
        self.depends_on = depends_on or []        # task_id зависимостей
        self.metadata = metadata or {}
        self.status = 'pending'                   # pending | running | done | failed | partial | blocked | timeout | skipped | non_actionable
        self.result: Any = None
        self.error: str | None = None
        self.attempts = 0

    def to_dict(self):
        return {
            'task_id': self.task_id,
            'goal': self.goal,
            'role': self.role,
            'priority': self.priority.value,
            'depends_on': self.depends_on,
            'status': self.status,
            'result': self.result,
            'error': self.error,
            'attempts': self.attempts,
        }


class OrchestrationSystem:
    """
    Orchestration System — Слой 18.

    Функции:
        - приём и приоритизация задач
        - управление очередью (FIFO с приоритетами)
        - делегирование задач Agent System
        - параллельное выполнение независимых задач
        - отслеживание зависимостей между задачами
        - мониторинг хода выполнения

    Используется:
        - Autonomous Loop (Слой 20)  — подача задач из цикла
        - Agent System (Слой 4)      — исполнитель задач
        - Monitoring (Слой 17)       — логирование
    """

    def __init__(self, agent_system=None, monitoring=None, max_workers: int = 4):
        """
        Args:
            agent_system — ManagerAgent (Слой 4)
            monitoring   — Monitoring (Слой 17)
            max_workers  — максимальное кол-во параллельных потоков
        """
        self.agent_system = agent_system
        self.monitoring = monitoring
        self.max_workers = max_workers

        self._queue: deque[OrchestratedTask] = deque()
        self._tasks: dict[str, OrchestratedTask] = {}    # все задачи по ID
        self._lock = threading.Lock()
        self._active_threads: list[threading.Thread] = []

    # ── Подача задач ──────────────────────────────────────────────────────────

    def submit(
        self,
        goal: str,
        role: str | None = None,
        priority: TaskPriority = TaskPriority.NORMAL,
        depends_on: list | None = None,
        metadata: dict | None = None,
    ) -> OrchestratedTask:
        """
        Добавляет задачу в очередь.

        Returns:
            OrchestratedTask с task_id для последующего отслеживания.
        """
        task = OrchestratedTask(goal, role=role, priority=priority,
                                depends_on=depends_on, metadata=metadata)
        with self._lock:
            self._tasks[task.task_id] = task
            self._enqueue(task)

        self._log(f"Задача [{task.task_id}] принята: '{goal[:60]}' (приоритет: {priority.name})")
        return task

    def submit_batch(self, tasks: list[dict]) -> list[OrchestratedTask]:
        """
        Подаёт несколько задач сразу.
        Каждый элемент — dict с полями: goal, role, priority, depends_on, metadata.
        """
        return [self.submit(**t) for t in tasks]

    # ── Выполнение ────────────────────────────────────────────────────────────

    def run_next(self) -> OrchestratedTask | None:
        """Берёт следующую готовую задачу из очереди и выполняет синхронно."""
        task = self._dequeue()
        if not task:
            return None
        self._execute(task)
        return task

    def run_all(self) -> list[OrchestratedTask]:
        """Выполняет все задачи в очереди последовательно."""
        results = []
        while self._queue:
            task = self.run_next()
            if task:
                results.append(task)
        return results

    def run_parallel(self) -> list[OrchestratedTask]:
        """
        Запускает независимые задачи параллельно (до max_workers потоков).
        Задачи с незавершёнными зависимостями ждут.
        """
        completed = []
        while self._queue:
            ready = self._get_ready_tasks()
            if not ready:
                break

            batch = ready[:self.max_workers]
            threads = []
            for task in batch:
                t = threading.Thread(target=self._execute, args=(task,), daemon=True)
                threads.append(t)
                t.start()

            for t in threads:
                t.join()

            completed.extend(batch)

        return completed

    # ── Статус и отслеживание ─────────────────────────────────────────────────

    def get_task(self, task_id: str) -> OrchestratedTask | None:
        return self._tasks.get(task_id)

    def get_all(self) -> list[dict]:
        return [t.to_dict() for t in self._tasks.values()]

    def get_pending(self) -> list[dict]:
        return [t.to_dict() for t in self._tasks.values() if t.status == 'pending']

    def get_done(self) -> list[dict]:
        return [t.to_dict() for t in self._tasks.values() if t.status == 'done']

    def get_failed(self) -> list[dict]:
        return [t.to_dict() for t in self._tasks.values() if t.status == 'failed']

    def queue_size(self) -> int:
        return len(self._queue)

    def summary(self) -> dict:
        all_tasks = list(self._tasks.values())
        return {
            'total': len(all_tasks),
            'pending': sum(1 for t in all_tasks if t.status == 'pending'),
            'running': sum(1 for t in all_tasks if t.status == 'running'),
            'done': sum(1 for t in all_tasks if t.status == 'done'),
            'failed': sum(1 for t in all_tasks if t.status == 'failed'),
            'queue_size': self.queue_size(),
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _execute(self, task: OrchestratedTask):  # pylint: disable=broad-except
        task.attempts += 1
        task.status = 'running'
        self._log(f"Выполнение [{task.task_id}]: '{task.goal[:60]}'")
        try:
            if self.agent_system:
                result = self.agent_system.handle({
                    'goal': task.goal,
                    'role': task.role,
                    **task.metadata,
                })
                task.result = result
                # Читаем реальный статус из результата, не назначаем 'done' вслепую
                res_status = result.get('status', '')
                if res_status in ('blocked', 'non_actionable', 'skipped', 'partial',
                                  'failed', 'timeout'):
                    task.status = res_status
                elif not result.get('success', True):
                    task.status = 'failed'
                else:
                    task.status = 'done'
                self._log(f"Готово [{task.task_id}]: статус={task.status}")
            else:
                task.result = {'warning': 'agent_system не подключён'}
                task.status = 'done'
        except Exception as e:  # pylint: disable=broad-except
            task.status = 'failed'
            task.error = str(e)
            self._log(f"Ошибка [{task.task_id}]: {e}")
            max_retries = int(task.metadata.get('max_retries', 1) or 0)
            if task.attempts <= max_retries:
                task.status = 'pending'
                with self._lock:
                    self._enqueue(task)
                self._log(
                    f"Ретрай [{task.task_id}] попытка {task.attempts}/{max_retries + 1}"
                )

    def _enqueue(self, task: OrchestratedTask):
        """Вставляет задачу в очередь с учётом приоритета."""
        # Стабильная вставка: более высокий приоритет идёт раньше,
        # при равном приоритете сохраняем FIFO.
        if not self._queue:
            self._queue.append(task)
            return

        items = list(self._queue)
        insert_idx = len(items)
        for i, queued in enumerate(items):
            if queued.priority.value < task.priority.value:
                insert_idx = i
                break
        items.insert(insert_idx, task)
        self._queue = deque(items)

    def _dequeue(self) -> OrchestratedTask | None:
        """Берёт следующую задачу у которой выполнены все зависимости."""
        best_idx = None
        best_prio = -1
        for idx, task in enumerate(self._queue):
            if not self._dependencies_met(task):
                continue
            prio = task.priority.value
            if prio > best_prio:
                best_prio = prio
                best_idx = idx
        if best_idx is None:
            return None
        items = list(self._queue)
        task = items.pop(best_idx)
        self._queue = deque(items)
        return task

    def _get_ready_tasks(self) -> list[OrchestratedTask]:
        """Возвращает задачи с выполненными зависимостями."""
        ready = []
        remaining = deque()
        while self._queue:
            task = self._queue.popleft()
            if self._dependencies_met(task):
                ready.append(task)
            else:
                remaining.append(task)
        self._queue = remaining
        return ready

    def _dependencies_met(self, task: OrchestratedTask) -> bool:
        """Проверяет что все зависимые задачи завершены успешно."""
        for dep_id in task.depends_on:
            dep = self._tasks.get(dep_id)
            if not dep or dep.status != 'done':
                return False
        return True

    def _log(self, message: str):
        if self.monitoring:
            self.monitoring.info(message, source='orchestration')
        else:
            print(f"[Orchestration] {message}")
