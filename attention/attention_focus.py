# Attention & Focus Management (управление вниманием и фокусом) — Слой 39
# Архитектура автономного AI-агента
# Управление фокусом агента: что важно прямо сейчас, фильтрация шума, переключение.


import time
from enum import Enum


class AttentionMode(Enum):
    FOCUSED    = 'focused'    # глубокая работа над одной задачей
    SCANNING   = 'scanning'   # широкий мониторинг среды
    REACTIVE   = 'reactive'   # реакция на срочные события
    IDLE       = 'idle'       # ожидание / фоновая обработка


class AttentionItem:
    """Элемент, требующий внимания агента."""

    def __init__(self, item_id: str, description: str, source: str,
                 urgency: float = 0.5, importance: float = 0.5,
                 context: dict | None = None):
        self.item_id = item_id
        self.description = description
        self.source = source                    # откуда пришёл сигнал
        self.urgency = max(0.0, min(1.0, urgency))
        self.importance = max(0.0, min(1.0, importance))
        self.context = context or {}
        self.created_at = time.time()
        self.attended = False
        self.attended_at: float | None = None

    @property
    def priority_score(self) -> float:
        """Eisenhower-матрица: срочность × важность."""
        return self.urgency * 0.6 + self.importance * 0.4

    def to_dict(self):
        return {
            'item_id': self.item_id,
            'description': self.description,
            'source': self.source,
            'urgency': self.urgency,
            'importance': self.importance,
            'priority_score': round(self.priority_score, 3),
            'attended': self.attended,
        }


class AttentionFocusManager:
    """
    Attention & Focus Management — Слой 39.

    Функции:
        - управление вниманием: что обрабатывать прямо сейчас
        - очередь входящих сигналов с приоритизацией
        - режимы внимания: focused / scanning / reactive / idle
        - подавление шума: фильтрация нерелевантных сигналов
        - переключение фокуса при критических событиях
        - «окно внимания»: ограничение числа одновременно обрабатываемых задач
        - интеграция с Goal Manager — фокус согласован с текущей целью

    Используется:
        - Autonomous Loop (Слой 20)     — определяет что обрабатывать в цикле
        - Goal Manager (Слой 37)        — текущая цель влияет на фокус
        - Perception Layer (Слой 1)     — входящие данные
        - Monitoring (Слой 17)          — события из системы
    """

    WINDOW_SIZE = 3   # максимум одновременно отслеживаемых элементов

    def __init__(self, cognitive_core=None, goal_manager=None,
                 monitoring=None):
        self.cognitive_core = cognitive_core
        self.goal_manager = goal_manager
        self.monitoring_sys = monitoring

        self._queue: list[AttentionItem] = []
        self._in_focus: list[AttentionItem] = []   # текущее «окно внимания»
        self._mode = AttentionMode.IDLE
        self._history: list[dict] = []
        self._counter = 0
        self._noise_keywords: list[str] = []        # слова-признаки шума

    # ── Входящие сигналы ──────────────────────────────────────────────────────

    def signal(self, description: str, source: str = 'unknown',
               urgency: float = 0.5, importance: float = 0.5,
               context: dict | None = None) -> AttentionItem:
        """Регистрирует новый сигнал/событие в очереди внимания."""
        self._counter += 1
        item = AttentionItem(
            item_id=f"att{self._counter:04d}",
            description=description,
            source=source,
            urgency=urgency,
            importance=importance,
            context=context or {},
        )

        if self._is_noise(item):
            self._log(f"Сигнал отфильтрован как шум: '{description[:60]}'")
            return item

        self._queue.append(item)
        self._queue.sort(key=lambda x: x.priority_score, reverse=True)

        # Срочное событие → реактивный режим
        if urgency >= 0.9:
            self._switch_mode(AttentionMode.REACTIVE)

        self._log(f"Сигнал принят: [{item.item_id}] score={item.priority_score:.2f}")
        return item

    def signal_critical(self, description: str, source: str = 'system') -> AttentionItem:
        """Критический сигнал — максимальная срочность и важность."""
        return self.signal(description, source, urgency=1.0, importance=1.0)

    # ── Управление фокусом ────────────────────────────────────────────────────

    def update_focus(self) -> list[AttentionItem]:
        """
        Обновляет «окно внимания»: выбирает топ-N приоритетных элементов.
        Учитывает текущую цель из Goal Manager.
        """
        # Отмечаем завершённые элементы
        self._in_focus = [i for i in self._in_focus if not i.attended]

        # Добираем из очереди до WINDOW_SIZE
        needed = self.WINDOW_SIZE - len(self._in_focus)
        candidates = [i for i in self._queue if not i.attended]

        # Если есть активная цель — поднимаем релевантные элементы
        if self.goal_manager:
            active = self.goal_manager.get_active()
            if active:
                candidates = self._rerank_by_goal(candidates, active.description)

        for item in candidates[:needed]:
            self._in_focus.append(item)
            self._queue.remove(item)

        if self._in_focus:
            self._switch_mode(AttentionMode.FOCUSED)
        elif not self._queue:
            self._switch_mode(AttentionMode.IDLE)

        return list(self._in_focus)

    def get_focus(self) -> list[AttentionItem]:
        """Возвращает текущее окно внимания без обновления."""
        return list(self._in_focus)

    def attend(self, item_id: str):
        """Отмечает элемент как обработанный."""
        for item in self._in_focus:
            if item.item_id == item_id:
                item.attended = True
                item.attended_at = time.time()
                self._history.append(item.to_dict())
                self._log(f"Обработан: [{item_id}]")
                return

    def attend_all(self):
        """Отмечает все элементы в фокусе как обработанные."""
        for item in self._in_focus:
            item.attended = True
            item.attended_at = time.time()
            self._history.append(item.to_dict())
        self._in_focus = []

    # ── Режимы внимания ───────────────────────────────────────────────────────

    def set_mode(self, mode: AttentionMode):
        self._switch_mode(mode)

    def get_mode(self) -> AttentionMode:
        return self._mode

    # ── Фильтрация шума ───────────────────────────────────────────────────────

    def add_noise_keyword(self, keyword: str):
        """Добавляет ключевое слово для фильтрации шумовых сигналов."""
        self._noise_keywords.append(keyword.lower())

    def _is_noise(self, item: AttentionItem) -> bool:
        if not self._noise_keywords:
            return False
        desc_lower = item.description.lower()
        return any(kw in desc_lower for kw in self._noise_keywords)

    # ── Аналитика ─────────────────────────────────────────────────────────────

    def summary(self) -> dict:
        return {
            'mode': self._mode.value,
            'queue_size': len(self._queue),
            'in_focus': len(self._in_focus),
            'total_processed': len(self._history),
            'noise_filters': len(self._noise_keywords),
        }

    def queue_snapshot(self) -> list[dict]:
        return [i.to_dict() for i in self._queue[:10]]

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _rerank_by_goal(self, items: list[AttentionItem],
                        goal_desc: str) -> list[AttentionItem]:
        """Поднимает элементы, релевантные текущей цели."""
        goal_words = set(goal_desc.lower().split())
        def relevance(item: AttentionItem) -> float:
            words = set(item.description.lower().split())
            overlap = len(goal_words & words) / max(1, len(goal_words | words))
            return item.priority_score + overlap * 0.3
        return sorted(items, key=relevance, reverse=True)

    def _switch_mode(self, mode: AttentionMode):
        if self._mode != mode:
            self._log(f"Режим внимания: {self._mode.value} → {mode.value}")
            self._mode = mode

    def _log(self, message: str):
        if self.monitoring_sys:
            self.monitoring_sys.info(message, source='attention_focus')
        else:
            print(f"[AttentionFocus] {message}")


