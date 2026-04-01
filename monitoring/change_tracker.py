# Change Tracker — трассировка изменений агента
# Записывает: какие файлы тронул, какие команды запускал, какие ошибки получил.
# Формат: JSONL (одна строка лога = один JSON-объект).
#
# Использование:
#   from monitoring.change_tracker import ChangeTracker
#   tracker = ChangeTracker()
#   tracker.file_changed('outputs/report.md', 'create', agent='cognitive_core')
#   tracker.command_ran('pip install X', exit_code=0, agent='sandbox')
#   tracker.error_occurred('TimeoutError', 'LLM не ответил за 30с', agent='llm_router')
#   tracker.flush()

from __future__ import annotations

import json
import os
import time
import threading
from typing import Literal

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_LOG = os.path.join(_HERE, '..', 'logs', 'changes.jsonl')

EventKind = Literal['file_change', 'command', 'error', 'action', 'rollback']


class ChangeTracker:
    """Персистентный JSONL-трекер всех изменений, которые агент вносит в систему."""

    def __init__(self, log_path: str | None = None, max_buffer: int = 20):
        self._path = log_path or _DEFAULT_LOG
        self._buffer: list[dict] = []
        self._max_buffer = max_buffer
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(self._path), exist_ok=True)

    # ── Публичные методы записи ────────────────────────────────────────────

    def file_changed(self, path: str, op: str = 'modify',
                     agent: str = '', details: str = '') -> None:
        """Фиксирует изменение файла (create / modify / delete)."""
        self._record('file_change', agent=agent, data={
            'path': path, 'op': op, 'details': details,
        })

    def command_ran(self, command: str, exit_code: int | None = None,
                    agent: str = '', output_preview: str = '') -> None:
        """Фиксирует запуск внешней команды."""
        self._record('command', agent=agent, data={
            'command': command,
            'exit_code': exit_code,
            'output_preview': output_preview[:500],
        })

    def error_occurred(self, error_type: str, message: str,
                       agent: str = '', traceback_str: str = '') -> None:
        """Фиксирует ошибку."""
        self._record('error', agent=agent, data={
            'error_type': error_type,
            'message': message[:500],
            'traceback': traceback_str[:2000],
        })

    def action_taken(self, action: str, result: str = '',
                     agent: str = '', data: dict | None = None) -> None:
        """Фиксирует произвольное действие (dispatch, LLM-вызов и т.д.)."""
        self._record('action', agent=agent, data={
            'action': action,
            'result': result[:500],
            **(data or {}),
        })

    def rollback(self, what: str, reason: str = '', agent: str = '') -> None:
        """Фиксирует откат."""
        self._record('rollback', agent=agent, data={
            'what': what, 'reason': reason,
        })

    # ── Чтение ─────────────────────────────────────────────────────────────

    def recent(self, n: int = 50, kind: str | None = None) -> list[dict]:
        """Возвращает последние n записей (из файла + буфера)."""
        self.flush()
        entries: list[dict] = []
        try:
            with open(self._path, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except FileNotFoundError:
            pass
        if kind:
            entries = [e for e in entries if e.get('kind') == kind]
        return entries[-n:]

    def files_touched(self, last_n: int = 100) -> list[str]:
        """Список уникальных файлов, которые агент менял (последние N записей)."""
        recent = self.recent(last_n, kind='file_change')
        seen: dict[str, str] = {}
        for e in recent:
            path = e.get('data', {}).get('path', '')
            op = e.get('data', {}).get('op', '')
            if path:
                seen[path] = op
        return [f"{op}: {path}" for path, op in seen.items()]

    def error_summary(self, last_n: int = 100) -> dict[str, int]:
        """Группировка последних ошибок по типу."""
        recent = self.recent(last_n, kind='error')
        counts: dict[str, int] = {}
        for e in recent:
            etype = e.get('data', {}).get('error_type', 'unknown')
            counts[etype] = counts.get(etype, 0) + 1
        return counts

    # ── Внутренние ─────────────────────────────────────────────────────────

    def _record(self, kind: EventKind, agent: str, data: dict) -> None:
        entry = {
            'ts': time.time(),
            'time': time.strftime('%Y-%m-%d %H:%M:%S'),
            'kind': kind,
            'agent': agent,
            'data': data,
        }
        with self._lock:
            self._buffer.append(entry)
            if len(self._buffer) >= self._max_buffer:
                self._flush_locked()

    def flush(self) -> None:
        """Сбрасывает буфер на диск."""
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        if not self._buffer:
            return
        try:
            with open(self._path, 'a', encoding='utf-8') as f:
                for entry in self._buffer:
                    f.write(json.dumps(entry, ensure_ascii=False) + '\n')
            self._buffer.clear()
        except OSError:
            pass

    def clear(self) -> None:
        """Очищает лог (осторожно — необратимо)."""
        with self._lock:
            self._buffer.clear()
        try:
            with open(self._path, 'w', encoding='utf-8') as f:
                f.truncate(0)
        except OSError:
            pass
