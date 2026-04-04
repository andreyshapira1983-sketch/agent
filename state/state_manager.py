# State & Session Management (управление состоянием) — Слой 23
# Архитектура автономного AI-агента
# Хранение контекста сессии, checkpoint/resume, идемпотентность, восстановление после сбоев.
# Формальная state machine с валидацией переходов (formal_contracts_spec §11).
# pylint: disable=broad-except


import os
import time
import json
import copy
import threading
from enum import Enum
from typing import Callable


class SessionStatus(Enum):
    ACTIVE    = 'active'
    PAUSED    = 'paused'
    COMPLETED = 'completed'
    FAILED    = 'failed'


class TaskState(Enum):
    """Формальные статусы задачи (formal_contracts_spec §11)."""
    CREATED            = 'created'
    PLANNED            = 'planned'
    AWAITING_APPROVAL  = 'awaiting_approval'
    RUNNING            = 'running'
    BLOCKED            = 'blocked'
    FAILED             = 'failed'
    COMPLETED          = 'completed'
    CANCELLED          = 'cancelled'


class InvalidTransitionError(Exception):
    """Попытка недопустимого перехода в state machine."""


# ── Формальная таблица допустимых переходов ───────────────────────────────────
# Ключ: текущее состояние → множество допустимых целевых состояний.
# Согласно formal_contracts_spec §11:
#   - created → completed запрещён
#   - awaiting_approval → running без approval запрещён
#   - failed → completed без explicit recovery запрещён
_VALID_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.CREATED: {
        TaskState.PLANNED,
        TaskState.CANCELLED,
        TaskState.BLOCKED,
        TaskState.FAILED,
        # created → completed ЗАПРЕЩЁН (spec §11)
    },
    TaskState.PLANNED: {
        TaskState.AWAITING_APPROVAL,
        TaskState.RUNNING,
        TaskState.BLOCKED,
        TaskState.CANCELLED,
        TaskState.FAILED,
    },
    TaskState.AWAITING_APPROVAL: {
        TaskState.RUNNING,     # только с valid approval (проверяется отдельно)
        TaskState.CANCELLED,
        TaskState.BLOCKED,
        TaskState.FAILED,
    },
    TaskState.RUNNING: {
        TaskState.COMPLETED,
        TaskState.FAILED,
        TaskState.BLOCKED,
        TaskState.CANCELLED,
    },
    TaskState.BLOCKED: {
        TaskState.RUNNING,
        TaskState.PLANNED,     # replanning после разблокировки
        TaskState.CANCELLED,
        TaskState.FAILED,
    },
    TaskState.FAILED: {
        # failed → completed запрещён без recovery (spec §11)
        # failed → running через explicit recovery path
        TaskState.RUNNING,     # recovery path
        TaskState.PLANNED,     # replan recovery path
        TaskState.CANCELLED,
    },
    TaskState.COMPLETED: set(),   # терминальное состояние — переходов нет
    TaskState.CANCELLED: set(),    # терминальное состояние — переходов нет
}


