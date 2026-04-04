# Experience Replay System (повторный анализ прошлого опыта) — Слой 36
# Архитектура автономного AI-агента
# Повторный просмотр эпизодов, извлечение паттернов, улучшение на основе прошлого.


import hashlib
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
        # P3: scoring
        self.priority: float = 1.0      # приоритет для replay (выше = чаще выбирается)
        self.fingerprint: str = self._compute_fingerprint()

    def _compute_fingerprint(self) -> str:
        """Детерминированный хэш эпизода для dedup."""
        key = f"{self.goal}|{'|'.join(str(a) for a in self.actions[:5])}|{self.success}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def to_dict(self):
        return {
            'episode_id': self.episode_id,
            'goal': self.goal,
            'actions': self.actions,
            'outcome': self.outcome,
            'success': self.success,
            'replayed_count': self.replayed_count,
            'lessons': self.lessons,
            'priority': round(self.priority, 3),
            'fingerprint': self.fingerprint,
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
        self._fingerprints: set[str] = set()     # P3: dedup по fingerprint
        self._patterns: list[dict] = []           # обнаруженные паттерны
        self._replay_stats = {'total': 0, 'patterns_found': 0, 'deduped': 0}

    @property
    def size(self) -> int:
        """Количество эпизодов в буфере."""
        return len(self._buffer)

    def recent_episodes(self, n: int = 10) -> list:
        """Public accessor: последние N эпизодов из буфера."""
        return list(self._buffer)[-n:]

    def get_episode(self, episode_id: str):
        """Public accessor: возвращает Episode по ID или None."""
        return self._index.get(episode_id)

    # ── Добавление опыта ──────────────────────────────────────────────────────

    def add(self, goal: str, actions: list, outcome: str,
            success: bool, context: Optional[dict] = None) -> Optional[Episode]:
        """Добавляет новый эпизод в буфер опыта.

        P3: dedup по fingerprint + автоматический priority scoring.
        Возвращает Episode или None если дубликат.
        """
        import uuid
        ep = Episode(
            episode_id=str(uuid.uuid4())[:8],
            goal=goal,
            actions=actions,
            outcome=outcome,
            success=success,
            context=context or {},
        )
        # Dedup: если точная комбинация (goal+actions+success) уже есть — пропуск
        if ep.fingerprint in self._fingerprints:
            self._replay_stats['deduped'] = self._replay_stats.get('deduped', 0) + 1
            self._log(f"Эпизод дедуплицирован (fingerprint={ep.fingerprint[:8]})")
            return None
        # Priority scoring
        ep.priority = self._compute_priority(ep)
        self._buffer.append(ep)
        self._index[ep.episode_id] = ep
        self._fingerprints.add(ep.fingerprint)
        self._log(f"Эпизод добавлен: '{ep.episode_id}' (успех={success}, pri={ep.priority:.2f})")
        return ep

    @staticmethod
    def _compute_priority(ep: Episode) -> float:
        """Вычисляет приоритет эпизода для replay.

        Высокий приоритет:
          - провалы (учимся на ошибках)
          - недавние эпизоды
          - эпизоды с разнообразными действиями (больше инфы)
          - ещё не анализированные
        """
        score = 1.0
        # Провалы важнее для обучения
        if not ep.success:
            score += 2.0
        # Разнообразие действий (log масштаб)
        n_actions = len(ep.actions) if ep.actions else 0
        if n_actions > 1:
            import math
            score += 0.5 * math.log1p(n_actions)
        # Ещё не анализированные — приоритетнее
        if ep.replayed_count == 0:
            score += 1.0
        else:
            score -= 0.3 * min(ep.replayed_count, 5)
        return max(0.1, score)

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
        """Приоритетная выборка n эпизодов из буфера (P3: weighted by priority)."""
        buf = list(self._buffer)
        if not buf:
            return []
        if len(buf) <= n:
            return list(buf)
        # Weighted sampling по priority
        weights = [ep.priority for ep in buf]
        total_w = sum(weights)
        if total_w <= 0:
            return random.sample(buf, n)
        # random.choices с весами (с заменой), потом deduplicate
        chosen = random.choices(buf, weights=weights, k=n * 2)
        seen: set[str] = set()
        result: list[Episode] = []
        for ep in chosen:
            if ep.episode_id not in seen:
                seen.add(ep.episode_id)
                result.append(ep)
                if len(result) >= n:
                    break
        return result

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
                lesson_text = lesson.get('lesson', '')
                # P3: dedup уроков — не записываем точный дубликат
                if lesson_text and lesson_text not in ep.lessons:
                    ep.lessons.append(lesson_text)
                ep.replayed_count += 1
                # P3: decay priority после replay — не анализировать одно и то же
                ep.priority = max(0.1, ep.priority * 0.6)
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
            'deduped': self._replay_stats.get('deduped', 0),
            'avg_priority': round(
                sum(ep.priority for ep in buf) / max(1, len(buf)), 2
            ),
        }

    def get_patterns(self) -> list[dict]:
        return list(self._patterns)

    # ── P3: Auto-skill generation ─────────────────────────────────────────────

    def extract_repeating_chains(
        self, min_occurrences: int = 3, min_actions: int = 2,
    ) -> list[dict]:
        """Находит повторяющиеся успешные цепочки действий → кандидаты в навыки.

        Алгоритм:
          1. Берём все успешные эпизоды.
          2. Нормализуем цепочку actions → «action signature» (тип + ключевые слова).
          3. Группируем по signature.
          4. Если signature встречается ≥ min_occurrences → кандидат.

        Возвращает список кандидатов:
            [{'signature': str, 'goal_pattern': str, 'steps': list, 'count': int}]
        """
        from collections import Counter

        successes = [ep for ep in self._buffer if ep.success and len(ep.actions) >= min_actions]
        if len(successes) < min_occurrences:
            return []

        # Строим signatures
        sig_map: dict[str, list[Episode]] = {}
        for ep in successes:
            sig = self._chain_signature(ep.actions)
            sig_map.setdefault(sig, []).append(ep)

        candidates = []
        for sig, eps in sig_map.items():
            if len(eps) >= min_occurrences:
                # goal_pattern = общие слова из целей этих эпизодов
                goal_tokens = self._common_tokens([ep.goal for ep in eps])
                # steps = actions из наиболее репрезентативного (последнего) эпизода
                representative = eps[-1]
                candidates.append({
                    'signature': sig,
                    'goal_pattern': ' '.join(sorted(goal_tokens)[:10]),
                    'trigger_keywords': sorted(goal_tokens)[:8],
                    'steps': list(representative.actions),
                    'count': len(eps),
                    'avg_actions': sum(len(ep.actions) for ep in eps) / len(eps),
                })

        candidates.sort(key=lambda c: -c['count'])
        self._log(f"Auto-skill candidates: {len(candidates)} повторяющихся цепочек")
        return candidates

    @staticmethod
    def _chain_signature(actions: list) -> str:
        """Нормализует цепочку действий в строку-signature для группировки.

        Из '[bash] OK | pip install requests' → 'bash:pip_install'
        Из 'write_file path=x.py' → 'write:x.py'
        """
        parts = []
        for a in actions[:10]:  # лимит на глубину цепочки
            a_str = str(a).strip().lower()
            # Извлекаем тип инструмента
            import re
            m = re.match(r'\[(\w+)\]', a_str)
            tool = m.group(1) if m else 'unknown'
            # Ключевые слова из действия (первые 3 значимых слова)
            words = re.findall(r'[a-zа-яё0-9_]{3,}', a_str)
            key = '_'.join(words[:3]) if words else 'noop'
            parts.append(f"{tool}:{key}")
        return '|'.join(parts)

    @staticmethod
    def _common_tokens(texts: list[str], min_freq_ratio: float = 0.5) -> set[str]:
        """Извлекает токены, встречающиеся в ≥ min_freq_ratio текстов."""
        import re
        token_re = re.compile(r'[a-zа-яё0-9_]{3,}')
        n = len(texts)
        if n == 0:
            return set()
        counter: dict[str, int] = {}
        for text in texts:
            unique_tokens = set(token_re.findall(text.lower()))
            for t in unique_tokens:
                counter[t] = counter.get(t, 0) + 1
        threshold = max(2, int(n * min_freq_ratio))
        return {t for t, c in counter.items() if c >= threshold}

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _log(self, message: str):
        if self.monitoring:
            self.monitoring.info(message, source='experience_replay')
        else:
            print(f"[ExperienceReplay] {message}")

    def export_state(self) -> dict:
        """Возвращает полное состояние для персистентности."""
        episodes = []
        for ep in self._buffer:
            episodes.append({
                "episode_id": ep.episode_id if hasattr(ep, 'episode_id') else "",
                "goal": ep.goal if hasattr(ep, 'goal') else "",
                "actions": ep.actions if hasattr(ep, 'actions') else [],
                "outcome": ep.outcome if hasattr(ep, 'outcome') else "",
                "success": ep.success if hasattr(ep, 'success') else False,
                "context": ep.context if hasattr(ep, 'context') else {},
                "replayed_count": getattr(ep, 'replayed_count', 0),
                "lessons": getattr(ep, 'lessons', []),
                "created_at": getattr(ep, 'created_at', None),
            })
        return {
            "episodes": episodes,
            "patterns": list(self._patterns),
            "replay_stats": dict(self._replay_stats),
        }

    def import_state(self, data):
        """Восстанавливает состояние из персистентного хранилища."""
        if isinstance(data, list):
            data_list = data
            patterns = []
            replay_stats = {}
        else:
            data_list = data.get("episodes", [])
            patterns = data.get("patterns", [])
            replay_stats = data.get("replay_stats", {})
        for ed in data_list:
            ep = Episode(
                episode_id=ed.get("episode_id", ""),
                goal=ed.get("goal", ""),
                actions=ed.get("actions", []),
                outcome=ed.get("outcome", ""),
                success=ed.get("success", False),
                context=ed.get("context"),
            )
            ep.replayed_count = ed.get("replayed_count", 0)
            ep.lessons = ed.get("lessons", [])
            if ed.get("created_at"):
                ep.created_at = ed["created_at"]
            self._buffer.append(ep)
            self._index[ep.episode_id] = ep
        if patterns:
            self._patterns.extend(patterns)
        if replay_stats:
            self._replay_stats.update(replay_stats)
