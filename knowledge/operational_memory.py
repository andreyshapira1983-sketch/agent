# Operational Memory — структурная память, влияющая на decision policy
# Три слоя: semantic, procedural, failure
# Память НЕ текст в промпте, а фильтры и ограничения для планировщика.

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ProcedureRecord:
    """Успешная последовательность действий (процедурная память)."""
    goal_pattern: str              # нормализованный паттерн цели
    steps: list[str]               # последовательность действий
    success_count: int = 0
    fail_count: int = 0
    last_used: float = field(default_factory=time.time)
    avg_cycles: float = 1.0        # среднее число циклов на выполнение
    context_tags: list[str] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.fail_count
        if total == 0:
            return 0.5
        return self.success_count / total

    @property
    def confidence(self) -> float:
        total = self.success_count + self.fail_count
        return min(total / 5.0, 1.0)

    def to_dict(self) -> dict:
        return {
            'goal_pattern': self.goal_pattern,
            'steps': self.steps,
            'success_count': self.success_count,
            'fail_count': self.fail_count,
            'last_used': self.last_used,
            'avg_cycles': self.avg_cycles,
            'context_tags': self.context_tags,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ProcedureRecord:
        return cls(
            goal_pattern=d.get('goal_pattern', ''),
            steps=d.get('steps', []),
            success_count=d.get('success_count', 0),
            fail_count=d.get('fail_count', 0),
            last_used=d.get('last_used', 0),
            avg_cycles=d.get('avg_cycles', 1.0),
            context_tags=d.get('context_tags', []),
        )


@dataclass
class FailureRecord:
    """Запись о провале — знание «чего не делать»."""
    goal_pattern: str
    failed_step: str               # какое действие ломалось
    failure_category: str          # из FailureCategory.value
    failure_signature: str         # краткая сигнатура
    occurrence_count: int = 1
    last_seen: float = field(default_factory=time.time)
    recovery_that_worked: str = '' # что помогло (если помогло)
    context_tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'goal_pattern': self.goal_pattern,
            'failed_step': self.failed_step,
            'failure_category': self.failure_category,
            'failure_signature': self.failure_signature,
            'occurrence_count': self.occurrence_count,
            'last_seen': self.last_seen,
            'recovery_that_worked': self.recovery_that_worked,
            'context_tags': self.context_tags,
        }

    @classmethod
    def from_dict(cls, d: dict) -> FailureRecord:
        return cls(
            goal_pattern=d.get('goal_pattern', ''),
            failed_step=d.get('failed_step', ''),
            failure_category=d.get('failure_category', 'unknown'),
            failure_signature=d.get('failure_signature', ''),
            occurrence_count=d.get('occurrence_count', 1),
            last_seen=d.get('last_seen', 0),
            recovery_that_worked=d.get('recovery_that_worked', ''),
            context_tags=d.get('context_tags', []),
        )


