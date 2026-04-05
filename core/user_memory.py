# User Memory Store — персональная память по пользователям
# Архитектура: Partner Core → профиль пользователя, стиль, цели, история решений
# Меморандум (Часть 5.1): "Агент постепенно запоминает мой стиль, предпочтения,
#   какие форматы помогают, какие действия раздражают"
# Меморандум (Часть 5.4): "Мы храним проекты, решения, выводы, шаблоны"
#
# Для мультитенанта (Tenant Isolation):
#   - отдельная память на каждого пользователя
#   - отдельные цели
#   - отдельные предпочтения
#   - отдельная история решений

from __future__ import annotations

import os
import json
import time
import threading
from dataclasses import dataclass, field


@dataclass
class UserPreferences:
    """Предпочтения пользователя — что агент узнал о нём."""
    communication_style: str = 'balanced'     # formal / friendly / technical / concise / detailed
    detail_level: str = 'medium'              # low / medium / high
    language: str = 'ru'                      # основной язык
    timezone: str = 'UTC'
    working_hours: tuple[int, int] = (9, 22)  # рабочие часы
    dnd_hours: tuple[int, int] = (23, 7)      # тихие часы
    initiative_tolerance: str = 'medium'      # low / medium / high — как часто проявлять инициативу
    formats_preferred: list[str] = field(default_factory=lambda: ['markdown', 'bullet_points'])
    annoyances: list[str] = field(default_factory=list)   # что раздражает
    strengths_valued: list[str] = field(default_factory=list)  # что ценит

    def to_dict(self) -> dict:
        return {
            'communication_style': self.communication_style,
            'detail_level': self.detail_level,
            'language': self.language,
            'timezone': self.timezone,
            'working_hours': list(self.working_hours),
            'dnd_hours': list(self.dnd_hours),
            'initiative_tolerance': self.initiative_tolerance,
            'formats_preferred': self.formats_preferred,
            'annoyances': self.annoyances,
            'strengths_valued': self.strengths_valued,
        }

    @classmethod
    def from_dict(cls, d: dict) -> UserPreferences:
        return cls(
            communication_style=d.get('communication_style', 'balanced'),
            detail_level=d.get('detail_level', 'medium'),
            language=d.get('language', 'ru'),
            timezone=d.get('timezone', 'UTC'),
            working_hours=tuple(d.get('working_hours', [9, 22])),
            dnd_hours=tuple(d.get('dnd_hours', [23, 7])),
            initiative_tolerance=d.get('initiative_tolerance', 'medium'),
            formats_preferred=d.get('formats_preferred', ['markdown', 'bullet_points']),
            annoyances=d.get('annoyances', []),
            strengths_valued=d.get('strengths_valued', []),
        )


@dataclass
class DecisionRecord:
    """Запись решения — что агент предложил, что человек выбрал."""
    decision_id: str
    timestamp: float
    context: str              # контекст: в чём вопрос
    options: list[str]        # варианты, которые рассматривались
    chosen: str               # что было выбрано
    outcome: str = ''         # результат (если известен)
    user_feedback: str = ''   # обратная связь пользователя
    reasoning: str = ''       # почему выбрано именно это

    def to_dict(self) -> dict:
        return {
            'decision_id': self.decision_id,
            'timestamp': self.timestamp,
            'context': self.context,
            'options': self.options,
            'chosen': self.chosen,
            'outcome': self.outcome,
            'user_feedback': self.user_feedback,
            'reasoning': self.reasoning,
        }

    @classmethod
    def from_dict(cls, d: dict) -> DecisionRecord:
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