class TaskStateMachine:
    """Формальная state machine для задачи с валидацией переходов.

    Отвечает за:
    - отслеживание текущего состояния задачи
    - блокировку невалидных переходов (InvalidTransitionError)
    - иммутабельный лог всех переходов (transition_history)
    - thread-safe мутации через lock
    """

    def __init__(self, task_id: str, initial_state: TaskState = TaskState.CREATED):
        self.task_id = task_id
        self._state = initial_state
        self._lock = threading.Lock()
        self._history: list[dict] = [{
            'from': None,
            'to': initial_state.value,
            'ts': time.time(),
            'reason': 'init',
        }]

    @property
    def state(self) -> TaskState:
        return self._state

    def transition(self, target: TaskState, reason: str = '',
                   approval_valid: bool = False) -> TaskState:
        """Выполняет переход в целевое состояние.

        Args:
            target          — целевое состояние
            reason          — причина перехода (для аудита)
            approval_valid  — есть ли валидный approval token
                              (требуется для awaiting_approval → running)

        Raises:
            InvalidTransitionError — если переход не допустим

        Returns:
            Новое состояние.
        """
        with self._lock:
            old = self._state
            allowed = _VALID_TRANSITIONS.get(old, set())

            if target not in allowed:
                raise InvalidTransitionError(
                    f"Переход {old.value} → {target.value} запрещён для task '{self.task_id}'. "
                    f"Допустимые переходы из {old.value}: "
                    f"{', '.join(s.value for s in allowed) or 'нет (терминальное)'}"
                )

            # Специальные проверки по spec §11
            if old == TaskState.AWAITING_APPROVAL and target == TaskState.RUNNING:
                if not approval_valid:
                    raise InvalidTransitionError(
                        f"Переход awaiting_approval → running для task '{self.task_id}' "
                        f"запрещён без valid approval token"
                    )

            self._state = target
            self._history.append({
                'from': old.value,
                'to': target.value,
                'ts': time.time(),
                'reason': reason,
            })
            return target

    @property
    def transition_history(self) -> list[dict]:
        """Иммутабельная копия истории переходов."""
        return list(self._history)

    def can_transition(self, target: TaskState) -> bool:
        """Проверяет допустимость перехода без выполнения."""
        allowed = _VALID_TRANSITIONS.get(self._state, set())
        return target in allowed

    def to_dict(self) -> dict:
        return {
            'task_id': self.task_id,
            'current_state': self._state.value,
            'history': list(self._history),
        }


class Checkpoint:
    """Снимок состояния в определённый момент."""

    def __init__(self, checkpoint_id: str, session_id: str, step: int, data: dict):
        self.checkpoint_id = checkpoint_id
        self.session_id = session_id
        self.step = step
        self.data = copy.deepcopy(data)
        self.created_at = time.time()

    def to_dict(self):
        return {
            'checkpoint_id': self.checkpoint_id,
            'session_id': self.session_id,
            'step': self.step,
            'data': self.data,
            'created_at': self.created_at,
        }


class Session:
    """Одна рабочая сессия агента."""

    def __init__(self, session_id: str, goal: str | None = None, metadata: dict | None = None):
        self.session_id = session_id
        self.goal = goal
        self.metadata = metadata or {}
        self.status = SessionStatus.ACTIVE
        self.created_at = time.time()
        self.updated_at = time.time()
        self.step = 0
        self.context: dict = {}           # текущий контекст
        self.checkpoints: list[Checkpoint] = []
        self.history: list[dict] = []     # лог всех шагов
        self._step_lock = threading.Lock()  # защита от concurrent mutation

    def advance(self, step_data: dict | None = None):
        """Переходит к следующему шагу (thread-safe)."""
        with self._step_lock:
            self.step += 1
            self.updated_at = time.time()
            entry = {'step': self.step, 'timestamp': self.updated_at, 'data': step_data or {}}
            self.history.append(entry)
            if step_data:
                self.context.update(step_data)

    def to_dict(self):
        return {
            'session_id': self.session_id,
            'goal': self.goal,
            'status': self.status.value,
            'step': self.step,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'context': self.context,
            'metadata': self.metadata,
            'checkpoints': [c.checkpoint_id for c in self.checkpoints],
        }


class StateManager:
    """
    State & Session Management — Слой 23.

    Функции:
        - создание и управление сессиями агента
        - хранение контекста текущей задачи
        - checkpoint: сохранение снимка состояния
        - resume: возобновление с checkpoint после паузы/сбоя
        - идемпотентность шагов: защита от двойного выполнения
        - персистентность: сохранение/загрузка состояния в файл

    Используется:
        - Autonomous Loop (Слой 20)  — управление состоянием цикла
        - Orchestration (Слой 18)    — контекст задач
        - Reliability (Слой 19)      — восстановление после сбоев
        - Monitoring (Слой 17)       — логирование переходов
    """

    def __init__(self, persistence_path: str | None = None, monitoring=None):
        """
        Args:
            persistence_path — путь к файлу для сохранения состояния (None = только в памяти)
            monitoring       — Monitoring (Слой 17)
        """
        self.persistence_path = persistence_path
        self.monitoring = monitoring

        self._sessions: dict[str, Session] = {}
        self._active_session_id: str | None = None
        self._completed_steps: set[str] = set()   # для идемпотентности
        self._completed_steps_lock = threading.Lock()  # concurrent protection
        self._session_lock = threading.Lock()

        # Task state machines: task_id → TaskStateMachine
        self._task_machines: dict[str, TaskStateMachine] = {}
        self._task_machines_lock = threading.Lock()

        if persistence_path:
            self._load_from_disk()

    # ── Сессии ────────────────────────────────────────────────────────────────

    def create_session(self, goal: str | None = None, metadata: dict | None = None) -> Session:
        """Создаёт новую сессию и делает её активной."""
        import uuid
        session_id = str(uuid.uuid4())[:12]
        session = Session(session_id, goal=goal, metadata=metadata)
        with self._session_lock:
            self._sessions[session_id] = session
            self._active_session_id = session_id
        self._log(f"Сессия [{session_id}] создана. Цель: {goal or '—'}")
        return session

    def get_session(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def get_active(self) -> Session | None:
        with self._session_lock:
            if not self._active_session_id:
                return None
            return self._sessions.get(self._active_session_id)

    def set_active(self, session_id: str):
        """Переключает активную сессию."""
        with self._session_lock:
            if session_id not in self._sessions:
                raise KeyError(f"Сессия '{session_id}' не найдена")
            self._active_session_id = session_id
        self._log(f"Активная сессия переключена на [{session_id}]")

    def close_session(self, session_id: str | None = None, success: bool = True):
        """Закрывает сессию."""
        with self._session_lock:
            sid = session_id or self._active_session_id
            if not sid:
                return
            session = self._sessions.get(sid)
            if session:
                session.status = SessionStatus.COMPLETED if success else SessionStatus.FAILED
                session.updated_at = time.time()
                self._log(f"Сессия [{sid}] закрыта: {session.status.value}")
            if self._active_session_id == sid:
                self._active_session_id = None

    def list_sessions(self) -> list[dict]:
        return [s.to_dict() for s in self._sessions.values()]

    # ── Контекст ──────────────────────────────────────────────────────────────

    def set_context(self, key: str, value, session_id: str | None = None):
        """Устанавливает значение в контексте активной (или указанной) сессии."""
        session = self._get_session_or_active(session_id)
        session.context[key] = value
        session.updated_at = time.time()

    def get_context(self, key: str | None = None, session_id: str | None = None):
        """Возвращает значение из контекста. Без key — весь контекст."""
        session = self._get_session_or_active(session_id)
        if key is None:
            return session.context
        return session.context.get(key)

    def update_context(self, data: dict, session_id: str | None = None):
        """Обновляет контекст несколькими ключами сразу."""
        session = self._get_session_or_active(session_id)
        session.context.update(data)
        session.updated_at = time.time()

    def advance_step(self, step_data: dict | None = None, session_id: str | None = None):
        """Продвигает сессию на один шаг."""
        session = self._get_session_or_active(session_id)
        session.advance(step_data)
        self._log(f"Сессия [{session.session_id}] → шаг {session.step}")

    # ── Checkpoint / Resume ───────────────────────────────────────────────────

    def checkpoint(self, label: str | None = None, session_id: str | None = None) -> Checkpoint:
        """Сохраняет снимок текущего состояния сессии."""
        session = self._get_session_or_active(session_id)
        import uuid
        cp_id = label or f"cp_{session.step}_{str(uuid.uuid4())[:4]}"
        cp = Checkpoint(cp_id, session.session_id, session.step, session.context)
        session.checkpoints.append(cp)
        self._log(f"Checkpoint [{cp_id}] сохранён на шаге {session.step}")

        if self.persistence_path:
            self._save_to_disk()

        return cp

    def resume_from(self, checkpoint_id: str, session_id: str | None = None) -> Session:
        """Восстанавливает состояние сессии из checkpoint."""
        session = self._get_session_or_active(session_id)
        cp = next((c for c in session.checkpoints if c.checkpoint_id == checkpoint_id), None)
        if not cp:
            raise KeyError(f"Checkpoint '{checkpoint_id}' не найден в сессии '{session.session_id}'")

        session.context = copy.deepcopy(cp.data)
        session.step = cp.step
        session.status = SessionStatus.ACTIVE
        session.updated_at = time.time()
        self._log(f"Сессия [{session.session_id}] восстановлена из checkpoint [{checkpoint_id}], шаг {cp.step}")
        return session

    def get_latest_checkpoint(self, session_id: str | None = None) -> Checkpoint | None:
        """Возвращает последний checkpoint сессии."""
        session = self._get_session_or_active(session_id)
        return session.checkpoints[-1] if session.checkpoints else None

    # ── Идемпотентность ───────────────────────────────────────────────────────

    def mark_done(self, step_key: str):
        """Помечает шаг как выполненный (защита от двойного запуска). Thread-safe."""
        with self._completed_steps_lock:
            self._completed_steps.add(step_key)

    def is_done(self, step_key: str) -> bool:
        """Проверяет, был ли шаг уже выполнен. Thread-safe."""
        with self._completed_steps_lock:
            return step_key in self._completed_steps

    def run_once(self, step_key: str, func: Callable, *args, **kwargs):
        """Выполняет func только если шаг ещё не был выполнен. Thread-safe."""
        with self._completed_steps_lock:
            if step_key in self._completed_steps:
                self._log(f"Шаг '{step_key}' уже выполнен — пропуск (идемпотентность)")
                return None
            # помечаем ДО выполнения (optimistic lock — предотвращает двойной запуск)
            self._completed_steps.add(step_key)
        try:
            return func(*args, **kwargs)
        except Exception:
            # откатываем пометку при ошибке
            with self._completed_steps_lock:
                self._completed_steps.discard(step_key)
            raise

    # ── Task State Machine ────────────────────────────────────────────────────

    def create_task_machine(self, task_id: str,
                            initial_state: TaskState = TaskState.CREATED) -> TaskStateMachine:
        """Создаёт state machine для задачи."""
        with self._task_machines_lock:
            if task_id in self._task_machines:
                return self._task_machines[task_id]
            machine = TaskStateMachine(task_id, initial_state)
            self._task_machines[task_id] = machine
            self._log(f"TaskStateMachine [{task_id}] создана в состоянии {initial_state.value}")
            return machine

    def get_task_machine(self, task_id: str) -> TaskStateMachine | None:
        """Возвращает state machine задачи или None."""
        return self._task_machines.get(task_id)

    def transition_task(self, task_id: str, target: TaskState,
                        reason: str = '', approval_valid: bool = False) -> TaskState:
        """Выполняет переход состояния задачи. Создаёт machine если не существует.

        Raises:
            InvalidTransitionError — если переход не допустим.
        """
        with self._task_machines_lock:
            machine = self._task_machines.get(task_id)
            if not machine:
                machine = TaskStateMachine(task_id)
                self._task_machines[task_id] = machine
        new_state = machine.transition(target, reason=reason,
                                       approval_valid=approval_valid)
        self._log(
            f"Task [{task_id}] → {new_state.value}"
            f"{f' ({reason})' if reason else ''}"
        )
        return new_state

    def get_task_state(self, task_id: str) -> TaskState | None:
        """Текущее состояние задачи."""
        machine = self._task_machines.get(task_id)
        return machine.state if machine else None

    def list_task_machines(self) -> list[dict]:
        """Возвращает состояние всех task machines."""
        return [m.to_dict() for m in self._task_machines.values()]

    # ── Персистентность ───────────────────────────────────────────────────────

    def save(self, path: str | None = None):
        """Сохраняет все сессии в JSON-файл."""
        self._save_to_disk(path)

    def load(self, path: str | None = None):
        """Загружает сессии из JSON-файла."""
        self._load_from_disk(path)

    def _save_to_disk(self, path: str | None = None):
        target = path or self.persistence_path
        if not target:
            return
        try:
            data = {
                'sessions': {sid: s.to_dict() for sid, s in self._sessions.items()},
                'active_session_id': self._active_session_id,
                'checkpoints': {
                    sid: [c.to_dict() for c in s.checkpoints]
                    for sid, s in self._sessions.items()
                },
            }
            with open(target, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._log(f"Ошибка сохранения состояния: {e}")

    def _load_from_disk(self, path: str | None = None):
        target = path or self.persistence_path
        if not target:
            return
        try:
            with open(target, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for sid, sdict in data.get('sessions', {}).items():
                session = Session(sid, goal=sdict.get('goal'), metadata=sdict.get('metadata', {}))
                session.status = SessionStatus(sdict.get('status', 'active'))
                session.step = sdict.get('step', 0)
                session.context = sdict.get('context', {})
                self._sessions[sid] = session
            # Восстанавливаем checkpoints
            for sid, cps in data.get('checkpoints', {}).items():
                session = self._sessions.get(sid)
                if session:
                    for cpd in cps:
                        cp = Checkpoint(cpd['checkpoint_id'], sid, cpd['step'], cpd['data'])
                        cp.created_at = cpd.get('created_at', time.time())
                        session.checkpoints.append(cp)
            self._active_session_id = data.get('active_session_id')
            self._log(f"Состояние загружено из '{target}': {len(self._sessions)} сессий")
        except FileNotFoundError:
            pass
        except Exception as e:
            self._log(f"Ошибка загрузки состояния: {e}")

    # ── Checkpoint / File-based persistence for AutonomousLoop ───────────────

    def save_checkpoint(self, working_dir: str, session_id: str, goal=None,
                        cycle_count: int = 0, consecutive_failures: int = 0,
                        learning_quality=None):
        """
        Serializes key loop state to:
            {working_dir}/.agent_state/checkpoint_{session_id}.json
        """
        state_dir = os.path.join(working_dir, '.agent_state')
        os.makedirs(state_dir, exist_ok=True)
        path = os.path.join(state_dir, f'checkpoint_{session_id}.json')
        payload = {
            'session_id': session_id,
            'goal': goal,
            'cycle_count': cycle_count,
            'consecutive_failures': consecutive_failures,
            'learning_quality': learning_quality.to_dict() if (
                learning_quality and hasattr(learning_quality, 'to_dict')
            ) else (learning_quality or {}),
            'saved_at': time.time(),
        }
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            self._log(f"Checkpoint saved: {path}")
        except Exception as e:
            self._log(f"save_checkpoint error: {e}")

    def restore_checkpoint(self, working_dir: str, session_id: str) -> dict:
        """
        Loads checkpoint from:
            {working_dir}/.agent_state/checkpoint_{session_id}.json
        Returns the state dict, or empty dict if not found.
        """
        path = os.path.join(working_dir, '.agent_state',
                            f'checkpoint_{session_id}.json')
        try:
            with open(path, 'r', encoding='utf-8') as f:
                state = json.load(f)
            self._log(f"Checkpoint restored: {path}")
            return state
        except FileNotFoundError:
            self._log(f"No checkpoint found at {path}")
            return {}
        except Exception as e:
            self._log(f"restore_checkpoint error: {e}")
            return {}

    def auto_checkpoint(self, loop_instance):
        """
        Saves current loop state every 10 cycles.
        Called with the AutonomousLoop instance.
        """
        if loop_instance._cycle_count % 10 != 0:  # pylint: disable=protected-access
            return
        working_dir = getattr(loop_instance, '_working_dir', None) or '.'
        # Derive a session_id from the loop object identity
        session_id = getattr(loop_instance, '_session_id',
                             str(id(loop_instance)))
        self.save_checkpoint(
            working_dir=working_dir,
            session_id=session_id,
            goal=getattr(loop_instance, '_goal', None),
            cycle_count=getattr(loop_instance, '_cycle_count', 0),
            consecutive_failures=getattr(loop_instance,
                                         '_consecutive_failures', 0),
            learning_quality=getattr(loop_instance, 'learning_quality', None),
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_session_or_active(self, session_id: str | None = None) -> Session:
        if session_id:
            s = self._sessions.get(session_id)
            if not s:
                raise KeyError(f"Сессия '{session_id}' не найдена")
            return s
        if not self._active_session_id:
            return self.create_session()
        return self._sessions[self._active_session_id]

    def _log(self, message: str):
        if self.monitoring:
            self.monitoring.info(message, source='state_manager')
        else:
            print(f"[StateManager] {message}")
