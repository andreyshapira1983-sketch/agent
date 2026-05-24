"""
brain/state/task_store.py — Persistent Task Session Store (S_State)

Сохраняет активные задачи агента в SQLite чтобы пережить перезапуск.

Концепции:
    TaskSession  — одна "работа" агента: от получения задания до его завершения.
                   Несёт: task_id, session_id (чат), user_id, статус, цель,
                   ссылку на план (plan_job_id), снимок контекста (context_snapshot).

    TaskStatus   — конечный автомат:
                     PENDING → RUNNING → COMPLETED / FAILED / CANCELLED
                                       ↘ PAUSED → RUNNING (resume)
                                       ↘ RECOVERING → RUNNING (после краша)

Что сохраняется:
    - goal          — что нужно сделать
    - plan_job_id   — ключ в PlanCheckpointStore (план с шагами)
    - context_json  — снимок контекста (последний известный)
    - status        — где находится задача
    - error         — причина провала (если FAILED)

Что НЕ сохраняется (намеренно):
    - Содержимое asyncio.Queue (неконсистентно при падении)
    - LLM-ответы (восстанавливаются из памяти)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Enums
# ------------------------------------------------------------------

class TaskStatus(str, Enum):
    PENDING    = "pending"      # создана, ещё не начата
    RUNNING    = "running"      # активно выполняется
    PAUSED     = "paused"       # явно приостановлена (пользователь / policy)
    RECOVERING = "recovering"   # обнаружена на старте, восстанавливается
    COMPLETED  = "completed"    # успешно завершена
    FAILED     = "failed"       # завершена с ошибкой
    CANCELLED  = "cancelled"    # отменена пользователем

    # Терминальные состояния — задача не изменится
    @classmethod
    def terminal(cls) -> frozenset["TaskStatus"]:
        return frozenset({cls.COMPLETED, cls.FAILED, cls.CANCELLED})

    def is_terminal(self) -> bool:
        return self in self.terminal()

    # Состояния, из которых можно возобновить
    @classmethod
    def resumable(cls) -> frozenset["TaskStatus"]:
        return frozenset({cls.RUNNING, cls.PAUSED, cls.RECOVERING})


# ------------------------------------------------------------------
# Dataclass
# ------------------------------------------------------------------

@dataclass
class TaskSession:
    """
    Одна рабочая сессия агента.

    task_id      — уникальный ID задачи (UUID hex)
    session_id   — ID чата/сессии пользователя (e.g. "telegram:12345")
    user_id      — человек-заказчик (e.g. "telegram_user:67890")
    goal         — текстовое описание цели
    status       — текущий статус (TaskStatus)
    plan_job_id  — ссылка на PlanCheckpointStore (может быть None до создания плана)
    context_json — сериализованный снимок контекста (dict → JSON)
    error        — сообщение об ошибке при FAILED
    created_at   — UTC timestamp создания
    updated_at   — UTC timestamp последнего изменения
    completed_at — UTC timestamp завершения (None если не завершена)
    """
    task_id:      str
    session_id:   str
    user_id:      str
    goal:         str
    status:       TaskStatus = TaskStatus.PENDING
    plan_job_id:  str | None = None
    context_json: str | None = None    # JSON blob
    error:        str | None = None
    created_at:   datetime = field(default_factory=datetime.utcnow)
    updated_at:   datetime = field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None

    @classmethod
    def new(
        cls,
        session_id: str,
        user_id: str,
        goal: str,
        *,
        plan_job_id: str | None = None,
    ) -> "TaskSession":
        now = datetime.utcnow()
        return cls(
            task_id=uuid.uuid4().hex,
            session_id=session_id,
            user_id=user_id,
            goal=goal,
            status=TaskStatus.PENDING,
            plan_job_id=plan_job_id,
            created_at=now,
            updated_at=now,
        )

    def snapshot_context(self, context: dict) -> None:
        """Сохраняет снимок контекста как JSON."""
        try:
            self.context_json = json.dumps(context, ensure_ascii=False, default=str)
        except (TypeError, ValueError) as exc:
            logger.warning("[TaskSession] context snapshot failed: %s", exc)

    def get_context(self) -> dict:
        """Восстанавливает контекст из JSON. Возвращает {} при ошибке."""
        if not self.context_json:
            return {}
        try:
            return json.loads(self.context_json)
        except json.JSONDecodeError:
            return {}

    def to_dict(self) -> dict:
        return {
            "task_id":      self.task_id,
            "session_id":   self.session_id,
            "user_id":      self.user_id,
            "goal":         self.goal,
            "status":       self.status.value,
            "plan_job_id":  self.plan_job_id,
            "error":        self.error,
            "created_at":   self.created_at.isoformat(),
            "updated_at":   self.updated_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


# ------------------------------------------------------------------
# TaskStore
# ------------------------------------------------------------------

class TaskStore:
    """
    SQLite-backed хранилище TaskSession.

    Thread-safe через check_same_thread=False + WAL mode.
    Один файл: data/tasks.db

    Основной API:
        create(session_id, user_id, goal)       → TaskSession
        update_status(task_id, status, ...)     → None
        set_plan(task_id, plan_job_id)          → None
        save_context(task_id, context)          → None
        get(task_id)                            → TaskSession | None
        list_active()                           → list[TaskSession]  ← ключевой для recovery
        list_by_session(session_id)             → list[TaskSession]
        complete(task_id)                       → None
        fail(task_id, error)                    → None
        cancel(task_id)                         → None
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._path),
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        # WAL позволяет читать и писать одновременно
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()
        logger.info("[TaskStore] opened: %s", self._path)

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id      TEXT PRIMARY KEY,
                session_id   TEXT NOT NULL,
                user_id      TEXT NOT NULL,
                goal         TEXT NOT NULL,
                status       TEXT NOT NULL DEFAULT 'pending',
                plan_job_id  TEXT,
                context_json TEXT,
                error        TEXT,
                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_task_session  ON tasks(session_id);
            CREATE INDEX IF NOT EXISTS idx_task_status   ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_task_updated  ON tasks(updated_at);
        """)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create(
        self,
        session_id: str,
        user_id: str,
        goal: str,
        *,
        plan_job_id: str | None = None,
    ) -> TaskSession:
        task = TaskSession.new(
            session_id=session_id,
            user_id=user_id,
            goal=goal,
            plan_job_id=plan_job_id,
        )
        now = task.created_at.isoformat()
        self._conn.execute(
            """
            INSERT INTO tasks
                (task_id, session_id, user_id, goal, status,
                 plan_job_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (task.task_id, task.session_id, task.user_id, task.goal,
             task.status.value, task.plan_job_id, now, now),
        )
        self._conn.commit()
        logger.info("[TaskStore] created task_id=%s session=%s", task.task_id, session_id)
        return task

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        error: str | None = None,
    ) -> None:
        now = datetime.utcnow().isoformat()
        completed_at: str | None = None
        if TaskStatus(status).is_terminal():
            completed_at = now

        self._conn.execute(
            """
            UPDATE tasks
            SET status=?, error=?, updated_at=?, completed_at=?
            WHERE task_id=?
            """,
            (status.value if isinstance(status, TaskStatus) else status,
             error, now, completed_at, task_id),
        )
        self._conn.commit()

    def set_plan(self, task_id: str, plan_job_id: str) -> None:
        self._conn.execute(
            "UPDATE tasks SET plan_job_id=?, updated_at=? WHERE task_id=?",
            (plan_job_id, datetime.utcnow().isoformat(), task_id),
        )
        self._conn.commit()

    def save_context(self, task_id: str, context: dict[str, Any]) -> None:
        try:
            blob = json.dumps(context, ensure_ascii=False, default=str)
        except (TypeError, ValueError) as exc:
            logger.warning("[TaskStore] context serialization error: %s", exc)
            return
        self._conn.execute(
            "UPDATE tasks SET context_json=?, updated_at=? WHERE task_id=?",
            (blob, datetime.utcnow().isoformat(), task_id),
        )
        self._conn.commit()

    def complete(self, task_id: str) -> None:
        self.update_status(task_id, TaskStatus.COMPLETED)
        logger.info("[TaskStore] completed task_id=%s", task_id)

    def fail(self, task_id: str, error: str) -> None:
        self.update_status(task_id, TaskStatus.FAILED, error=error[:2000])
        logger.warning("[TaskStore] failed task_id=%s error=%s", task_id, error[:120])

    def cancel(self, task_id: str) -> None:
        self.update_status(task_id, TaskStatus.CANCELLED)

    def mark_recovering(self, task_id: str) -> None:
        """Помечает задачу как восстанавливаемую (переходное состояние)."""
        self.update_status(task_id, TaskStatus.RECOVERING)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get(self, task_id: str) -> TaskSession | None:
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE task_id=?", (task_id,)
        ).fetchone()
        return self._row_to_session(row) if row else None

    def list_active(self) -> list[TaskSession]:
        """
        Возвращает все незавершённые задачи.
        Ключевой метод для recovery при старте.
        """
        rows = self._conn.execute(
            """
            SELECT * FROM tasks
            WHERE status NOT IN ('completed', 'failed', 'cancelled')
            ORDER BY updated_at DESC
            """
        ).fetchall()
        return [self._row_to_session(r) for r in rows]

    def list_by_session(self, session_id: str, *, limit: int = 20) -> list[TaskSession]:
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE session_id=? ORDER BY created_at DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [self._row_to_session(r) for r in rows]

    def list_failed(self, *, limit: int = 50) -> list[TaskSession]:
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE status='failed' ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_session(r) for r in rows]

    def count(self, *, status: TaskStatus | None = None) -> int:
        if status is None:
            return self._conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        return self._conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status=?", (status.value,)
        ).fetchone()[0]

    def stats(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status"
        ).fetchall()
        return {r["status"]: r["cnt"] for r in rows}

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> TaskSession:
        def _dt(s: str | None) -> datetime | None:
            if not s:
                return None
            try:
                return datetime.fromisoformat(s)
            except ValueError:
                return None

        return TaskSession(
            task_id=row["task_id"],
            session_id=row["session_id"],
            user_id=row["user_id"],
            goal=row["goal"],
            status=TaskStatus(row["status"]),
            plan_job_id=row["plan_job_id"],
            context_json=row["context_json"],
            error=row["error"],
            created_at=_dt(row["created_at"]) or datetime.utcnow(),
            updated_at=_dt(row["updated_at"]) or datetime.utcnow(),
            completed_at=_dt(row["completed_at"]),
        )

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass
