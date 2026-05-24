"""
brain/state/idempotency.py — Идемпотентность вызовов инструментов

Проблема:
    Агент выполняет Step 3 → вызывает email_tool(send) → процесс падает.
    При восстановлении: план показывает Step 3 как RUNNING → агент вызывает
    email_tool(send) снова → дублирующее письмо уходит клиенту.

Решение:
    Перед каждым вызовом инструмента — проверяем IdempotencyStore.
    Если такой вызов (task_id + step_id + tool_name + params) уже выполнялся
    и результат сохранён — возвращаем кэшированный результат.

Ключ идемпотентности:
    sha256(task_id + ":" + step_id + ":" + tool_name + ":" + sorted_params_json)
    Детерминирован, не зависит от порядка kwargs.

TTL:
    По умолчанию 24 часа. После истечения запись удаляется и вызов
    будет выполнен заново (безопасно для безопасных операций, опасно для
    деструктивных — но деструктивные требуют Human Approval в любом случае).

Ограничения:
    - Храним только успешные результаты (success=True).
      Неуспешные не кэшируем: их безопасно повторить.
    - Результат обрезается до 64 KB при сериализации.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from tools.base import ToolResult

logger = logging.getLogger(__name__)

_MAX_RESULT_BYTES = 64 * 1024   # 64 KB — лимит хранимого результата
_DEFAULT_TTL_HOURS = 24


# ------------------------------------------------------------------
# Dataclass
# ------------------------------------------------------------------

@dataclass
class CachedCall:
    call_key:    str
    task_id:     str
    step_id:     str
    tool_name:   str
    result_json: str
    executed_at: datetime
    expires_at:  datetime

    def to_tool_result(self) -> ToolResult:
        """Восстанавливает ToolResult из JSON."""
        try:
            data = json.loads(self.result_json)
            return ToolResult(
                tool_name=data.get("tool_name", self.tool_name),
                success=bool(data.get("success", True)),
                output=data.get("output"),
                error=data.get("error"),
                metadata={
                    **data.get("metadata", {}),
                    "_from_cache": True,
                    "_cached_at": self.executed_at.isoformat(),
                },
            )
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning("[Idempotency] result deserialization failed: %s", exc)
            return ToolResult(
                tool_name=self.tool_name,
                success=True,
                output=None,
                error=None,
                metadata={"_from_cache": True, "_deserialize_error": str(exc)},
            )


# ------------------------------------------------------------------
# IdempotencyStore
# ------------------------------------------------------------------

class IdempotencyStore:
    """
    SQLite-backed хранилище идемпотентных вызовов инструментов.

    Использование:
        store = IdempotencyStore("data/idempotency.db")

        # Перед вызовом:
        key = store.make_key(task_id, step_id, tool_name, params)
        cached = store.check(key)
        if cached:
            return cached.to_tool_result()

        # После успешного вызова:
        store.save(key, task_id, step_id, tool_name, result)
    """

    def __init__(self, path: Path | str, *, ttl_hours: int = _DEFAULT_TTL_HOURS) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._ttl = ttl_hours
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()
        logger.info("[Idempotency] opened: %s (ttl=%dh)", self._path, ttl_hours)

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS tool_calls (
                call_key    TEXT PRIMARY KEY,
                task_id     TEXT NOT NULL,
                step_id     TEXT NOT NULL,
                tool_name   TEXT NOT NULL,
                result_json TEXT NOT NULL,
                executed_at TEXT NOT NULL,
                expires_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_idem_task    ON tool_calls(task_id);
            CREATE INDEX IF NOT EXISTS idx_idem_expires ON tool_calls(expires_at);
        """)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Key generation
    # ------------------------------------------------------------------

    @staticmethod
    def make_key(
        task_id: str,
        step_id: str | int,
        tool_name: str,
        params: dict[str, Any] | None = None,
    ) -> str:
        """
        Детерминированный ключ для вызова инструмента.

        Сортируем ключи params чтобы порядок kwargs не влиял на ключ.
        Чувствителен к значениям — разные params = разные ключи.
        """
        params_str = json.dumps(
            params or {},
            sort_keys=True,
            ensure_ascii=True,
            default=str,
        )
        raw = f"{task_id}:{step_id}:{tool_name}:{params_str}"
        return hashlib.sha256(raw.encode()).hexdigest()

    # ------------------------------------------------------------------
    # Check & Save
    # ------------------------------------------------------------------

    def check(self, call_key: str) -> CachedCall | None:
        """
        Проверяет кэш. Возвращает CachedCall если есть действующая запись,
        иначе None. Автоматически удаляет просроченные записи.
        """
        now = datetime.utcnow().isoformat()
        row = self._conn.execute(
            "SELECT * FROM tool_calls WHERE call_key=? AND expires_at > ?",
            (call_key, now),
        ).fetchone()
        if row is None:
            return None

        try:
            return CachedCall(
                call_key=row["call_key"],
                task_id=row["task_id"],
                step_id=row["step_id"],
                tool_name=row["tool_name"],
                result_json=row["result_json"],
                executed_at=datetime.fromisoformat(row["executed_at"]),
                expires_at=datetime.fromisoformat(row["expires_at"]),
            )
        except (ValueError, KeyError) as exc:
            logger.warning("[Idempotency] cache row corrupt for key=%s: %s", call_key[:16], exc)
            return None

    def save(
        self,
        call_key: str,
        task_id: str,
        step_id: str | int,
        tool_name: str,
        result: ToolResult,
        *,
        ttl_hours: int | None = None,
    ) -> None:
        """
        Сохраняет успешный результат инструмента.
        Только success=True результаты сохраняются — неуспешные не кэшируем.
        """
        if not result.success:
            return   # не кэшируем ошибки

        ttl = ttl_hours if ttl_hours is not None else self._ttl
        now = datetime.utcnow()
        expires = now + timedelta(hours=ttl)

        try:
            blob = json.dumps(result.to_dict(), ensure_ascii=False, default=str)
        except (TypeError, ValueError) as exc:
            logger.warning("[Idempotency] result serialization failed: %s", exc)
            return

        # Обрезаем если слишком большой
        if len(blob.encode()) > _MAX_RESULT_BYTES:
            logger.debug("[Idempotency] result truncated for key=%s", call_key[:16])
            blob = json.dumps({
                "tool_name": result.tool_name,
                "success": True,
                "output": "[truncated — result too large to cache]",
                "error": None,
                "metadata": {"_truncated": True},
            })

        self._conn.execute(
            """
            INSERT OR REPLACE INTO tool_calls
                (call_key, task_id, step_id, tool_name, result_json, executed_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (call_key, task_id, str(step_id), tool_name,
             blob, now.isoformat(), expires.isoformat()),
        )
        self._conn.commit()
        logger.debug("[Idempotency] saved key=%s tool=%s task=%s", call_key[:16], tool_name, task_id[:8])

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def purge_expired(self) -> int:
        """Удаляет просроченные записи. Возвращает количество удалённых."""
        now = datetime.utcnow().isoformat()
        cur = self._conn.execute("DELETE FROM tool_calls WHERE expires_at <= ?", (now,))
        self._conn.commit()
        count = cur.rowcount
        if count:
            logger.info("[Idempotency] purged %d expired entries", count)
        return count

    def clear_task(self, task_id: str) -> int:
        """Удаляет все записи для задачи (после её завершения)."""
        cur = self._conn.execute("DELETE FROM tool_calls WHERE task_id=?", (task_id,))
        self._conn.commit()
        return cur.rowcount

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0]

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass
