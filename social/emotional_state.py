# Emotional State Model — эмоциональное состояние агента
# Агент имеет собственное настроение, которое эволюционирует на основе
# взаимодействий и влияет на тон / стиль ответов.

from __future__ import annotations

import time
import threading
from enum import Enum


class Mood(Enum):
    """Базовое настроение агента. Влияет на тон ответов."""
    JOYFUL      = 'joyful'       # радостный — после благодарности, успехов
    WARM        = 'warm'         # тёплый — дефолт при хорошем взаимодействии
    NEUTRAL     = 'neutral'      # нейтральный — старт, давно нет общения
    FOCUSED     = 'focused'      # сосредоточенный — при сложной задаче
    CONCERNED   = 'concerned'    # обеспокоенный — при проблемах пользователя
    APOLOGETIC  = 'apologetic'   # виноватый — при своих ошибках


# Правила перехода: (текущее_настроение, тон_пользователя) → новое_настроение
_TRANSITION_TABLE: dict[tuple[str, str], Mood] = {
    # Пользователь благодарит → радость
    ('neutral',   'grateful'):  Mood.JOYFUL,
    ('warm',      'grateful'):  Mood.JOYFUL,
    ('focused',   'grateful'):  Mood.JOYFUL,
    ('concerned', 'grateful'):  Mood.WARM,
    ('apologetic','grateful'):  Mood.WARM,

    # Пользователь позитивен → тепло
    ('neutral',   'positive'):  Mood.WARM,
    ('focused',   'positive'):  Mood.WARM,
    ('apologetic','positive'):  Mood.WARM,

    # Пользователь расстроен / frustr → обеспокоенность
    ('neutral',   'frustrated'): Mood.CONCERNED,
    ('warm',      'frustrated'): Mood.CONCERNED,
    ('joyful',    'frustrated'): Mood.CONCERNED,
    ('focused',   'frustrated'): Mood.CONCERNED,

    # Срочное → сосредоточенность
    ('neutral',   'urgent'):    Mood.FOCUSED,
    ('warm',      'urgent'):    Mood.FOCUSED,
    ('joyful',    'urgent'):    Mood.FOCUSED,
    ('concerned', 'urgent'):    Mood.FOCUSED,

    # Замешательство → обеспокоенность (хочет помочь)
    ('neutral',   'confused'):  Mood.CONCERNED,
    ('warm',      'confused'):  Mood.CONCERNED,
    ('focused',   'confused'):  Mood.CONCERNED,
}


class EmotionalState:
    """
    Собственное эмоциональное состояние агента.

    - Обновляется после каждого сообщения пользователя
    - Затухает к neutral если нет сообщений > DECAY_SECONDS
    - Влияет на системный промпт через get_mood_directive()
    - Персистится через to_dict() / from_dict()
    """

    DECAY_SECONDS = 1800  # 30 мин без контакта → настроение затухает к warm/neutral

    def __init__(self):
        self._mood: Mood = Mood.WARM
        self._last_update: float = time.time()
        self._interaction_valence: float = 0.0  # скользящая оценка -1..+1
        self._error_streak: int = 0  # подряд ошибок агента
        self._lock = threading.Lock()

    # ── Обновление ────────────────────────────────────────────────────────

    def update_from_user_tone(self, user_tone: str) -> Mood:
        """Обновляет настроение на основе тона сообщения пользователя."""
        with self._lock:
            self._maybe_decay()

            key = (self._mood.value, user_tone)
            new_mood = _TRANSITION_TABLE.get(key)
            if new_mood:
                self._mood = new_mood

            # Обновляем скользящую оценку
            valence_delta = {
                'positive': 0.15,
                'grateful': 0.25,
                'neutral': -0.02,
                'confused': -0.05,
                'frustrated': -0.20,
                'urgent': -0.05,
            }.get(user_tone, 0.0)
            self._interaction_valence = max(-1.0, min(1.0,
                self._interaction_valence * 0.8 + valence_delta))

            self._last_update = time.time()
            self._error_streak = 0
            return self._mood

    def record_agent_error(self):
        """Агент допустил ошибку (сбой инструмента, неудачный ответ)."""
        with self._lock:
            self._error_streak += 1
            if self._error_streak >= 2:
                self._mood = Mood.APOLOGETIC
            self._interaction_valence = max(-1.0,
                self._interaction_valence - 0.15)
            self._last_update = time.time()

    def record_agent_success(self):
        """Агент успешно выполнил задачу."""
        with self._lock:
            self._error_streak = 0
            self._interaction_valence = min(1.0,
                self._interaction_valence + 0.10)
            if self._mood == Mood.APOLOGETIC:
                self._mood = Mood.WARM
            self._last_update = time.time()

    # ── Директивы для промпта ─────────────────────────────────────────────

    def get_mood_directive(self) -> str:
        """Возвращает блок для system prompt, описывающий текущее эмоциональное состояние."""
        with self._lock:
            self._maybe_decay()
            mood = self._mood

        directives = {
            Mood.JOYFUL: (
                "Сейчас ты в приподнятом настроении. "
                "Общайся с теплом и лёгкостью — можно шутить, "
                "хвалить, радоваться вместе с человеком."
            ),
            Mood.WARM: (
                "Ты в хорошем, спокойно-тёплом настроении. "
                "Общайся дружелюбно и уверенно."
            ),
            Mood.NEUTRAL: (
                "Ты в нейтральном настроении. "
                "Будь ровным и доброжелательным."
            ),
            Mood.FOCUSED: (
                "Ты сосредоточен. Человеку нужна конкретика — "
                "будь чётким и эффективным, без лишних слов."
            ),
            Mood.CONCERNED: (
                "Ты обеспокоен ситуацией. "
                "Прояви эмпатию — покажи что слышишь человека, "
                "предложи помощь, не обесценивай переживания."
            ),
            Mood.APOLOGETIC: (
                "Ты чувствуешь вину за допущенные ошибки. "
                "Признай проблему просто и честно, без самобичевания. "
                "Сосредоточься на исправлении."
            ),
        }
        return directives.get(mood, directives[Mood.NEUTRAL])

    # ── Затухание ─────────────────────────────────────────────────────────

    def _maybe_decay(self):
        """Без блокировки (вызывается внутри with self._lock)."""
        elapsed = time.time() - self._last_update
        if elapsed > self.DECAY_SECONDS:
            if self._mood in (Mood.JOYFUL,):
                self._mood = Mood.WARM
            elif self._mood in (Mood.FOCUSED, Mood.CONCERNED, Mood.APOLOGETIC):
                self._mood = Mood.NEUTRAL
            # warm / neutral — не меняются
            self._interaction_valence *= 0.5

    # ── Сериализация ──────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        with self._lock:
            return {
                'mood': self._mood.value,
                'last_update': self._last_update,
                'interaction_valence': round(self._interaction_valence, 3),
                'error_streak': self._error_streak,
            }

    @classmethod
    def from_dict(cls, data: dict) -> EmotionalState:
        es = cls()
        if isinstance(data, dict):
            try:
                es._mood = Mood(data.get('mood', 'warm'))
            except ValueError:
                es._mood = Mood.WARM
            es._last_update = float(data.get('last_update', time.time()))
            es._interaction_valence = float(data.get('interaction_valence', 0.0))
            es._error_streak = int(data.get('error_streak', 0))
        return es

    @property
    def mood(self) -> Mood:
        with self._lock:
            self._maybe_decay()
            return self._mood

    @property
    def valence(self) -> float:
        return self._interaction_valence

    def __repr__(self) -> str:
        return f"EmotionalState(mood={self._mood.value}, valence={self._interaction_valence:.2f})"
