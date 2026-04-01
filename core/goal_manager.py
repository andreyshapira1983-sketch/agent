# Goal Management System (управление целями) — Слой 37
# Архитектура автономного AI-агента
# Управление целями агента: приоритизация, разрешение конфликтов, дерево целей.


import re
import time
from enum import Enum


class GoalStatus(Enum):
    PENDING   = 'pending'
    ACTIVE    = 'active'
    PAUSED    = 'paused'
    COMPLETED = 'completed'
    FAILED    = 'failed'
    CANCELLED = 'cancelled'


class GoalPriority(Enum):
    CRITICAL = 1
    HIGH     = 2
    MEDIUM   = 3
    LOW      = 4
    DEFERRED = 5


class Goal:
    """Одна цель агента."""

    def __init__(self, goal_id: str, description: str,
                 priority: GoalPriority = GoalPriority.MEDIUM,
                 parent_id: str | None = None, deadline: float | None = None,
                 success_criteria: str | None = None, tags: list | None = None):
        self.goal_id = goal_id
        self.description = description
        self.priority = priority
        self.parent_id = parent_id          # для дерева целей
        self.deadline = deadline            # unix timestamp
        self.success_criteria = success_criteria
        self.tags = tags or []
        self.status = GoalStatus.PENDING
        self.progress: float = 0.0          # 0–1
        self.sub_goals: list[str] = []      # id дочерних целей
        self.created_at = time.time()
        self.started_at: float | None = None
        self.completed_at: float | None = None
        self.notes: list[str] = []

    @property
    def is_overdue(self) -> bool:
        return self.deadline is not None and time.time() > self.deadline

    @property
    def urgency_score(self) -> float:
        """Оценка срочности: чем ближе дедлайн и выше приоритет, тем выше."""
        base = (6 - self.priority.value) / 5.0     # 1.0 для CRITICAL, 0.2 для DEFERRED
        if self.deadline:
            remaining = max(0, self.deadline - time.time())
            urgency = 1.0 / (1.0 + remaining / 3600)  # убывает со временем
            return (base + urgency) / 2
        return base

    def to_dict(self):
        return {
            'goal_id': self.goal_id,
            'description': self.description,
            'priority': self.priority.name,
            'status': self.status.value,
            'progress': self.progress,
            'parent_id': self.parent_id,
            'sub_goals': self.sub_goals,
            'deadline': self.deadline,
            'urgency_score': round(self.urgency_score, 3),
            'is_overdue': self.is_overdue,
        }


