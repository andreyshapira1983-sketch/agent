# Experience Replay System (повторный анализ прошлого опыта) — Слой 36
# Архитектура автономного AI-агента
# Повторный просмотр эпизодов, извлечение паттернов, улучшение на основе прошлого.


import time
import random
from collections import deque
from typing import Optional


class Episode:
    """Один эпизод опыта агента."""

    def __init__(self, episode_id: str, goal: str, actions: list,
                 outcome: str, success: bool, context: Optional[dict] = None):
        self.episode_id = episode_id
        self.goal = goal
        self.actions = actions          # список действий, предпринятых агентом
        self.outcome = outcome          # результат / итог
        self.success = success
        self.context = context or {}
        self.created_at = time.time()
        self.replayed_count = 0
        self.lessons: list[str] = []    # уроки, извлечённые при повторном анализе

    def to_dict(self):
        return {
            'episode_id': self.episode_id,
            'goal': self.goal,
            'actions': self.actions,
            'outcome': self.outcome,
            'success': self.success,
            'replayed_count': self.replayed_count,
            'lessons': self.lessons,
        }


class ExperienceReplay:
    """
    Experience Replay System — Слой 36.

    Функции:
        - хранение эпизодов (буфер опыта)
        - случайная или приоритетная выборка эпизодов для повторного анализа
        - извлечение паттернов из прошлых успехов и ошибок
        - передача уроков в Self-Improvement и Knowledge System
        - периодический автоматический replay (в цикле Autonomous Loop)

    Используется:
        - Autonomous Loop (Слой 20)     — периодический replay
        - Self-Improvement (Слой 12)    — применение уроков
        - Knowledge System (Слой 2)     — сохранение паттернов
        - Reflection System (Слой 10)   — пополнение буфера
    """

    def __init__(self, buffer_size: int = 1000,
                 knowledge_system=None, cognitive_core=None,
                 self_improvement=None, reflection=None,
                 monitoring=None):
        self.knowledge = knowledge_system
        self.cognitive_core = cognitive_core
        self.self_improvement = self_improvement
        self.reflection = reflection
        self.monitoring = monitoring

        self._buffer: deque[Episode] = deque(maxlen=buffer_size)
        self._index: dict[str, Episode] = {}     # episode_id → Episode
        self._patterns: list[dict] = []           # обнаруженные паттерны
        self._replay_stats = {'total': 0, 'patterns_found': 0}

    @property
    def size(self) -> int:
        """Количество эпизодов в буфере."""
        return len(self._buffer)

    # ── Добавление опыта ──────────────────────────────────────────────────────

    def add(self, goal: str, actions: list, outcome: str,
            success: bool, context: Optional[dict] = None) -> Episode:
        """Добавляет новый эпизод в буфер опыта."""
        import uuid
        ep = Episode(
            episode_id=str(uuid.uuid4())[:8],
            goal=goal,
            actions=actions,
            outcome=outcome,
            success=success,
            context=context or {},
        )
        self._buffer.append(ep)
        self._index[ep.episode_id] = ep
        self._log(f"Эпизод добавлен: '{ep.episode_id}' (успех={success})")
        return ep

    def add_from_knowledge(self, n: int = 50) -> int:
        """
        Загружает эпизоды из Knowledge System (эпизодическая память).
        Возвращает количество загруженных эпизодов.
        """
        if not self.knowledge:
            return 0
        loaded = 0
        episodes = self.knowledge.get_episodic_memory(None) or []
        for ep_dict in episodes[:n]:
            if isinstance(ep_dict, dict) and 'goal' in ep_dict:
                self.add(
                    goal=ep_dict.get('goal', ''),
                    actions=ep_dict.get('actions', []),
                    outcome=ep_dict.get('outcome', ''),
                    success=ep_dict.get('success', False),
                    context=ep_dict.get('context', {}),
                )
                loaded += 1
        return loaded

    # ── Выборка эпизодов ──────────────────────────────────────────────────────

    def sample_random(self, n: int = 10) -> list[Episode]:
        """Случайная выборка n эпизодов из буфера."""
        buf = list(self._buffer)
        if not buf:
            return []
        return random.sample(buf, min(n, len(buf)))

    def sample_failures(self, n: int = 10) -> list[Episode]:
        """Выборка n эпизодов с неуспешным результатом (для анализа ошибок)."""
        failures = [ep for ep in self._buffer if not ep.success]
        return random.sample(failures, min(n, len(failures))) if failures else []

    def sample_successes(self, n: int = 10) -> list[Episode]:
        """Выборка n успешных эпизодов (для усиления паттернов)."""
        successes = [ep for ep in self._buffer if ep.success]
        return random.sample(successes, min(n, len(successes))) if successes else []

    def sample_by_goal(self, goal_keyword: str, n: int = 5) -> list[Episode]:
        """Выборка эпизодов, связанных с конкретной целью/задачей."""
        matching = [ep for ep in self._buffer
                    if goal_keyword.lower() in ep.goal.lower()]
        return matching[:n]

    # ── Повторный анализ ──────────────────────────────────────────────────────

    def replay(self, episodes: Optional[list[Episode]] = None,
               n: int = 10, focus: str = 'mixed') -> list[dict]:
        """
        Повторный анализ эпизодов через Cognitive Core.
        Извлекает уроки и паттерны.

        Args:
            episodes — конкретные эпизоды (если None — выбираются автоматически)
            n        — количество эпизодов для анализа
            focus    — 'failures', 'successes', 'mixed'

        Returns:
            список словарей с извлечёнными уроками
        """
        if episodes is None:
            if focus == 'failures':
                episodes = self.sample_failures(n)
            elif focus == 'successes':
                episodes = self.sample_successes(n)
            else:
                episodes = self.sample_random(n)

        if not episodes:
            self._log("Буфер пуст — нечего анализировать")
            return []

        lessons_list = []
        for ep in episodes:
            lesson = self._analyse_episode(ep)
            if lesson:
                ep.lessons.append(lesson.get('lesson', ''))
                ep.replayed_count += 1
                lessons_list.append(lesson)

        self._replay_stats['total'] += len(episodes)
        self._log(f"Replay: обработано {len(episodes)} эпизодов, "
                  f"извлечено {len(lessons_list)} уроков")
        return lessons_list

    def replay_all(self, focus: str = 'failures') -> list[dict]:
        """Запускает replay для всего буфера."""
        return self.replay(list(self._buffer), focus=focus)

    def _analyse_episode(self, ep: Episode) -> dict | None:
        """Анализирует один эпизод и извлекает урок."""
        if not self.cognitive_core:
            return {
                'episode_id': ep.episode_id,
                'lesson': f"Цель '{ep.goal}' — результат: {ep.outcome}",
                'pattern': None,
            }

        prompt = (
            f"Проанализируй эпизод работы автономного агента:\n\n"
            f"Цель: {ep.goal}\n"
            f"Действия: {ep.actions}\n"
            f"Итог: {ep.outcome}\n"
            f"Успех: {ep.success}\n\n"
            f"Извлеки:\n"
            f"УРОК: <что стоит запомнить на будущее>\n"
            f"ПАТТЕРН: <паттерн поведения или ошибки>\n"
            f"РЕКОМЕНДАЦИЯ: <что изменить в стратегии>"
        )
        raw = str(self.cognitive_core.reasoning(prompt))

        import re
        lesson_m = re.search(r'УРОК[:\s]+(.+)', raw, re.IGNORECASE)
        pattern_m = re.search(r'ПАТТЕРН[:\s]+(.+)', raw, re.IGNORECASE)
        rec_m = re.search(r'РЕКОМЕНДАЦИЯ[:\s]+(.+)', raw, re.IGNORECASE)

        return {
            'episode_id': ep.episode_id,
            'lesson': lesson_m.group(1).strip() if lesson_m else raw[:200],
            'pattern': pattern_m.group(1).strip() if pattern_m else None,
            'recommendation': rec_m.group(1).strip() if rec_m else None,
            'success': ep.success,
        }

    # ── Извлечение паттернов ──────────────────────────────────────────────────

    def find_patterns(self) -> list[dict]:
        """
        Анализирует весь буфер и находит повторяющиеся паттерны.
        Сохраняет их в Knowledge System.
        """
        if not self.cognitive_core or len(self._buffer) < 3:
            return []

        sample = self.sample_random(min(30, len(self._buffer)))
        episodes_text = '\n'.join(
            f"[{ep.episode_id}] Цель={ep.goal}, Успех={ep.success}, Итог={ep.outcome}"
            for ep in sample
        )

        raw = str(self.cognitive_core.reasoning(
            f"Проанализируй эти эпизоды работы агента и найди повторяющиеся паттерны "
            f"(успеха и неудачи). Укажи каждый паттерн в формате ПАТТЕРН: <описание>.\n\n"
            f"{episodes_text}"
        ))

        import re
        found = re.findall(r'ПАТТЕРН[:\s]+(.+)', raw, re.IGNORECASE)
        patterns = [{'pattern': p.strip(), 'found_at': time.time()} for p in found]
        self._patterns.extend(patterns)
        self._replay_stats['patterns_found'] += len(patterns)

        # Сохраняем в Knowledge System
        if self.knowledge and patterns:
            self.knowledge.store_long_term(
                f'experience_patterns_{int(time.time())}',
                str(patterns),
                source='learning', trust=0.6,
            )

        self._log(f"Найдено паттернов: {len(patterns)}")
        return patterns

    def apply_lessons(self, lessons: list[dict]):
        """
        Передаёт уроки: инсайты → Reflection, рекомендации → SelfImprovement.propose().
        """
        for lesson in lessons:
            lesson_text = lesson.get('lesson', '')
            recommendation = lesson.get('recommendation', '')

            # Инсайты → Reflection (у которого есть add_insight)
            if self.reflection and lesson_text:
                self.reflection.add_insight(lesson_text)

            # Рекомендации → SelfImprovement как предложения
            if self.self_improvement and recommendation:
                self.self_improvement.propose(
                    area='experience_replay',
                    current_behavior=lesson_text[:200],
                    proposed_change=recommendation[:300],
                    rationale=f"Из опыта: эпизод {lesson.get('episode_id', '?')}",
                    priority=2,
                )

    # ── Статистика и реестр ───────────────────────────────────────────────────

    def summary(self) -> dict:
        buf = list(self._buffer)
        return {
            'buffer_size': len(buf),
            'success_rate': round(
                sum(1 for ep in buf if ep.success) / max(1, len(buf)), 2
            ),
            'total_replayed': self._replay_stats['total'],
            'patterns_found': self._replay_stats['patterns_found'],
            'patterns_stored': len(self._patterns),
        }

    def get_patterns(self) -> list[dict]:
        return list(self._patterns)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _log(self, message: str):
        if self.monitoring:
            self.monitoring.info(message, source='experience_replay')
        else:
            print(f"[ExperienceReplay] {message}")
