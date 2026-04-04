"""
AuditJournal — персистентный журнал результатов аудит-тестов.

Хранит запись каждого выполненного шага агента: цель, интент, вердикт,
проблемы, использованный инструмент.  Переживает перезапуски процесса.

При планировании агент консультируется с журналом, чтобы не повторять
уже известные ошибки на похожих задачах.

Файл:  <memory_dir>/audit_journal.json
Ротация: хранится не более MAX_ENTRIES записей (старые вытесняются новыми).
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

MAX_ENTRIES = 500   # Максимум записей в журнале
MIN_KEYWORD_LEN = 3  # Минимальная длина слова для совпадения
_MAX_HINT_ENTRIES = 3  # Сколько прошлых провалов показываем в подсказке


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

_STOPWORDS = {
    'что', 'как', 'для', 'это', 'с', 'в', 'на', 'и', 'или', 'по', 'из',
    'к', 'у', 'о', 'а', 'но', 'не', 'же', 'то', 'мне', 'мой', 'моя',
    'моё', 'мои', 'the', 'a', 'an', 'of', 'in', 'to', 'for', 'is', 'it',
    'at', 'by', 'be', 'as', 'do', 'if', 'or', 'my',
}


def _keywords(text: str) -> set[str]:
    """Извлекает ключевые слова из строки (нижний регистр, без стоп-слов)."""
    words = re.findall(r'[а-яёa-z]+', text.lower())
    return {w for w in words if len(w) >= MIN_KEYWORD_LEN and w not in _STOPWORDS}


def _similarity(a: str, b: str) -> float:
    """Оценка сходства двух строк в диапазоне 0..1 (Jaccard index)."""
    ka, kb = _keywords(a), _keywords(b)
    if not ka or not kb:
        return 0.0
    return len(ka & kb) / len(ka | kb)


# ──────────────────────────────────────────────────────────────────────────────
# Data types
# ──────────────────────────────────────────────────────────────────────────────

class AuditEntry:
    """Одна запись в журнале = один шаг агента."""

    __slots__ = (
        'ts', 'run_id', 'goal', 'intent',
        'verdict', 'score', 'issues', 'tool_used',
    )

    def __init__(
        self,
        ts: float,
        run_id: str,
        goal: str,
        intent: str,
        verdict: str,
        score: float,
        issues: list[str],
        tool_used: str,
    ):
        self.ts        = ts
        self.run_id    = run_id
        self.goal      = goal
        self.intent    = intent
        self.verdict   = verdict
        self.score     = float(score)
        self.issues    = list(issues)
        self.tool_used = tool_used

    @classmethod
    def from_dict(cls, d: dict) -> 'AuditEntry':
        return cls(
            ts=float(d.get('ts', 0)),
            run_id=str(d.get('run_id', '')),
            goal=str(d.get('goal', '')),
            intent=str(d.get('intent', '')),
            verdict=str(d.get('verdict', '')),
            score=float(d.get('score', 0.0)),
            issues=list(d.get('issues', [])),
            tool_used=str(d.get('tool_used', '')),
        )

    def to_dict(self) -> dict:
        return {
            'ts':        self.ts,
            'run_id':    self.run_id,
            'goal':      self.goal,
            'intent':    self.intent,
            'verdict':   self.verdict,
            'score':     self.score,
            'issues':    self.issues,
            'tool_used': self.tool_used,
        }

    @property
    def is_failure(self) -> bool:
        return not self.verdict.startswith('SUCCESS')

    def human_date(self) -> str:
        try:
            return datetime.fromtimestamp(self.ts).strftime('%Y-%m-%d %H:%M')
        except (OSError, OverflowError, ValueError):
            return '?'


# ──────────────────────────────────────────────────────────────────────────────
# AuditJournal
# ──────────────────────────────────────────────────────────────────────────────

class AuditJournal:
    """Персистентный журнал аудит-результатов агента.

    Два потока данных:
        1. ``record_task()`` — legacy AuditEntry (оценка шагов).
        2. ``log()``         — structured audit events (§10 formal contracts).
           Каждый event имеет event_id, trace_id, task_id, step_id, actor.
           Хранятся в append-only JSONL-файле ``audit_events.jsonl``.
    """

    def __init__(self, memory_dir: str = '.agent_memory'):
        self._path = Path(memory_dir) / 'audit_journal.json'
        self._events_path = Path(memory_dir) / 'audit_events.jsonl'
        self._entries: list[AuditEntry] = []
        self._run_id: str = datetime.now().strftime('%Y%m%d_%H%M%S')
        self._lock = threading.Lock()
        self._load()

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            if self._path.exists():
                raw = json.loads(self._path.read_text(encoding='utf-8'))
                self._entries = [
                    AuditEntry.from_dict(d)
                    for d in raw.get('entries', [])
                    if isinstance(d, dict)
                ]
        except (OSError, json.JSONDecodeError, ValueError, KeyError):
            self._entries = []

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {'entries': [e.to_dict() for e in self._entries]}
            # Атомарная запись через временный файл
            tmp = self._path.with_suffix('.tmp')
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
            tmp.replace(self._path)
        except (OSError, TypeError, ValueError):
            pass  # Журнал не является критичным — падение не должно ронять агента

    # ── Write ─────────────────────────────────────────────────────────────────

    def record_task(
        self,
        goal: str,
        intent: str,
        verdict: str,
        score: float = 1.0,
        issues: list[str] | None = None,
        tool_used: str = '',
    ) -> None:
        """Записывает результат одного шага в журнал."""
        entry = AuditEntry(
            ts=time.time(),
            run_id=self._run_id,
            goal=str(goal)[:300],
            intent=str(intent),
            verdict=str(verdict),
            score=float(score),
            issues=list(issues or []),
            tool_used=str(tool_used),
        )
        self._entries.append(entry)
        # Ротация
        if len(self._entries) > MAX_ENTRIES:
            self._entries = self._entries[-MAX_ENTRIES:]
        self._save()

    # ── Query ─────────────────────────────────────────────────────────────────

    def get_similar_past_failures(
        self,
        goal: str,
        threshold: float = 0.20,
        max_results: int = _MAX_HINT_ENTRIES,
    ) -> list[AuditEntry]:
        """
        Возвращает до max_results прошлых НЕУДАЧНЫХ шагов, похожих на goal.
        Сортировка: сначала самые похожие.
        """
        scored: list[tuple[float, AuditEntry]] = []
        for e in self._entries:
            if not e.is_failure:
                continue
            sim = _similarity(goal, e.goal)
            if sim >= threshold:
                scored.append((sim, e))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored[:max_results]]

    def get_intent_failures(self, intent: str, max_results: int = 5) -> list[AuditEntry]:
        """Последние провалы для заданного интента."""
        failures = [e for e in self._entries if e.intent == intent and e.is_failure]
        return failures[-max_results:]

    def get_intent_failure_rate(self, intent: str, min_runs: int = 2) -> float:
        """
        Доля провалов для интента (0..1).
        Возвращает 0.0 если данных недостаточно.
        """
        relevant = [e for e in self._entries if e.intent == intent]
        if len(relevant) < min_runs:
            return 0.0
        failures = sum(1 for e in relevant if e.is_failure)
        return failures / len(relevant)

    def count_runs_for_goal(self, goal: str, threshold: float = 0.25) -> int:
        """Сколько раз выполнялась похожая задача."""
        return sum(1 for e in self._entries if _similarity(goal, e.goal) >= threshold)

    # ── Prompt helpers ────────────────────────────────────────────────────────

    def get_failure_summary_for_prompt(self, goal: str) -> str:
        """
        Возвращает текст-подсказку для LLM о прошлых провалах на похожих задачах.
        Если прошлых провалов нет — пустая строка.
        """
        past = self.get_similar_past_failures(goal)
        if not past:
            return ''

        lines: list[str] = [
            '⚠️ [AuditJournal] История прошлых провалов на похожих задачах:'
        ]
        for e in past:
            issues_str = '; '.join(e.issues[:3]) if e.issues else '—'
            tool_note = f', использован инструмент: {e.tool_used}' if e.tool_used and e.tool_used != 'unknown' else ''
            lines.append(
                f'  • [{e.human_date()}] "{e.goal[:80]}" → {e.verdict}'
                f'{tool_note}. Проблемы: {issues_str}'
            )
        lines.append(
            'Учти эти ошибки при планировании. Не повторяй тот же подход, '
            'который уже провалился.'
        )
        return '\n'.join(lines)

    def get_run_summary(self, run_id: str | None = None) -> dict[str, Any]:
        """
        Суммарная статистика по run_id (или по текущему сеансу).
        """
        rid = run_id or self._run_id
        entries = [e for e in self._entries if e.run_id == rid]
        if not entries:
            return {'run_id': rid, 'total': 0}

        verdicts: dict[str, int] = {}
        for e in entries:
            verdicts[e.verdict] = verdicts.get(e.verdict, 0) + 1

        successes = sum(1 for e in entries if not e.is_failure)
        return {
            'run_id':   rid,
            'total':    len(entries),
            'success':  successes,
            'failure':  len(entries) - successes,
            'rate':     successes / len(entries) if entries else 0.0,
            'verdicts': verdicts,
        }

    # ── Structured audit events (§10 formal_contracts_spec) ──────────────────

    def log(self, event: object) -> None:
        """Append structured audit event to JSONL log.

        Принимает dict или объект с ``.to_dict()`` (StructuredAuditEvent).
        Thread-safe; append-only.
        """
        if isinstance(event, dict):
            data = event
        elif hasattr(event, 'to_dict'):
            data = event.to_dict()  # type: ignore[union-attr]
        else:
            return  # ignore unsupported types silently

        with self._lock:
            try:
                self._events_path.parent.mkdir(parents=True, exist_ok=True)
                line = json.dumps(data, ensure_ascii=False, default=str)
                with open(self._events_path, 'a', encoding='utf-8') as f:
                    f.write(line + '\n')
            except (OSError, TypeError, ValueError):
                pass  # audit не должен ронять агента

    def get_events_by_trace_id(self, trace_id: str) -> list[dict]:
        """Возвращает все structured events для данного trace_id.

        Читает JSONL-файл и фильтрует по trace_id.
        """
        results: list[dict] = []
        try:
            if not self._events_path.exists():
                return results
            with open(self._events_path, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        evt = json.loads(line)
                        if evt.get('trace_id') == trace_id:
                            results.append(evt)
                    except (json.JSONDecodeError, AttributeError):
                        continue
        except OSError:
            pass
        return results

    def get_events(self, max_count: int = 200) -> list[dict]:
        """Возвращает последние ``max_count`` structured events."""
        results: list[dict] = []
        try:
            if not self._events_path.exists():
                return results
            with open(self._events_path, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        results.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass
        return results[-max_count:]


# ──────────────────────────────────────────────────────────────────────────────
# Singleton factory
# ──────────────────────────────────────────────────────────────────────────────

_instances: dict[str, AuditJournal] = {}


def get_journal(memory_dir: str = '.agent_memory') -> AuditJournal:
    """
    Возвращает единственный экземпляр AuditJournal для данного memory_dir.
    Singleton в рамках процесса — не нужно каждый раз читать диск.
    """
    key = str(Path(memory_dir).resolve())
    if key not in _instances:
        _instances[key] = AuditJournal(memory_dir=memory_dir)
    return _instances[key]