class AttentionFocus:
    """
    Lightweight attention/focus tracker that works entirely without LLM.

    Maintains per-topic attention weights and decays them over time.
    """

    STOP_WORDS = frozenset({
        'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to',
        'for', 'of', 'with', 'by', 'from', 'is', 'are', 'was', 'were',
        'be', 'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did',
        'it', 'its', 'this', 'that', 'they', 'them', 'then', 'than',
    })

    def __init__(self):
        self._attention_weights: dict[str, float] = {}

    # ── Core API ──────────────────────────────────────────────────────────────

    def update_focus(self, current_task: str,
                     context: dict | None = None) -> dict:
        """
        Update attention weights based on the current task and return focus state.

        - Significant words (len > 3, not stop-words) from current_task have
          their weights boosted by 0.1 (capped at 1.0).
        - All other tracked words are decayed by a factor of 0.95.
        - Returns the top-3 topics by weight, the max weight as focus_score,
          and a distracted flag when the top weight is below 0.3.

        Args:
            current_task — description of what the agent is working on
            context      — optional extra context (reserved for future use)

        Returns:
            Dict with keys: focused_topics, focus_score, distracted
        """
        _ = context  # reserved for future use
        # Extract significant words from the task
        words = current_task.lower().split()
        significant = {
            w.strip('.,!?;:') for w in words
            if len(w.strip('.,!?;:')) > 3 and w.strip('.,!?;:') not in self.STOP_WORDS
        }

        # Decay existing weights first
        for topic in list(self._attention_weights.keys()):
            if topic not in significant:
                self._attention_weights[topic] *= 0.95
                if self._attention_weights[topic] < 0.01:
                    del self._attention_weights[topic]

        # Boost weights for current significant words
        for word in significant:
            current = self._attention_weights.get(word, 0.0)
            self._attention_weights[word] = min(1.0, current + 0.1)

        return self.get_focus()

    def get_focus(self) -> dict:
        """
        Returns the current focus state.

        Returns:
            Dict with keys: focused_topics (list of top-3), focus_score (float),
            distracted (bool — True when top weight < 0.3)
        """
        if not self._attention_weights:
            return {
                'focused_topics': [],
                'focus_score': 0.0,
                'distracted': True,
            }

        sorted_topics = sorted(
            self._attention_weights.items(),
            key=lambda kv: kv[1],
            reverse=True,
        )
        top3 = [topic for topic, _ in sorted_topics[:3]]
        max_weight = sorted_topics[0][1]

        return {
            'focused_topics': top3,
            'focus_score': round(max_weight, 4),
            'distracted': max_weight < 0.3,
        }