class UserProfile:
    """
    Полный профиль одного пользователя.

    Хранит:
        - идентификация (id, имя, контакт)
        - стиль общения и предпочтения
        - персональные цели
        - история решений
        - контекст взаимодействия (что агент узнал)
        - заметки агента о пользователе
    """

    MAX_DECISIONS = 500
    MAX_GOALS = 100
    MAX_INSIGHTS = 200
    MAX_CONTEXT_ITEMS = 300

    def __init__(self, user_id: str, name: str = ''):
        self.user_id: str = user_id
        self.name: str = name
        self.created_at: float = time.time()
        self.last_interaction: float = time.time()
        self.interaction_count: int = 0

        # Предпочтения
        self.preferences = UserPreferences()

        # Персональные цели пользователя
        self.goals: list[dict] = []
        # Формат: {"goal": str, "priority": str, "status": str, "created": float}

        # История решений
        self.decisions: list[dict] = []

        # Инсайты: что агент узнал о пользователе
        self.insights: list[dict] = []
        # Формат: {"text": str, "category": str, "confidence": float, "time": float}

        # Контекст: текущие темы, проекты, задачи
        self.active_context: list[dict] = []
        # Формат: {"topic": str, "details": str, "since": float, "priority": str}

        # Заметки агента (свободная форма)
        self.notes: list[str] = []

    def record_interaction(self):
        """Фиксирует факт взаимодействия."""
        self.interaction_count += 1
        self.last_interaction = time.time()

    def add_goal(self, goal: str, priority: str = 'medium') -> dict:
        """Добавляет персональную цель пользователя."""
        entry = {
            'goal': goal,
            'priority': priority,
            'status': 'active',
            'created': time.time(),
        }
        self.goals.append(entry)
        if len(self.goals) > self.MAX_GOALS:
            # Удаляем старые завершённые
            self.goals = [g for g in self.goals if g['status'] == 'active'] + \
                         [g for g in self.goals if g['status'] != 'active'][-20:]
        return entry

    def add_decision(self, context: str, options: list[str],
                     chosen: str, reasoning: str = '') -> dict:
        """Записывает решение."""
        import uuid
        entry = DecisionRecord(
            decision_id=f"dec_{uuid.uuid4().hex[:8]}",
            timestamp=time.time(),
            context=context,
            options=options,
            chosen=chosen,
            reasoning=reasoning,
        ).to_dict()
        self.decisions.append(entry)
        if len(self.decisions) > self.MAX_DECISIONS:
            self.decisions = self.decisions[-self.MAX_DECISIONS:]
        return entry

    def add_insight(self, text: str, category: str = 'general',
                    confidence: float = 0.7):
        """Добавляет новый инсайт о пользователе."""
        self.insights.append({
            'text': text,
            'category': category,
            'confidence': round(confidence, 3),
            'time': time.time(),
        })
        if len(self.insights) > self.MAX_INSIGHTS:
            self.insights = self.insights[-self.MAX_INSIGHTS:]

    def get_context_summary(self) -> str:
        """Краткое описание пользователя для LLM-контекста."""
        parts = [f"Пользователь: {self.name or self.user_id}"]
        if self.preferences.communication_style != 'balanced':
            parts.append(f"Стиль: {self.preferences.communication_style}")
        if self.preferences.language != 'ru':
            parts.append(f"Язык: {self.preferences.language}")
        if self.goals:
            active = [g['goal'] for g in self.goals if g['status'] == 'active'][:3]
            if active:
                parts.append(f"Цели: {'; '.join(active)}")
        if self.insights:
            recent = [i['text'] for i in self.insights[-3:]]
            parts.append(f"Контекст: {'; '.join(recent)}")
        return '\n'.join(parts)

    # ── Serialization ─────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            'user_id': self.user_id,
            'name': self.name,
            'created_at': self.created_at,
            'last_interaction': self.last_interaction,
            'interaction_count': self.interaction_count,
            'preferences': self.preferences.to_dict(),
            'goals': self.goals,
            'decisions': self.decisions[-self.MAX_DECISIONS:],
            'insights': self.insights[-self.MAX_INSIGHTS:],
            'active_context': self.active_context,
            'notes': self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> UserProfile:
        p = cls(user_id=d['user_id'], name=d.get('name', ''))
        p.created_at = d.get('created_at', time.time())
        p.last_interaction = d.get('last_interaction', time.time())
        p.interaction_count = d.get('interaction_count', 0)
        p.preferences = UserPreferences.from_dict(d.get('preferences', {}))
        p.goals = d.get('goals', [])
        p.decisions = d.get('decisions', [])
        p.insights = d.get('insights', [])
        p.active_context = d.get('active_context', [])
        p.notes = d.get('notes', [])
        return p


# ══════════════════════════════════════════════════════════════════════════════
# User Memory Store — центральное хранилище per-user памяти
# ══════════════════════════════════════════════════════════════════════════════

