# Structured Dynamic Skills — навыки с trigger / preconditions / steps / postchecks
# Заменяет простые keyword→code навыки на полноценные исполняемые шаблоны.

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StructuredSkill:
    """Полноценный исполняемый шаблон навыка."""
    name: str
    trigger: list[str]                    # ключевые слова / паттерны
    preconditions: list[str] = field(default_factory=list)   # что должно быть true
    steps: list[str] = field(default_factory=list)           # шаги выполнения (код/инструкция)
    postchecks: list[str] = field(default_factory=list)      # проверки после выполнения
    failure_signatures: list[str] = field(default_factory=list)  # какие ошибки обрабатывает
    cooldown_seconds: float = 0.0
    confidence: float = 0.5
    use_count: int = 0
    success_count: int = 0
    last_used: float = 0.0
    created_at: float = field(default_factory=time.time)
    source: str = 'auto'                 # auto / manual / codified_error

    @property
    def success_rate(self) -> float:
        if self.use_count == 0:
            return 0.5
        return self.success_count / self.use_count

    @property
    def is_on_cooldown(self) -> bool:
        if self.cooldown_seconds <= 0:
            return False
        return (time.time() - self.last_used) < self.cooldown_seconds

    def record_use(self, success: bool):
        self.use_count += 1
        if success:
            self.success_count += 1
        self.last_used = time.time()
        # Обновляем confidence на основе реальных данных
        total = self.use_count
        alpha = 0.3  # learning rate
        self.confidence = self.confidence * (1 - alpha) + (self.success_rate * alpha)

    def matches(self, goal: str) -> float:
        """Возвращает score совпадения (0.0 .. 1.0) с similarity retrieval.

        Использует комбинацию:
          - exact keyword match (быстрый)
          - token Jaccard similarity (fuzzy)
          - TF-IDF-подобный вес редких слов
        """
        goal_lower = goal.lower()
        goal_tokens = _tokenize(goal_lower)
        trigger_tokens = set()
        for kw in self.trigger:
            trigger_tokens.update(_tokenize(kw.lower()))

        if not goal_tokens or not trigger_tokens:
            return 0.0

        # Exact keyword match (legacy)
        exact_matched = sum(1 for kw in self.trigger if kw.lower() in goal_lower)
        exact_score = exact_matched / len(self.trigger) if self.trigger else 0.0

        # Jaccard similarity по токенам
        intersection = goal_tokens & trigger_tokens
        union = goal_tokens | trigger_tokens
        jaccard = len(intersection) / len(union) if union else 0.0

        # TF-IDF-подобный вес: редкие совпавшие токены важнее
        if intersection:
            idf_sum = sum(
                1.0 / (1.0 + math.log1p(len(t)))  # короткие = более специфичные
                for t in intersection
            )
            idf_norm = idf_sum / max(len(trigger_tokens), 1)
        else:
            idf_norm = 0.0

        # Комбинированный score
        raw = 0.4 * exact_score + 0.35 * jaccard + 0.25 * idf_norm

        # Штраф за cooldown
        if self.is_on_cooldown:
            raw *= 0.1

        # Boost за confidence и success_rate
        return min(1.0, raw * (0.5 + 0.3 * self.confidence + 0.2 * self.success_rate))

    def matches_failure(self, error_msg: str) -> bool:
        """Проверяет, подходит ли навык для обработки данной ошибки."""
        if not self.failure_signatures:
            return False
        error_lower = error_msg.lower()
        return any(sig.lower() in error_lower for sig in self.failure_signatures)

    def get_plan(self) -> str:
        """Возвращает готовый план (объединение steps)."""
        return '\n'.join(self.steps)

    def check_preconditions(self) -> tuple[bool, str]:
        """Проверяет preconditions. Возвращает (ok, reason)."""
        for pre in self.preconditions:
            try:
                # preconditions формата: env:VAR_NAME, file:path, module:name
                if pre.startswith('env:'):
                    var = pre[4:].strip()
                    if not os.environ.get(var):
                        return False, f'env var {var} not set'
                elif pre.startswith('file:'):
                    path = pre[5:].strip()
                    if not os.path.exists(path):
                        return False, f'file {path} not found'
                elif pre.startswith('module:'):
                    mod = pre[7:].strip()
                    _ALLOWED_MODULES = {
                        'os', 'sys', 'json', 're', 'math', 'time', 'datetime',
                        'pathlib', 'hashlib', 'collections', 'itertools',
                        'functools', 'typing', 'dataclasses', 'logging',
                        'requests', 'httpx', 'aiohttp',
                        'numpy', 'pandas', 'torch', 'transformers',
                        'PIL', 'bs4', 'yaml', 'toml',
                    }
                    top_module = mod.split('.')[0]
                    if top_module not in _ALLOWED_MODULES:
                        return False, f'module {mod} not in allowed whitelist'
                    try:
                        __import__(mod)
                    except ImportError:
                        return False, f'module {mod} not installed'
                # else: ignore unknown precondition format
            except Exception as e:
                return False, f'precondition check failed: {e}'
        return True, ''

    def to_dict(self) -> dict:
        return {
            'name': self.name,
            'trigger': self.trigger,
            'preconditions': self.preconditions,
            'steps': self.steps,
            'postchecks': self.postchecks,
            'failure_signatures': self.failure_signatures,
            'cooldown_seconds': self.cooldown_seconds,
            'confidence': round(self.confidence, 3),
            'use_count': self.use_count,
            'success_count': self.success_count,
            'last_used': self.last_used,
            'created_at': self.created_at,
            'source': self.source,
        }

    @classmethod
    def from_dict(cls, d: dict) -> StructuredSkill:
        return cls(
            name=d.get('name', ''),
            trigger=d.get('trigger', []),
            preconditions=d.get('preconditions', []),
            steps=d.get('steps', []),
            postchecks=d.get('postchecks', []),
            failure_signatures=d.get('failure_signatures', []),
            cooldown_seconds=d.get('cooldown_seconds', 0.0),
            confidence=d.get('confidence', 0.5),
            use_count=d.get('use_count', 0),
            success_count=d.get('success_count', 0),
            last_used=d.get('last_used', 0.0),
            created_at=d.get('created_at', 0.0),
            source=d.get('source', 'auto'),
        )

    @classmethod
    def from_legacy(cls, keywords: list[str], code: str,
                    trigger_error: str = '') -> StructuredSkill:
        """Конвертирует старый формат (keywords, code) в StructuredSkill."""
        name = '_'.join(keywords[:3]) if keywords else 'unnamed'
        return cls(
            name=name,
            trigger=keywords,
            steps=[code],
            failure_signatures=[trigger_error] if trigger_error else [],
            source='codified_error' if trigger_error else 'auto',
        )