class GoalManager:
    """
    Goal Management System — Слой 37.

    Функции:
        - создание и хранение целей (дерево: цель → подцели)
        - приоритизация: автоматически выбирает следующую цель
        - разрешение конфликтов: выявляет противоречащие цели
        - отслеживание прогресса и дедлайнов
        - декомпозиция целей через Cognitive Core
        - интеграция с Autonomous Loop — всегда знает, что делать

    Используется:
        - Autonomous Loop (Слой 20)         — получает текущую цель
        - Task Decomposition (Слой 30)      — разбивает цель на задачи
        - Long-Horizon Planning (Слой 38)   — долгосрочное планирование
        - Reflection System (Слой 10)       — обновляет прогресс целей
    """

    def __init__(self, cognitive_core=None, knowledge_system=None,
                 monitoring=None):
        self.cognitive_core = cognitive_core
        self.knowledge = knowledge_system
        self.monitoring = monitoring

        self._goals: dict[str, Goal] = {}
        self._active_goal_id: str | None = None
        self._counter = 0

    @classmethod
    def _normalize_goal_text(cls, text: str | None) -> str:
        """Нормализует описание цели для дедупликации."""
        return cls._normalize_subgoal_text(text).casefold()

    def _find_duplicate_goal(self, description: str, parent_id: str | None) -> Goal | None:
        """Ищет живую цель с тем же смыслом и тем же родителем."""
        normalized = self._normalize_goal_text(description)
        if not normalized:
            return None

        active_statuses = {
            GoalStatus.PENDING,
            GoalStatus.ACTIVE,
            GoalStatus.PAUSED,
        }
        for goal in self._goals.values():
            if goal.parent_id != parent_id:
                continue
            if goal.status not in active_statuses:
                continue
            if self._normalize_goal_text(goal.description) == normalized:
                return goal
        return None

    @staticmethod
    def _normalize_subgoal_text(text: str | None) -> str:
        """Очищает подцель от markdown, нумерации и служебного мусора."""
        cleaned = " ".join(str(text or "").strip().split())
        if not cleaned:
            return ""

        cleaned = re.sub(r'^[\-*•#>\s]+', '', cleaned)
        cleaned = re.sub(r'^\d+[.)\]:-]?\s*', '', cleaned)
        cleaned = re.sub(r'^\*+\s*', '', cleaned)
        cleaned = re.sub(r'\*\*(.*?)\*\*', r'\1', cleaned)
        cleaned = re.sub(r'`([^`]+)`', r'\1', cleaned)
        cleaned = re.sub(r'^п\s*о\s*д\s*ц\s*е\s*л\s*ь\s*:\s*', '', cleaned, flags=re.IGNORECASE)
        # Удаляем оставшиеся звездочки в начале, конце и внутри текста
        cleaned = re.sub(r'[\*_]+', '', cleaned)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip(' .;:-')
        return cleaned

    # ── Создание и управление целями ──────────────────────────────────────────

    def add(self, description: str,
            priority: GoalPriority = GoalPriority.MEDIUM,
            parent_id: str | None = None, deadline: float | None = None,
            success_criteria: str | None = None, tags: list | None = None) -> Goal:
        """Добавляет новую цель в систему."""
        duplicate = self._find_duplicate_goal(description, parent_id)
        if duplicate:
            if deadline is not None and duplicate.deadline is None:
                duplicate.deadline = deadline
            if success_criteria and not duplicate.success_criteria:
                duplicate.success_criteria = success_criteria
            if tags:
                merged_tags = list(dict.fromkeys([*duplicate.tags, *tags]))
                duplicate.tags = merged_tags
            self._log(
                f"Цель переиспользована: [{duplicate.goal_id}] '{duplicate.description[:60]}…'",
                level='debug',
            )
            return duplicate

        self._counter += 1
        goal_id = f"g{self._counter:04d}"
        goal = Goal(
            goal_id=goal_id,
            description=description,
            priority=priority,
            parent_id=parent_id,
            deadline=deadline,
            success_criteria=success_criteria,
            tags=tags or [],
        )
        self._goals[goal_id] = goal

        # Регистрируем как подцель у родителя
        if parent_id and parent_id in self._goals:
            self._goals[parent_id].sub_goals.append(goal_id)

        self._log(f"Цель добавлена: [{goal_id}] '{description}' "
                  f"(приоритет: {priority.name})")
        return goal

    def decompose(self, goal_id: str) -> list[Goal]:
        """
        Декомпозирует цель на подцели через Cognitive Core.
        Возвращает список созданных подцелей.
        """
        goal = self._goals.get(goal_id)
        if not goal or not self.cognitive_core:
            return []

        raw = str(self.cognitive_core.reasoning(
            f"Декомпозируй цель на 3–5 конкретных подцелей.\n"
            f"Цель: {goal.description}\n\n"
            f"ВАЖНО: Отвечай ТОЛЬКО строго в формате ниже. НЕ используй таблицы, НЕ используй markdown. "
            f"Каждая подцель — одна строка с префиксом ПОДЦЕЛЬ:\n\n"
            f"ПОДЦЕЛЬ: <краткое описание>\n"
            f"КРИТЕРИЙ: <как понять что выполнена>\n\n"
            f"ПОДЦЕЛЬ: <краткое описание>\n"
            f"КРИТЕРИЙ: <как понять что выполнена>\n\n"
            f"(повтори для каждой подцели, только текст без таблиц)"
        ))

        sub_descs = re.findall(r'ПОДЦЕЛЬ[:\s]+(.+)', raw, re.IGNORECASE)
        criteria = re.findall(r'КРИТЕРИЙ[:\s]+(.+)', raw, re.IGNORECASE)

        # Отфильтровываем мусор: строки таблиц, слишком короткие, обрывочные фрагменты
        def _is_garbage(d: str) -> bool:
            d = d.strip()
            if not d or len(d) < 8:
                return True
            if d.startswith('|') or d.count('|') > 1:
                return True
            dl = d.lower()
            _table_kw = ('критерий успеха', 'приоритет', 'зависит от',
                         'запустить первой', ':---', '--- |')
            return any(kw in dl for kw in _table_kw)

        sub_descs = [d for d in sub_descs if not _is_garbage(d)]

        sub_goals = []
        for i, desc in enumerate(sub_descs):
            desc = self._normalize_subgoal_text(desc)
            crit = self._normalize_subgoal_text(criteria[i]) if i < len(criteria) else None
            if not desc:
                continue
            sg = self.add(
                description=desc,
                priority=goal.priority,
                parent_id=goal_id,
                success_criteria=crit,
            )
            sub_goals.append(sg)

        self._log(f"Цель [{goal_id}] декомпозирована на {len(sub_goals)} подцелей")
        return sub_goals

    def update_progress(self, goal_id: str, progress: float,
                        note: str | None = None):
        """Обновляет прогресс цели (0.0 – 1.0)."""
        goal = self._goals.get(goal_id)
        if not goal:
            return
        goal.progress = max(0.0, min(1.0, progress))
        if note:
            goal.notes.append(note)
        if goal.progress >= 1.0:
            self.complete(goal_id)

    def complete(self, goal_id: str):
        """Помечает цель как выполненную."""
        goal = self._goals.get(goal_id)
        if goal:
            goal.status = GoalStatus.COMPLETED
            goal.progress = 1.0
            goal.completed_at = time.time()
            self._log(f"Цель выполнена: [{goal_id}] '{goal.description}'")
            # Обновляем прогресс родителя
            if goal.parent_id:
                self._recalc_parent_progress(goal.parent_id)

    def fail(self, goal_id: str, reason: str | None = None):
        goal = self._goals.get(goal_id)
        if goal:
            goal.status = GoalStatus.FAILED
            if reason:
                goal.notes.append(f"Причина провала: {reason}")
            self._log(f"Цель провалена: [{goal_id}]" + (f" ({reason})" if reason else ""))

    def cancel(self, goal_id: str):
        goal = self._goals.get(goal_id)
        if goal:
            goal.status = GoalStatus.CANCELLED
            self._log(f"Цель отменена: [{goal_id}]")

    def activate(self, goal_id: str):
        """Помечает цель как активную (в работе)."""
        goal = self._goals.get(goal_id)
        if goal:
            goal.status = GoalStatus.ACTIVE
            goal.started_at = goal.started_at or time.time()
            self._active_goal_id = goal_id

    # ── Приоритизация ─────────────────────────────────────────────────────────

    def get_next(self) -> Goal | None:
        """
        Возвращает следующую цель для выполнения.
        Сортирует по urgency_score (срочность + приоритет).
        """
        candidates = [
            g for g in self._goals.values()
            if g.status in (GoalStatus.PENDING, GoalStatus.ACTIVE)
            and not g.sub_goals  # берём листовые цели (без подцелей)
        ]
        if not candidates:
            # Если нет листовых — берём корневые активные
            candidates = [
                g for g in self._goals.values()
                if g.status in (GoalStatus.PENDING, GoalStatus.ACTIVE)
            ]
        if not candidates:
            return None
        return max(candidates, key=lambda g: g.urgency_score)

    def get_active(self) -> Goal | None:
        if self._active_goal_id:
            return self._goals.get(self._active_goal_id)
        return None

    def get_goal(self, goal_id: str) -> Goal | None:
        """Возвращает объект цели по id."""
        return self._goals.get(goal_id)

    def get_open_subgoals(self, goal_id: str) -> list[Goal]:
        """Возвращает незавершённые подцели родителя в исходном порядке."""
        goal = self._goals.get(goal_id)
        if not goal:
            return []

        open_statuses = {
            GoalStatus.PENDING,
            GoalStatus.ACTIVE,
            GoalStatus.PAUSED,
        }
        return [
            self._goals[sid]
            for sid in goal.sub_goals
            if sid in self._goals and self._goals[sid].status in open_statuses
        ]

    # ── Конфликты ─────────────────────────────────────────────────────────────

    def detect_conflicts(self) -> list[tuple[str, str, str]]:
        """
        Выявляет конфликтующие цели через Cognitive Core.
        Возвращает список (goal_id_1, goal_id_2, причина).
        """
        active = [g for g in self._goals.values()
                  if g.status in (GoalStatus.PENDING, GoalStatus.ACTIVE)]
        if len(active) < 2 or not self.cognitive_core:
            return []

        goals_text = '\n'.join(f"[{g.goal_id}] {g.description}" for g in active)
        raw = str(self.cognitive_core.reasoning(
            f"Найди конфликтующие цели среди следующих. "
            f"Если конфликт есть, укажи: КОНФЛИКТ: id1 vs id2 — причина.\n\n"
            f"{goals_text}"
        ))

        conflicts = []
        for m in re.finditer(r'КОНФЛИКТ[:\s]+(\S+)\s+vs\s+(\S+)\s*[—-]\s*(.+)',
                             raw, re.IGNORECASE):
            conflicts.append((m.group(1), m.group(2), m.group(3).strip()))

        if conflicts:
            self._log(f"Обнаружено конфликтов: {len(conflicts)}")
        return conflicts

    def resolve_conflict(self, goal_id_1: str, goal_id_2: str) -> str:
        """
        Разрешает конфликт между двумя целями — одну приостанавливает.
        Возвращает id оставшейся активной цели.
        """
        g1 = self._goals.get(goal_id_1)
        g2 = self._goals.get(goal_id_2)
        if not g1 or not g2:
            return goal_id_1
        if g1.urgency_score >= g2.urgency_score:
            g2.status = GoalStatus.PAUSED
            self._log(f"Конфликт решён: [{goal_id_2}] приостановлена в пользу [{goal_id_1}]")
            return goal_id_1
        else:
            g1.status = GoalStatus.PAUSED
            self._log(f"Конфликт решён: [{goal_id_1}] приостановлена в пользу [{goal_id_2}]")
            return goal_id_2

    # ── Реестр ────────────────────────────────────────────────────────────────

    def get_all(self, status: GoalStatus | None = None) -> list[dict]:
        goals = self._goals.values()
        if status:
            goals = [g for g in goals if g.status == status]
        return [g.to_dict() for g in goals]

    def get_tree(self, root_id: str | None = None) -> dict:
        """Возвращает дерево целей начиная с root_id (или корневых целей)."""
        def build_node(goal: Goal) -> dict:
            node = goal.to_dict()
            node['children'] = [
                build_node(self._goals[sid])
                for sid in goal.sub_goals
                if sid in self._goals
            ]
            return node

        if root_id:
            root = self._goals.get(root_id)
            return build_node(root) if root else {}

        roots = [g for g in self._goals.values() if g.parent_id is None]
        return {'roots': [build_node(r) for r in roots]}

    def summary(self) -> dict:
        from collections import Counter
        statuses = Counter(g.status.value for g in self._goals.values())
        return {
            'total': len(self._goals),
            **dict(statuses),
            'active_goal': self._active_goal_id,
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _recalc_parent_progress(self, parent_id: str):
        parent = self._goals.get(parent_id)
        if not parent or not parent.sub_goals:
            return
        sub_list = [self._goals[sid] for sid in parent.sub_goals if sid in self._goals]
        if sub_list:
            avg = sum(g.progress for g in sub_list) / len(sub_list)
            parent.progress = avg
            if avg >= 1.0:
                self.complete(parent_id)

    def add_goal(self, description: str,
                 priority: 'GoalPriority | None' = None,
                 deadline: float | None = None,
                 tags: list | None = None) -> 'Goal':
        """
        Convenience wrapper — adds a goal without requiring GoalPriority enum.
        Works without LLM.
        """
        if priority is None:
            priority = GoalPriority.MEDIUM
        return self.add(
            description=description,
            priority=priority,
            deadline=deadline,
            tags=tags or [],
        )

    def get_next_goal(self) -> 'Goal | None':
        """Returns the next goal to work on without LLM. Wraps get_next()."""
        return self.get_next()

    def prioritize(self, goals: list) -> list:
        """
        Deterministic goal prioritization without LLM.

        Each goal dict may contain:
            urgency    (0-1) — how time-sensitive the goal is
            importance (0-1) — how important the goal is
            effort     (0-1) — how much work is required

        If any field is missing, keywords in the 'goal' string are used:
            'срочно' / 'urgent' / 'critical' → urgency = 0.9
            'важно' / 'important'            → importance = 0.8
            'простое' / 'easy' / 'quick'    → effort = 0.2

        Score formula: urgency * 0.4 + importance * 0.4 - effort * 0.2

        Args:
            goals — list of dicts with at least a 'goal' key

        Returns:
            Same list sorted by score DESC, with 'priority_score' added.
        """
        URGENCY_KEYWORDS  = ('срочно', 'urgent', 'critical', 'asap', 'immediately')
        IMPORTANCE_KEYWORDS = ('важно', 'important', 'crucial', 'key', 'essential')
        LOW_EFFORT_KEYWORDS = ('простое', 'easy', 'quick', 'simple', 'trivial')

        scored = []
        for g in goals:
            text = str(g.get('goal', '')).lower()

            urgency = g.get('urgency')
            if urgency is None:
                urgency = 0.9 if any(k in text for k in URGENCY_KEYWORDS) else 0.5

            importance = g.get('importance')
            if importance is None:
                importance = 0.8 if any(k in text for k in IMPORTANCE_KEYWORDS) else 0.5

            effort = g.get('effort')
            if effort is None:
                effort = 0.2 if any(k in text for k in LOW_EFFORT_KEYWORDS) else 0.5

            score = urgency * 0.4 + importance * 0.4 - effort * 0.2
            goal_copy = dict(g)
            goal_copy['priority_score'] = round(score, 4)
            scored.append(goal_copy)

        scored.sort(key=lambda x: x['priority_score'], reverse=True)
        return scored

    def audit_goals(self) -> dict:
        """
        Проверяет каждую цель: была ли она реально выполнена, сделан ли прогресс,
        или агент просто пропустил её без единой попытки.

        Категории:
            completed        — выполнена полностью (status=COMPLETED)
            progressed       — был прогресс, но не завершена (0 < progress < 1)
            attempted        — агент начал (started_at задан), но прогресса нет
            skipped          — никогда не трогалась (started_at=None, progress=0)
                               и либо отменена/провалена, либо ждёт >24 ч
        """
        import time as _time
        completed  = []
        progressed = []
        attempted  = []
        skipped    = []

        terminal = {GoalStatus.COMPLETED, GoalStatus.FAILED, GoalStatus.CANCELLED}
        now = _time.time()

        for g in self._goals.values():
            if g.status == GoalStatus.ACTIVE:
                continue  # в работе прямо сейчас — не оцениваем

            if g.status == GoalStatus.COMPLETED:
                completed.append(g.goal_id)
                continue

            if g.progress > 0.0:
                progressed.append(g.goal_id)
                continue

            # Прогресса нет — смотрим, начинал ли агент
            if g.started_at is not None:
                attempted.append(g.goal_id)
                continue

            # started_at нет, progress=0 — пропустил?
            if g.status in terminal or (now - g.created_at > 86400):
                skipped.append(g.goal_id)

        result = {
            'total':      len(self._goals),
            'completed':  len(completed),
            'progressed': len(progressed),
            'attempted':  len(attempted),
            'skipped':    len(skipped),
            'skipped_ids': skipped[:10],   # первые 10 для лога
        }

        msg = (
            f"Аудит целей: всего={result['total']} "
            f"выполнено={result['completed']} "
            f"прогресс={result['progressed']} "
            f"попытка_без_результата={result['attempted']} "
            f"пропущено_без_попытки={result['skipped']}"
        )
        if skipped:
            msg += f" | пропущенные (до 10): {[self._goals[i].description[:40] for i in skipped[:5]]}"
        self._log(msg)

        return result

    def _log(self, message: str, level: str = 'info'):
        if self.monitoring:
            log_fn = getattr(self.monitoring, level, self.monitoring.info)
            log_fn(message, source='goal_manager')
        else:
            print(f"[GoalManager] {message}")
