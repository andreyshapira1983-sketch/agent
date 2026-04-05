# Trust Model — модель доверия агента-партнёра
# Архитектура: Partner Core → доверие как числовой score (0.0–1.0)
# Меморандум (Часть 52): "доверие не бинарно, а заработано"
#
# Функции:
#   - числовой trust score (0.0–1.0) для пользователей, источников, сервисов
#   - автоматическое затухание (decay) со временем без подтверждений
#   - события доверия: успех, провал, нарушение, восстановление
#   - связка trust → разрешённые действия (trust gates)
#   - персистентность через PersistentBrain

from __future__ import annotations

import time
import math
import threading
from dataclasses import dataclass, field
from enum import Enum


class TrustCategory(Enum):
    """Категория субъекта доверия."""
    USER     = 'user'
    SOURCE   = 'source'       # источник информации
    SERVICE  = 'service'      # внешний API/сервис
    AGENT    = 'agent'        # другой агент
    LIBRARY  = 'library'      # внешняя библиотека


class TrustEvent(Enum):
    """Типы событий, влияющих на доверие."""
    SUCCESS           = 'success'            # задача выполнена успешно
    FAILURE           = 'failure'            # задача провалена
    POLICY_VIOLATION  = 'policy_violation'   # нарушение политики
    VERIFIED          = 'verified'           # данные подтверждены
    CONTRADICTED      = 'contradicted'       # данные опровергнуты
    INTERACTION       = 'interaction'        # обычное взаимодействие
    RESTORED          = 'restored'           # ручное восстановление доверия


# Дельты trust score по типам событий
_TRUST_DELTAS: dict[TrustEvent, float] = {
    TrustEvent.SUCCESS:          +0.05,
    TrustEvent.FAILURE:          -0.08,
    TrustEvent.POLICY_VIOLATION: -0.20,
    TrustEvent.VERIFIED:         +0.10,
    TrustEvent.CONTRADICTED:     -0.15,
    TrustEvent.INTERACTION:      +0.01,
    TrustEvent.RESTORED:         +0.15,
}


@dataclass
class TrustRecord:
    """Запись доверия к одному субъекту."""
    subject_id: str
    category: TrustCategory
    score: float = 0.5                  # начальное доверие — нейтральное
    min_score: float = 0.0
    max_score: float = 1.0
    last_event_time: float = field(default_factory=time.time)
    created_at: float = field(default_factory=time.time)
    event_count: int = 0
    history: list[dict] = field(default_factory=list)

    # ── Decay ─────────────────────────────────────────────────────────────

    # Доверие затухает если нет подтверждений
    DECAY_RATE: float = 0.005           # потеря за час бездействия
    DECAY_FLOOR: float = 0.2            # минимум после decay (не падает до 0)
    DECAY_GRACE_HOURS: float = 24.0     # grace period — первые 24ч без decay

    def apply_decay(self) -> float:
        """Применяет decay и возвращает новый score."""
        now = time.time()
        hours_since = (now - self.last_event_time) / 3600.0
        if hours_since <= self.DECAY_GRACE_HOURS:
            return self.score

        decay_hours = hours_since - self.DECAY_GRACE_HOURS
        decay = self.DECAY_RATE * math.sqrt(decay_hours)  # sqrt — замедляющий decay
        new_score = max(self.DECAY_FLOOR, self.score - decay)
        self.score = new_score
        return new_score

    # ── Serialization ─────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            'subject_id': self.subject_id,
            'category': self.category.value,
            'score': round(self.score, 4),
            'last_event_time': self.last_event_time,
            'created_at': self.created_at,
            'event_count': self.event_count,
            'history': self.history[-50:],  # последние 50 событий
        }

    @classmethod
    def from_dict(cls, d: dict) -> TrustRecord:
        return cls(
            subject_id=str(d['subject_id']),
            category=TrustCategory(d.get('category', 'user')),
            score=float(d.get('score', 0.5)),
            last_event_time=float(d.get('last_event_time', time.time())),
            created_at=float(d.get('created_at', time.time())),
            event_count=int(d.get('event_count', 0)),
            history=list(d.get('history', [])),
        )


# ── Trust Gates: trust → permissions ──────────────────────────────────────────

class TrustGate:
    """
    Определяет пороги доверия для различных действий.

    Меморандум (Часть 22): "опасные, дорогие, юридически чувствительные
    или необратимые действия требуют моего явного подтверждения"

    Trust gates:
        score >= 0.8  → полная автономия (safe actions)
        score >= 0.5  → стандартные действия, опасные требуют approval
        score >= 0.3  → только read + безопасные write
        score <  0.3  → read-only, всё остальное требует human approval
    """

    FULL_AUTONOMY    = 0.8
    STANDARD         = 0.5
    RESTRICTED       = 0.3
    READ_ONLY        = 0.0   # всё ниже RESTRICTED

    @classmethod
    def allowed_actions(cls, score: float) -> set[str]:
        """Возвращает набор разрешённых типов действий для данного trust score."""
        actions = {'read', 'query', 'observe'}
        if score >= cls.RESTRICTED:
            actions |= {'write_safe', 'create_file', 'search'}
        if score >= cls.STANDARD:
            actions |= {'write', 'execute_safe', 'send_message', 'api_call'}
        if score >= cls.FULL_AUTONOMY:
            actions |= {'execute', 'deploy', 'delete', 'financial'}
        return actions

    @classmethod
    def requires_approval(cls, score: float, action_type: str) -> bool:
        """Проверяет, требуется ли human approval для действия при данном trust."""
        allowed = cls.allowed_actions(score)
        return action_type not in allowed

    @classmethod
    def describe_level(cls, score: float) -> str:
        """Человекочитаемое описание уровня доверия."""
        if score >= cls.FULL_AUTONOMY:
            return "полное доверие — автономная работа"
        if score >= cls.STANDARD:
            return "стандартное доверие — опасные действия требуют подтверждения"
        if score >= cls.RESTRICTED:
            return "ограниченное доверие — только безопасные операции"
        return "минимальное доверие — только чтение"