class StructuredSkillRegistry:
    """Реестр structured skills. Центральный поиск навыков перед _plan()."""

    def __init__(self, data_dir: str = '.agent_memory'):
        self._skills: dict[str, StructuredSkill] = {}
        self._path = os.path.join(data_dir, 'structured_skills.json')
        self._load()

    def has(self, name: str) -> bool:
        """Public accessor: есть ли навык с таким именем."""
        return name in self._skills

    def register(self, skill: StructuredSkill) -> None:
        self._skills[skill.name] = skill
        self._save()

    def remove(self, name: str) -> None:
        self._skills.pop(name, None)
        self._save()

    def find_best(self, goal: str, top_k: int = 3) -> list[tuple[float, StructuredSkill]]:
        """Ищет навыки по goal. Возвращает [(score, skill)] отсортированные по score DESC."""
        results = []
        for skill in self._skills.values():
            score = skill.matches(goal)
            if score > 0.1:
                results.append((score, skill))
        results.sort(key=lambda x: -x[0])
        return results[:top_k]

    def find_for_failure(self, error_msg: str) -> Optional[StructuredSkill]:
        """Ищет навык, который умеет обрабатывать данную ошибку."""
        for skill in self._skills.values():
            if skill.matches_failure(error_msg) and not skill.is_on_cooldown:
                return skill
        return None

    def find_exact(self, goal: str, threshold: float = 0.6) -> Optional[StructuredSkill]:
        """Ищет один лучший навык. Возвращает None если score < threshold."""
        results = self.find_best(goal, top_k=1)
        if results and results[0][0] >= threshold:
            return results[0][1]
        return None

    def record_use(self, name: str, success: bool) -> None:
        skill = self._skills.get(name)
        if skill:
            skill.record_use(success)
            self._save()

    def get(self, name: str) -> Optional[StructuredSkill]:
        return self._skills.get(name)

    def all_skills(self) -> list[StructuredSkill]:
        return list(self._skills.values())

    def import_legacy_skills(self, legacy: list[tuple[list[str], str]]):
        """Импорт из старого формата _dynamic_skills."""
        for keywords, code in legacy:
            skill = StructuredSkill.from_legacy(keywords, code)
            if skill.name not in self._skills:
                self._skills[skill.name] = skill
        self._save()

    def create_from_chain(self, candidate: dict) -> Optional[StructuredSkill]:
        """P3: создаёт StructuredSkill из кандидата extract_repeating_chains().

        Args:
            candidate: {'signature', 'goal_pattern', 'trigger_keywords', 'steps', 'count'}

        Returns:
            Created skill or None if duplicate.
        """
        trigger = candidate.get('trigger_keywords', [])
        if not trigger:
            return None
        name = 'auto_' + '_'.join(trigger[:3])
        if name in self._skills:
            return None  # уже есть
        skill = StructuredSkill(
            name=name,
            trigger=trigger,
            steps=candidate.get('steps', []),
            confidence=min(0.8, 0.3 + 0.1 * candidate.get('count', 0)),
            use_count=candidate.get('count', 0),
            success_count=candidate.get('count', 0),  # все цепочки были успешными
            source='auto_chain',
        )
        self._skills[name] = skill
        self._save()
        return skill

    # ── Persistence ───────────────────────────────────────────────────────────

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self._path) or '.', exist_ok=True)
            data = [s.to_dict() for s in self._skills.values()]
            with open(self._path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
        except (OSError, ValueError):
            pass

    def _load(self):
        try:
            if not os.path.exists(self._path):
                return
            with open(self._path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for d in data:
                skill = StructuredSkill.from_dict(d)
                if skill.name:
                    self._skills[skill.name] = skill
        except (OSError, json.JSONDecodeError, ValueError):
            pass

    def summary(self) -> dict:
        return {
            'total': len(self._skills),
            'by_source': {},
            'top_by_success': [
                {'name': s.name, 'success_rate': s.success_rate, 'uses': s.use_count}
                for s in sorted(self._skills.values(), key=lambda x: -x.success_rate)[:5]
            ],
        }


# ── Utilities ─────────────────────────────────────────────────────────────────

_TOKEN_RE = re.compile(r'[a-zа-яё0-9_]{2,}')


def _tokenize(text: str) -> set[str]:
    """Извлекает множество значимых токенов из текста (≥2 символа)."""
    return set(_TOKEN_RE.findall(text.lower()))
