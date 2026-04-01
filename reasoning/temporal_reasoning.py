# Temporal Reasoning System (темпоральное рассуждение) — Слой 40
# Архитектура автономного AI-агента
# Рассуждение о времени: последовательности, длительности, причинно-следственные цепи во времени.


import time
from datetime import datetime
from enum import Enum


class TemporalRelation(Enum):
    BEFORE   = 'before'
    AFTER    = 'after'
    DURING   = 'during'
    OVERLAPS = 'overlaps'
    MEETS    = 'meets'
    STARTS   = 'starts'
    FINISHES = 'finishes'
    EQUALS   = 'equals'


class TemporalEvent:
    """Событие с временными атрибутами."""

    def __init__(self, event_id: str, description: str,
                 start: float | None = None, end: float | None = None,
                 duration_seconds: float | None = None, tags: list | None = None):
        self.event_id = event_id
        self.description = description
        self.start = start or time.time()
        if end:
            self.end = end
        elif duration_seconds:
            self.end = self.start + duration_seconds
        else:
            self.end = None
        self.tags = tags or []
        self.relations: list[dict] = []     # [(event_id, TemporalRelation)]

    @property
    def duration(self) -> float | None:
        if self.end:
            return self.end - self.start
        return None

    @property
    def is_ongoing(self) -> bool:
        now = time.time()
        if self.end is None:
            return self.start <= now
        return self.start <= now <= self.end

    def to_dict(self):
        return {
            'event_id': self.event_id,
            'description': self.description,
            'start': self.start,
            'end': self.end,
            'duration': self.duration,
            'is_ongoing': self.is_ongoing,
            'tags': self.tags,
        }