# ── Trust Model ───────────────────────────────────────────────────────────────

class TrustModel:
    """
    Модель доверия агента-партнёра.

    Архитектура:
        - Каждый субъект (user, source, service, agent) получает числовой score
        - Score обновляется по событиям (успех, провал, нарушение)
        - Decay: без подтверждений score медленно снижается
        - Trust gates: score определяет допустимые действия
        - Интеграция с SecuritySystem, GovernanceLayer, HumanApprovalLayer

    KPI партнёра (из задания):
        - насколько надёжен          → trust score отражает reliability
        - насколько безопасен        → trust gates ограничивают опасные действия
        - насколько предсказуем      → history + decay дают предсказуемость
    """

    MAX_HISTORY_PER_SUBJECT = 50

    def __init__(self):
        self._records: dict[str, TrustRecord] = {}
        self._lock = threading.Lock()
        self._listeners: list = []

    # ── CRUD ──────────────────────────────────────────────────────────────

    def get_or_create(self, subject_id: str,
                      category: TrustCategory = TrustCategory.USER,
                      initial_score: float = 0.5) -> TrustRecord:
        """Получает или создаёт запись доверия."""
        with self._lock:
            if subject_id not in self._records:
                self._records[subject_id] = TrustRecord(
                    subject_id=subject_id,
                    category=category,
                    score=max(0.0, min(1.0, initial_score)),
                )
            return self._records[subject_id]

    def get_score(self, subject_id: str) -> float:
        """Возвращает текущий trust score с учётом decay."""
        record = self._records.get(subject_id)
        if not record:
            return 0.5  # нейтральный default
        return record.apply_decay()

    def record_event(self, subject_id: str, event: TrustEvent,
                     category: TrustCategory = TrustCategory.USER,
                     details: str = '') -> float:
        """
        Записывает событие доверия, обновляет score.
        Возвращает новый score.
        """
        with self._lock:
            record = self.get_or_create(subject_id, category)

            # Применяем decay перед обновлением
            record.apply_decay()

            delta = _TRUST_DELTAS.get(event, 0.0)
            old_score = record.score
            record.score = max(record.min_score,
                               min(record.max_score, record.score + delta))
            record.last_event_time = time.time()
            record.event_count += 1

            # Пишем в history
            entry = {
                'event': event.value,
                'delta': round(delta, 4),
                'old_score': round(old_score, 4),
                'new_score': round(record.score, 4),
                'time': time.time(),
                'details': details,
            }
            record.history.append(entry)
            if len(record.history) > self.MAX_HISTORY_PER_SUBJECT:
                record.history = record.history[-self.MAX_HISTORY_PER_SUBJECT:]

            # Уведомляем listeners
            for listener in self._listeners:
                try:
                    listener(subject_id, event, old_score, record.score)
                except Exception:
                    pass

            return record.score

    # ── Trust Gates ───────────────────────────────────────────────────────

    def check_permission(self, subject_id: str, action_type: str) -> bool:
        """Проверяет, разрешено ли действие для данного субъекта."""
        score = self.get_score(subject_id)
        return not TrustGate.requires_approval(score, action_type)

    def get_allowed_actions(self, subject_id: str) -> set[str]:
        """Возвращает набор разрешённых действий для субъекта."""
        score = self.get_score(subject_id)
        return TrustGate.allowed_actions(score)

    def describe_trust(self, subject_id: str) -> dict:
        """Человекочитаемое описание доверия к субъекту."""
        record = self._records.get(subject_id)
        if not record:
            return {
                'subject_id': subject_id,
                'score': 0.5,
                'level': TrustGate.describe_level(0.5),
                'allowed_actions': sorted(TrustGate.allowed_actions(0.5)),
                'status': 'unknown',
            }

        score = record.apply_decay()
        return {
            'subject_id': subject_id,
            'category': record.category.value,
            'score': round(score, 4),
            'level': TrustGate.describe_level(score),
            'allowed_actions': sorted(TrustGate.allowed_actions(score)),
            'event_count': record.event_count,
            'last_event': record.history[-1] if record.history else None,
        }

    # ── Listeners ─────────────────────────────────────────────────────────

    def add_listener(self, callback):
        """Добавляет listener для изменений trust (для интеграции с SecuritySystem)."""
        self._listeners.append(callback)

    # ── Serialization ─────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        with self._lock:
            return {
                sid: rec.to_dict()
                for sid, rec in self._records.items()
            }

    def load_from_dict(self, data: dict):
        with self._lock:
            for sid, rec_data in data.items():
                try:
                    self._records[sid] = TrustRecord.from_dict(rec_data)
                except (KeyError, ValueError):
                    continue

    # ── Summary ───────────────────────────────────────────────────────────

    def summary(self) -> dict:
        """Краткая сводка по всем субъектам."""
        with self._lock:
            subjects = {}
            for sid, rec in self._records.items():
                score = rec.apply_decay()
                subjects[sid] = {
                    'score': round(score, 3),
                    'category': rec.category.value,
                    'events': rec.event_count,
                    'level': TrustGate.describe_level(score),
                }
            return subjects