class UserMemoryStore:
    """
    Хранилище персональной памяти по пользователям.

    Каждый пользователь получает:
        - собственный UserProfile
        - изолированную папку для данных
        - собственные цели, предпочтения, историю решений

    Для мультитенанта: каждый user_id → отдельная папка.

    Персистентность:
        - {data_dir}/users/{user_id}/profile.json
        - Автоматическое сохранение при изменениях
    """

    def __init__(self, data_dir: str):
        self._data_dir = data_dir
        self._users_dir = os.path.join(data_dir, 'users')
        self._profiles: dict[str, UserProfile] = {}
        self._lock = threading.Lock()
        os.makedirs(self._users_dir, exist_ok=True)
        self._load_all()

    def _user_dir(self, user_id: str) -> str:
        # Безопасное имя папки
        safe_id = ''.join(c if c.isalnum() or c in '-_' else '_' for c in str(user_id))
        return os.path.join(self._users_dir, safe_id)

    def _profile_path(self, user_id: str) -> str:
        return os.path.join(self._user_dir(user_id), 'profile.json')

    # ── Load / Save ───────────────────────────────────────────────────────

    def _load_all(self):
        """Загружает все профили при старте."""
        if not os.path.isdir(self._users_dir):
            return
        for entry in os.listdir(self._users_dir):
            profile_path = os.path.join(self._users_dir, entry, 'profile.json')
            if os.path.isfile(profile_path):
                try:
                    with open(profile_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    profile = UserProfile.from_dict(data)
                    self._profiles[profile.user_id] = profile
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue

    def _save_profile(self, user_id: str):
        """Сохраняет профиль на диск."""
        profile = self._profiles.get(user_id)
        if not profile:
            return
        user_dir = self._user_dir(user_id)
        os.makedirs(user_dir, exist_ok=True)
        path = self._profile_path(user_id)
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(profile.to_dict(), f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    # ── Public API ────────────────────────────────────────────────────────

    def get_or_create(self, user_id: str, name: str = '') -> UserProfile:
        """Возвращает профиль пользователя, создаёт если не существует."""
        with self._lock:
            if user_id not in self._profiles:
                self._profiles[user_id] = UserProfile(user_id, name)
                self._save_profile(user_id)
            profile = self._profiles[user_id]
            if name and not profile.name:
                profile.name = name
            return profile

    def get(self, user_id: str) -> UserProfile | None:
        """Возвращает профиль или None."""
        return self._profiles.get(user_id)

    def record_interaction(self, user_id: str, name: str = ''):
        """Фиксирует взаимодействие и автосохраняет."""
        profile = self.get_or_create(user_id, name)
        profile.record_interaction()
        self._save_profile(user_id)

    def add_goal(self, user_id: str, goal: str, priority: str = 'medium') -> dict:
        profile = self.get_or_create(user_id)
        entry = profile.add_goal(goal, priority)
        self._save_profile(user_id)
        return entry

    def add_decision(self, user_id: str, context: str,
                     options: list[str], chosen: str,
                     reasoning: str = '') -> dict:
        profile = self.get_or_create(user_id)
        entry = profile.add_decision(context, options, chosen, reasoning)
        self._save_profile(user_id)
        return entry

    def add_insight(self, user_id: str, text: str,
                    category: str = 'general', confidence: float = 0.7):
        profile = self.get_or_create(user_id)
        profile.add_insight(text, category, confidence)
        self._save_profile(user_id)

    def update_preferences(self, user_id: str, **kwargs):
        """Обновляет предпочтения пользователя."""
        profile = self.get_or_create(user_id)
        for key, value in kwargs.items():
            if hasattr(profile.preferences, key):
                setattr(profile.preferences, key, value)
        self._save_profile(user_id)

    def get_context_for_llm(self, user_id: str) -> str:
        """Возвращает контекст пользователя для LLM-промпта."""
        profile = self._profiles.get(user_id)
        if not profile:
            return ''
        return profile.get_context_summary()

    def list_users(self) -> list[dict]:
        """Краткий список всех пользователей."""
        return [
            {
                'user_id': p.user_id,
                'name': p.name,
                'interactions': p.interaction_count,
                'last_seen': p.last_interaction,
                'goals_count': len([g for g in p.goals if g.get('status') == 'active']),
            }
            for p in self._profiles.values()
        ]

    def save_all(self):
        """Сохраняет все профили (для autosave)."""
        with self._lock:
            for uid in self._profiles:
                self._save_profile(uid)

    # ── User workspace directory ──────────────────────────────────────────

    def get_workspace_dir(self, user_id: str) -> str:
        """Возвращает путь к рабочей папке пользователя (для Tenant Isolation)."""
        d = os.path.join(self._user_dir(user_id), 'workspace')
        os.makedirs(d, exist_ok=True)
        return d

    def get_knowledge_dir(self, user_id: str) -> str:
        """Возвращает путь к папке знаний пользователя."""
        d = os.path.join(self._user_dir(user_id), 'knowledge')
        os.makedirs(d, exist_ok=True)
        return d