class TemporalReasoningSystem:
    """
    Temporal Reasoning System — Слой 40.

    Функции:
        - регистрация и хранение временных событий
        - определение отношений между событиями (до/после/во время/...)
        - построение временных цепочек и шкал
        - рассуждение о длительностях и интервалах
        - предсказание момента наступления будущих событий
        - ответы на темпоральные вопросы («что было до X?», «сколько займёт Y?»)

    Используется:
        - Cognitive Core (Слой 3)       — темпоральные вопросы в рассуждениях
        - Long-Horizon Planning (Слой 38) — планирование по времени
        - Knowledge System (Слой 2)     — хранение временного контекста
        - Causal Reasoning (Слой 41)    — причины/следствия с временной меткой
    """

    def __init__(self, cognitive_core=None, knowledge_system=None,
                 monitoring=None):
        self.cognitive_core = cognitive_core
        self.knowledge = knowledge_system
        self.monitoring = monitoring

        self._events: dict[str, TemporalEvent] = {}
        self._timeline: list[str] = []     # event_ids, отсортированные по start
        self._counter = 0

    # ── Регистрация событий ───────────────────────────────────────────────────

    def record(self, description: str, start: float | None = None,
               end: float | None = None, duration_seconds: float | None = None,
               tags: list | None = None) -> TemporalEvent:
        """Регистрирует событие на временной шкале."""
        self._counter += 1
        ev = TemporalEvent(
            event_id=f"ev{self._counter:04d}",
            description=description,
            start=start,
            end=end,
            duration_seconds=duration_seconds,
            tags=tags or [],
        )
        self._events[ev.event_id] = ev
        self._insert_sorted(ev.event_id)
        self._log(f"Событие: [{ev.event_id}] '{description[:60]}'")
        return ev

    # ── Определение отношений ─────────────────────────────────────────────────

    def relate(self, id1: str, id2: str) -> TemporalRelation | None:
        """Определяет темпоральное отношение между двумя событиями (Allen Interval Algebra)."""
        e1 = self._events.get(id1)
        e2 = self._events.get(id2)
        if not e1 or not e2:
            return None

        s1, e1e = e1.start, e1.end or e1.start
        s2, e2e = e2.start, e2.end or e2.start

        if e1e < s2:
            return TemporalRelation.BEFORE
        if s1 > e2e:
            return TemporalRelation.AFTER
        if s1 == s2 and e1e == e2e:
            return TemporalRelation.EQUALS
        if s1 == s2:
            return TemporalRelation.STARTS
        if e1e == e2e:
            return TemporalRelation.FINISHES
        if s1 <= s2 and e1e >= e2e:
            return TemporalRelation.DURING
        return TemporalRelation.OVERLAPS

    def build_chain(self, event_ids: list[str]) -> list[dict]:
        """
        Строит упорядоченную цепочку событий с их отношениями.
        """
        events = [self._events[eid] for eid in event_ids if eid in self._events]
        events.sort(key=lambda e: e.start)
        chain = []
        for i, ev in enumerate(events):
            entry = ev.to_dict()
            if i > 0:
                entry['relation_to_prev'] = self.relate(events[i-1].event_id,
                                                         ev.event_id)
                if entry['relation_to_prev']:
                    entry['relation_to_prev'] = entry['relation_to_prev'].value
            chain.append(entry)
        return chain

    # ── Темпоральные вопросы ──────────────────────────────────────────────────

    def ask(self, question: str) -> str:
        """
        Отвечает на темпоральный вопрос используя Cognitive Core и известные события.
        """
        if not self.cognitive_core:
            return "Cognitive Core недоступен."

        context = self._build_context()
        raw = self.cognitive_core.reasoning(
            f"Темпоральный вопрос: {question}\n\n"
            f"Известные события (хронологически):\n{context}\n\n"
            f"Ответь точно, используя только предоставленную информацию."
        )
        return str(raw)

    def estimate_duration(self, task_description: str) -> str:
        """Оценивает ожидаемую продолжительность задачи через Cognitive Core."""
        if not self.cognitive_core:
            return "Неизвестно"

        # Ищем похожие прошлые события
        similar = self._find_similar_events(task_description)
        context = ""
        if similar:
            durations = [e.duration for e in similar if e.duration]
            if durations:
                avg = sum(durations) / len(durations)
                context = f"Похожие задачи занимали в среднем {avg:.0f} секунд.\n"

        raw = self.cognitive_core.reasoning(
            f"{context}"
            f"Оцени продолжительность задачи: {task_description}\n"
            f"Дай ответ в формате: ОЦЕНКА: <число> <единица>"
        )
        import re
        m = re.search(r'ОЦЕНКА[:\s]+(.+)', str(raw), re.IGNORECASE)
        return m.group(1).strip() if m else str(raw)[:100]

    def what_happened_before(self, event_id: str, n: int = 3) -> list[dict]:
        """Возвращает n событий, произошедших непосредственно до указанного."""
        ev = self._events.get(event_id)
        if not ev:
            return []
        before = [
            e for e in self._events.values()
            if e.start < ev.start and e.event_id != event_id
        ]
        before.sort(key=lambda e: e.start, reverse=True)
        return [e.to_dict() for e in before[:n]]

    def what_happened_after(self, event_id: str, n: int = 3) -> list[dict]:
        """Возвращает n событий после указанного."""
        ev = self._events.get(event_id)
        if not ev:
            return []
        after = [
            e for e in self._events.values()
            if e.start > (ev.end or ev.start) and e.event_id != event_id
        ]
        after.sort(key=lambda e: e.start)
        return [e.to_dict() for e in after[:n]]

    # ── Временная шкала ───────────────────────────────────────────────────────

    def get_timeline(self, since: float | None = None,
                     until: float | None = None) -> list[dict]:
        """Возвращает события в диапазоне времени."""
        events = [self._events[eid] for eid in self._timeline
                  if eid in self._events]
        if since:
            events = [e for e in events if e.start >= since]
        if until:
            events = [e for e in events if e.start <= until]
        return [e.to_dict() for e in events]

    def get_ongoing(self) -> list[dict]:
        """Возвращает события, происходящие прямо сейчас."""
        return [e.to_dict() for e in self._events.values() if e.is_ongoing]

    def summary(self) -> dict:
        return {
            'total_events': len(self._events),
            'ongoing': len(self.get_ongoing()),
            'earliest': min((e.start for e in self._events.values()), default=None),
            'latest': max((e.start for e in self._events.values()), default=None),
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _insert_sorted(self, event_id: str):
        self._timeline.append(event_id)
        # Фильтруем осиротевшие ID (могут остаться после частичной загрузки)
        self._timeline = [eid for eid in self._timeline if eid in self._events]
        self._timeline.sort(key=lambda eid: self._events[eid].start)

    def _build_context(self, n: int = 20) -> str:
        events = [self._events[eid] for eid in self._timeline[-n:]
                  if eid in self._events]
        lines = []
        for e in events:
            ts = datetime.fromtimestamp(e.start).strftime('%Y-%m-%d %H:%M')
            dur = f" (длит. {e.duration:.0f}с)" if e.duration else ""
            lines.append(f"[{e.event_id}] {ts}{dur} — {e.description}")
        return '\n'.join(lines)

    def _find_similar_events(self, description: str,
                              n: int = 5) -> list[TemporalEvent]:
        desc_words = set(description.lower().split())
        scored = []
        for ev in self._events.values():
            ev_words = set(ev.description.lower().split())
            if ev_words and desc_words:
                score = len(desc_words & ev_words) / len(desc_words | ev_words)
                scored.append((score, ev))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [ev for _, ev in scored[:n]]

    def add_event(self, description: str, start: float | None = None,
                  end: float | None = None, duration_seconds: float | None = None,
                  tags: list | None = None) -> 'TemporalEvent':
        """
        Convenience wrapper around record() that works without LLM.
        """
        return self.record(
            description=description,
            start=start,
            end=end,
            duration_seconds=duration_seconds,
            tags=tags,
        )

    def reason_about_time(self, event: str,
                          reference_time: float | None = None) -> dict:
        """
        Deterministic temporal reasoning without LLM.

        Parses natural-language time expressions in Russian and English:
            - yesterday / вчера           → -86400 s
            - tomorrow / завтра           → +86400 s
            - in N days / через N дней    → +N * 86400 s
            - N days ago / N дней назад   → -N * 86400 s
            - next week / на следующей неделе → +7 * 86400 s
            - last week / на прошлой неделе   → -7 * 86400 s

        Args:
            event          — natural-language description of the event
            reference_time — unix timestamp to use as "now" (defaults to current time)

        Returns:
            Dict with keys: event, relative_time (seconds from now),
            absolute_time (unix timestamp), description (human-readable)
        """
        import re

        now = reference_time if reference_time is not None else time.time()
        event_lower = event.lower()
        relative_seconds = 0.0
        matched = False

        # yesterday / вчера
        if re.search(r'\bвчера\b|\byesterday\b', event_lower):
            relative_seconds = -86400.0
            matched = True

        # tomorrow / завтра
        elif re.search(r'\bзавтра\b|\btomorrow\b', event_lower):
            relative_seconds = +86400.0
            matched = True

        # "through N days" / "через N дней" / "in N days"
        elif (m := re.search(
                r'(?:через|in)\s+(\d+)\s*(?:дн[еёяи]|days?)', event_lower)):
            n = int(m.group(1))
            relative_seconds = n * 86400.0
            matched = True

        # "N days ago" / "N дней назад"
        elif (m := re.search(
                r'(\d+)\s*(?:дн[еёяи]|days?)\s*(?:назад|ago)', event_lower)):
            n = int(m.group(1))
            relative_seconds = -n * 86400.0
            matched = True

        # "next week" / "следующая неделя"
        elif re.search(r'next\s+week|следующ\w+\s+недел', event_lower):
            relative_seconds = 7 * 86400.0
            matched = True

        # "last week" / "прошлая неделя"
        elif re.search(r'last\s+week|прошл\w+\s+недел', event_lower):
            relative_seconds = -7 * 86400.0
            matched = True

        # Generic N-number near a time word fallback
        elif (m := re.search(r'(\d+)\s*(?:дн[еёяи]|days?)', event_lower)):
            n = int(m.group(1))
            relative_seconds = n * 86400.0
            matched = True

        absolute_time = now + relative_seconds
        dt = datetime.fromtimestamp(absolute_time)

        if not matched:
            description = f"No time expression detected in: {event!r}"
        elif relative_seconds == 0:
            description = "now"
        elif relative_seconds > 0:
            description = f"in {relative_seconds / 86400:.1f} day(s): {dt.strftime('%Y-%m-%d')}"
        else:
            description = f"{abs(relative_seconds) / 86400:.1f} day(s) ago: {dt.strftime('%Y-%m-%d')}"

        return {
            'event': event,
            'relative_time': relative_seconds,
            'absolute_time': absolute_time,
            'description': description,
        }

    def _log(self, message: str):
        if self.monitoring:
            self.monitoring.info(message, source='temporal_reasoning')
        else:
            print(f"[TemporalReasoning] {message}")


# Alias for compatibility
TemporalReasoning = TemporalReasoningSystem