class OperationalMemory:
    """
    Структурная память, влияющая на поисковое пространство планировщика.

    Три слоя:
        A. Semantic memory — факты о проекте/пользователе (делегируется KnowledgeSystem)
        B. Procedural memory — какие последовательности действий работали
        C. Failure memory — какие действия ломались и что делать вместо них

    Ключевая идея: память МЕНЯЕТ набор допустимых шагов до генерации плана,
    а не «советует» LLM текстом в промпте.
    """

    def __init__(self, data_dir: str = '.agent_memory'):
        self._data_dir = data_dir
        self._procedures: list[ProcedureRecord] = []
        self._failures: list[FailureRecord] = []
        self._procedures_path = os.path.join(data_dir, 'procedural_memory.json')
        self._failures_path = os.path.join(data_dir, 'failure_memory.json')
        self._load()

    # ── Procedural Memory ─────────────────────────────────────────────────────

    def record_procedure(self, goal: str, steps: list[str], success: bool,
                         cycles_used: int = 1, context_tags: list[str] | None = None):
        """Записывает или обновляет процедуру."""
        pattern = self._normalize_goal(goal)
        existing = self._find_procedure(pattern)
        if existing:
            if success:
                existing.success_count += 1
            else:
                existing.fail_count += 1
            existing.last_used = time.time()
            # Скользящее среднее по числу циклов
            existing.avg_cycles = (
                existing.avg_cycles * 0.7 + cycles_used * 0.3
            )
        else:
            proc = ProcedureRecord(
                goal_pattern=pattern,
                steps=steps,
                success_count=1 if success else 0,
                fail_count=0 if success else 1,
                avg_cycles=float(cycles_used),
                context_tags=context_tags or [],
            )
            self._procedures.append(proc)
        self._save_procedures()

    def get_procedure(self, goal: str) -> Optional[ProcedureRecord]:
        """Ищет процедуру по goal (exact + fuzzy match)."""
        pattern = self._normalize_goal(goal)
        # Exact
        exact = self._find_procedure(pattern)
        if exact and exact.success_rate >= 0.5 and exact.confidence >= 0.3:
            return exact
        # Fuzzy: Jaccard по словам
        best: Optional[ProcedureRecord] = None
        best_score = 0.3  # минимальный порог
        goal_words = set(pattern.split())
        for proc in self._procedures:
            proc_words = set(proc.goal_pattern.split())
            intersection = goal_words & proc_words
            union = goal_words | proc_words
            if not union:
                continue
            score = len(intersection) / len(union)
            if score > best_score and proc.success_rate >= 0.5:
                best_score = score
                best = proc
        return best

    def get_successful_steps(self, goal: str) -> list[str]:
        """Возвращает шаги процедуры с высоким success_rate, или пустой список."""
        proc = self.get_procedure(goal)
        if proc and proc.success_rate >= 0.6:
            return list(proc.steps)
        return []

    # ── Failure Memory ────────────────────────────────────────────────────────

    def record_failure(self, goal: str, failed_step: str, category: str,
                       signature: str = '', context_tags: list[str] | None = None):
        """Записывает провал. Если такой провал уже видели — инкрементирует счётчик."""
        pattern = self._normalize_goal(goal)
        existing = self._find_failure(pattern, failed_step, category)
        if existing:
            existing.occurrence_count += 1
            existing.last_seen = time.time()
        else:
            rec = FailureRecord(
                goal_pattern=pattern,
                failed_step=failed_step,
                failure_category=category,
                failure_signature=signature,
                context_tags=context_tags or [],
            )
            self._failures.append(rec)
        self._save_failures()

    def record_recovery(self, goal: str, failed_step: str, category: str,
                        recovery: str):
        """Фиксирует успешный recovery для известного провала."""
        pattern = self._normalize_goal(goal)
        existing = self._find_failure(pattern, failed_step, category)
        if existing:
            existing.recovery_that_worked = recovery
            self._save_failures()

    # ── Decay: провалы не должны блокировать навсегда ──────────────────────
    _DECAY_WINDOW_H = 24    # через 24ч count уменьшается вдвое
    _DECAY_FLOOR    = 1     # минимальный effective count после decay

    def _effective_failure_count(self, fr: 'FailureRecord') -> int:
        """Возвращает occurrence_count с учётом временного распада (decay).

        Каждые _DECAY_WINDOW_H часов с момента last_seen count делится на 2.
        Это гарантирует, что старые провалы не блокируют агент навсегда.
        """
        age_h = (time.time() - fr.last_seen) / 3600.0
        if age_h <= 0:
            return fr.occurrence_count
        halvings = int(age_h / self._DECAY_WINDOW_H)
        effective = fr.occurrence_count >> halvings  # быстрое деление на 2^halvings
        return max(effective, self._DECAY_FLOOR)

    def get_blocked_steps(self, goal: str, threshold: int = 3) -> list[dict]:
        """Возвращает шаги, которые проваливались >= threshold раз для похожей цели.

        Это фильтр: планировщик должен ИСКЛЮЧИТЬ эти шаги из плана.
        Учитывается временной decay: старые провалы «забываются» (count/2 каждые 24ч).
        """
        pattern = self._normalize_goal(goal)
        goal_words = set(pattern.split())
        blocked = []
        for fr in self._failures:
            fr_words = set(fr.goal_pattern.split())
            intersection = goal_words & fr_words
            union = goal_words | fr_words
            similarity = len(intersection) / len(union) if union else 0
            effective_count = self._effective_failure_count(fr)
            if similarity >= 0.3 and effective_count >= threshold:
                blocked.append({
                    'step': fr.failed_step,
                    'category': fr.failure_category,
                    'count': effective_count,
                    'recovery': fr.recovery_that_worked,
                })
        return blocked

    def get_failure_constraints(self, goal: str) -> str:
        """Генерирует текст ограничений для планировщика на основе failure memory.

        Формат: детерминированный набор запретов, а не «совет».
        """
        blocked = self.get_blocked_steps(goal)
        if not blocked:
            return ''
        lines = []
        for b in blocked[:5]:
            line = f"ЗАПРЕТ: не использовать '{b['step']}' ({b['category']}, проваливался {b['count']}x)"
            if b['recovery']:
                line += f". Вместо этого: {b['recovery']}"
            lines.append(line)
        return '[FAILURE MEMORY]\n' + '\n'.join(lines)

    def get_priority_boost(self, goal: str) -> list[str]:
        """Возвращает шаги, которые уже были успешны для похожих целей.

        Планировщик должен ПОВЫСИТЬ приоритет этих шагов.
        """
        proc = self.get_procedure(goal)
        if proc and proc.success_rate >= 0.6:
            return list(proc.steps)
        return []

    # ── Persistence ───────────────────────────────────────────────────────────

    def _save_procedures(self):
        try:
            os.makedirs(self._data_dir, exist_ok=True)
            data = [p.to_dict() for p in self._procedures[-200:]]
            with open(self._procedures_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
        except (OSError, ValueError):
            pass

    def _save_failures(self):
        try:
            os.makedirs(self._data_dir, exist_ok=True)
            data = [fr.to_dict() for fr in self._failures[-500:]]
            with open(self._failures_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
        except (OSError, ValueError):
            pass

    def _load(self):
        try:
            if os.path.exists(self._procedures_path):
                with open(self._procedures_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self._procedures = [ProcedureRecord.from_dict(d) for d in data]
        except (OSError, json.JSONDecodeError, ValueError):
            pass
        try:
            if os.path.exists(self._failures_path):
                with open(self._failures_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self._failures = [FailureRecord.from_dict(d) for d in data]
        except (OSError, json.JSONDecodeError, ValueError):
            pass

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_goal(goal: str) -> str:
        import re
        s = goal.lower().strip()
        s = re.sub(r'[^\w\s]', '', s)
        s = re.sub(r'\s+', ' ', s)
        return s[:200]

    def _find_procedure(self, pattern: str) -> Optional[ProcedureRecord]:
        for proc in self._procedures:
            if proc.goal_pattern == pattern:
                return proc
        return None

    def _find_failure(self, pattern: str, step: str, category: str) -> Optional[FailureRecord]:
        for fr in self._failures:
            if (fr.goal_pattern == pattern
                    and fr.failed_step == step
                    and fr.failure_category == category):
                return fr
        return None

    def summary(self) -> dict:
        return {
            'procedures': len(self._procedures),
            'failures': len(self._failures),
            'top_procedures': [
                {'goal': p.goal_pattern[:60], 'success_rate': p.success_rate}
                for p in sorted(self._procedures, key=lambda x: -x.success_rate)[:5]
            ],
            'top_failures': [
                {'goal': fr.goal_pattern[:60], 'category': fr.failure_category,
                 'count': fr.occurrence_count}
                for fr in sorted(self._failures, key=lambda x: -x.occurrence_count)[:5]
            ],
        }
