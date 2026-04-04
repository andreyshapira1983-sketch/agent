# Adaptive Reasoning — три ключевые слабости агента
# Архитектура автономного AI-агента (Слой 3, Cognitive Core expansion)
#
# Модуль устраняет три структурных пробела:
#   1. GoalConflictResolver   — конфликтующие цели
#   2. IncompletenessDetector — неполные данные
#   3. DecisionRevisor        — необходимость пересмотра решений
#
# Используется:
#   - Autonomous Loop (Слой 20) — _analyze(), _plan()
#   - Goal Manager (Слой 37)    — detect_conflicts()

from __future__ import annotations

import time
import re
from enum import Enum
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


# ─────────────────────────────────────────────────────────────────────────────
# 1. GOAL CONFLICT RESOLVER — Конфликтующие цели
# ─────────────────────────────────────────────────────────────────────────────

class ConflictType(Enum):
    RESOURCE   = 'resource'    # оба требуют один ресурс/файл/API
    LOGICAL    = 'logical'     # G1 противоречит G2 по смыслу
    TEMPORAL   = 'temporal'    # взаимоисключающие временные окна
    PRIORITY   = 'priority'    # оба CRITICAL, непонятно что первым


class ConflictResolution(Enum):
    SEQUENCE   = 'sequence'    # выполнить последовательно
    PRIORITIZE = 'prioritize'  # выбрать цель с высшим приоритетом
    MERGE      = 'merge'       # объединить в одну выполнимую цель
    PAUSE_ONE  = 'pause_one'   # приостановить менее срочную
    ASK_USER   = 'ask_user'    # необходимо вмешательство человека


@dataclass
class GoalConflict:
    goal_a: str                       # описание первой цели
    goal_b: str                       # описание второй цели
    goal_a_id: str = ''
    goal_b_id: str = ''
    conflict_type: ConflictType = ConflictType.LOGICAL
    resolution: ConflictResolution = ConflictResolution.PRIORITIZE
    explanation: str = ''
    resolved_goal: str = ''           # итоговая цель после разрешения (если MERGE)

    def to_dict(self) -> dict:
        return {
            'goal_a':       self.goal_a[:80],
            'goal_b':       self.goal_b[:80],
            'type':         self.conflict_type.value,
            'resolution':   self.resolution.value,
            'explanation':  self.explanation[:200],
            'resolved_goal': self.resolved_goal[:120],
        }


class GoalConflictResolver:
    """
    Обнаруживает и разрешает конфликты между активными целями.

    Правила:
    - Одновременные CRITICAL-цели, не зависящие друг от друга → PRIORITIZE
    - Цели, использующие один и тот же файл/API в разных направлениях → RESOURCE
    - Цели, чьи ключевые слова противоположны (удалить/создать, остановить/запустить) → LOGICAL
    - При ≥3 незавершённых ACTIVE целях → PAUSEодну

    Разрешение без LLM: быстрое, детерминированное.
    Разрешение через LLM: только если детерминированный путь не дал ответа.
    """

    # Пары слов-антонимов, сигнализирующих о логическом конфликте
    _ANTONYMS: list[tuple[str, str]] = [
        ('удали', 'созда'), ('create', 'delete'), ('start', 'stop'),
        ('запусти', 'останови'), ('включи', 'выключи'),
        ('enable', 'disable'), ('добавь', 'удали'),
        ('зашифруй', 'расшифруй'), ('encrypt', 'decrypt'),
        ('publish', 'unpublish'), ('опубликуй', 'скрой'),
        ('увеличь', 'уменьши'), ('increase', 'decrease'),
        ('заблокируй', 'разблокируй'), ('block', 'unblock'),
    ]

    # Ресурсоёмкие ключевые слова — одновременная запись говорит о ресурс-конфликте
    _RESOURCE_KEYS: list[str] = [
        'database', 'db', 'базу данных', 'таблицу', 'файл settings',
        'config', 'настройки', '.env', 'порт 80', 'порт 443',
        'gpu', 'gpus', 'model weights', 'веса модели',
    ]

    def detect(self, goals: list[tuple[str, str, int]]) -> list[GoalConflict]:
        """
        Принимает список (goal_id, description, priority_value) активных целей.
        Возвращает список найденных конфликтов.

        priority_value: 1=CRITICAL … 5=DEFERRED
        """
        conflicts: list[GoalConflict] = []
        for i, (id_a, desc_a, prio_a) in enumerate(goals):
            for id_b, desc_b, prio_b in goals[i + 1:]:
                conflict = self._check_pair(id_a, desc_a, prio_a, id_b, desc_b, prio_b)
                if conflict:
                    conflicts.append(conflict)
        return conflicts

    def _check_pair(
        self,
        id_a: str, desc_a: str, prio_a: int,
        id_b: str, desc_b: str, prio_b: int,
    ) -> GoalConflict | None:
        a_low = desc_a.lower()
        b_low = desc_b.lower()

        # 1. Логический конфликт (антонимы)
        for word_a, word_b in self._ANTONYMS:
            a_has_a = word_a in a_low
            a_has_b = word_b in a_low
            b_has_a = word_a in b_low
            b_has_b = word_b in b_low
            if (a_has_a and b_has_b) or (a_has_b and b_has_a):
                resolution = (
                    ConflictResolution.PRIORITIZE if prio_a != prio_b
                    else ConflictResolution.ASK_USER
                )
                winner = desc_a if prio_a <= prio_b else desc_b
                return GoalConflict(
                    goal_a=desc_a, goal_b=desc_b,
                    goal_a_id=id_a, goal_b_id=id_b,
                    conflict_type=ConflictType.LOGICAL,
                    resolution=resolution,
                    explanation=f'Противоположные действия: "{word_a}" vs "{word_b}"',
                    resolved_goal=winner,
                )

        # 2. Ресурсный конфликт (общий ресурс)
        for rkey in self._RESOURCE_KEYS:
            if rkey in a_low and rkey in b_low:
                resolution = (
                    ConflictResolution.SEQUENCE if prio_a == prio_b
                    else ConflictResolution.PRIORITIZE
                )
                winner = desc_a if prio_a <= prio_b else desc_b
                return GoalConflict(
                    goal_a=desc_a, goal_b=desc_b,
                    goal_a_id=id_a, goal_b_id=id_b,
                    conflict_type=ConflictType.RESOURCE,
                    resolution=resolution,
                    explanation=f'Оба используют ресурс: "{rkey}"',
                    resolved_goal=winner,
                )

        # 3. Приоритетный конфликт (оба CRITICAL или HIGH)
        if prio_a <= 2 and prio_b <= 2:
            return GoalConflict(
                goal_a=desc_a, goal_b=desc_b,
                goal_a_id=id_a, goal_b_id=id_b,
                conflict_type=ConflictType.PRIORITY,
                resolution=ConflictResolution.PRIORITIZE,
                explanation='Несколько критических целей одновременно',
                resolved_goal=desc_a if prio_a <= prio_b else desc_b,
            )

        return None

    def resolve_to_prompt_hint(self, conflicts: list[GoalConflict]) -> str:
        """Формирует подсказку для LLM-плана на основе найденных конфликтов."""
        if not conflicts:
            return ''
        lines = ['[CONFLICT_RESOLUTION] Обнаружены конфликты между целями:']
        for c in conflicts[:3]:
            lines.append(
                f'  • Конфликт ({c.conflict_type.value}): '
                f'"{c.goal_a[:60]}" vs "{c.goal_b[:60]}"'
            )
            lines.append(
                f'    Решение: {c.resolution.value} → '
                f'Приоритет: "{c.resolved_goal[:60]}"'
            )
        lines.append('Выполни только приоритетную цель, остальные поставь на паузу.')
        return '\n'.join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 2. INCOMPLETENESS DETECTOR — Неполные данные
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DataGap:
    """Описание конкретного пробела в данных."""
    field: str                    # что отсутствует: 'url', 'filename', 'api_key', etc.
    description: str              # человекочитаемое описание
    severity: float = 0.5         # 0.0 (некритично) … 1.0 (нельзя продолжать)
    suggested_action: str = ''    # что сделать: SEARCH, ASK_USER, USE_DEFAULT

    def to_dict(self) -> dict:
        return {
            'field':            self.field,
            'description':      self.description,
            'severity':         round(self.severity, 2),
            'suggested_action': self.suggested_action,
        }


class IncompletenessDetector:
    """
    Определяет, достаточно ли данных для выполнения плана.

    Проверяет:
    - Есть ли конкретные параметры (URL, имена файлов, API-ключи)?
    - Есть ли наблюдение из среды?
    - Есть ли противоречия в данных?
    - Достаточно ли контекста для планирования?

    Не требует LLM — полностью детерминированная оценка.
    Опционально: уточняющий запрос через LLM только при высокой severity.
    """

    # Паттерны-маркеры неопределённости в тексте
    _VAGUE_PATTERNS = [
        r'\b(какой-то|какой то|some|certain|appropriate|relevant)\b',
        r'\b(нужный|нужный файл|нужный url|нужный ключ)\b',
        r'\bTODO\b|\bFIXME\b|\bPLACEHOLDER\b|\bXXX\b',
        r'<[A-Z_]+>',          # <API_KEY>, <FILENAME>
        r'\?\?\?|\[\.\.\.?\]', # ???, [...], [..]
    ]

    # Если в цели есть эти слова — нужны конкретные данные
    _REQUIRES_SPECIFICS: dict[str, tuple[str, str]] = {
        'api':      ('api_key_or_url', 'Нужен API ключ или URL'),
        'скачай':   ('url',            'Нужна конкретная ссылка для скачивания'),
        'download': ('url',            'Need a specific URL to download'),
        'прочитай': ('filename',       'Нужно имя файла для чтения'),
        'read file':('filename',       'Need a specific filename to read'),
        'запиши':   ('filename',       'Нужно куда записывать'),
        'email':    ('recipient',      'Нужен адрес получателя'),
        'отправь':  ('target',         'Куда отправить? Адрес/endpoint не задан'),
        'send':     ('target',         'Where to send? Target not specified'),
        'подключись':('host',          'Нужен хост/IP для подключения'),
        'connect':  ('host',           'Need a host/IP to connect'),
        'базе данных':('db_name',      'Нужно имя базы данных'),
        'database': ('db_name',        'Need database name'),
    }

    def assess(
        self,
        goal: str,
        observation: dict | None,
        analysis: str | None,
    ) -> list[DataGap]:
        """
        Оценивает полноту данных для заданной цели.
        Возвращает список DataGap — пробелов в данных.
        """
        gaps: list[DataGap] = []
        goal_low = goal.lower()

        # 1. Проверяем обязательные параметры по ключевым словам цели
        for kw, (field, descr) in self._REQUIRES_SPECIFICS.items():
            if kw in goal_low:
                # Есть ли конкретное значение в наблюдении или анализе?
                context = (str(observation or '') + str(analysis or '')).lower()
                # Ищем признаки конкретного значения рядом с этим ключевым словом
                has_value = self._context_has_concrete_value(field, context, goal_low)
                if not has_value:
                    gaps.append(DataGap(
                        field=field,
                        description=descr,
                        severity=0.7,
                        suggested_action='ASK_USER или SEARCH для уточнения',
                    ))

        # 2. Проверяем паттерны неопределённости в самой цели
        for pattern in self._VAGUE_PATTERNS:
            match = re.search(pattern, goal, re.IGNORECASE)
            if match:
                gaps.append(DataGap(
                    field='vague_reference',
                    description=f'Неопределённая ссылка в цели: "{match.group()}"',
                    severity=0.6,
                    suggested_action='Уточни конкретное значение',
                ))
                break  # один пробел такого типа достаточно

        # 3. Пустое наблюдение при задаче, требующей внешних данных
        _external_kws = ('search', 'найди', 'fetch', 'получи', 'crawl', 'обход')
        needs_external = any(kw in goal_low for kw in _external_kws)
        if needs_external and not observation:
            gaps.append(DataGap(
                field='observation',
                description='Задача требует внешних данных, но observation пуст',
                severity=0.5,
                suggested_action='SEARCH: запрос для получения данных',
            ))

        # 4. Анализ пустой, но план требует понимания контекста
        _requires_context = ('анализ', 'compare', 'сравни', 'оцени', 'оценивай')
        needs_ctx = any(kw in goal_low for kw in _requires_context)
        if needs_ctx and not analysis:
            gaps.append(DataGap(
                field='analysis',
                description='Задача требует анализа контекста, но анализ не выполнен',
                severity=0.4,
                suggested_action='Выполни анализ перед планированием',
            ))

        return gaps

    def _context_has_concrete_value(self, field: str, context: str, goal: str) -> bool:
        """Эвристика: есть ли конкретное значение в контексте."""
        if field == 'url':
            return bool(re.search(r'https?://', context + ' ' + goal))
        if field in ('filename', 'file'):
            return bool(re.search(r'\w+\.\w{1,5}', goal))
        if field == 'api_key_or_url':
            return bool(
                re.search(r'https?://', context + goal)
                or re.search(r'[A-Za-z0-9_-]{20,}', context)  # похоже на API ключ
            )
        if field == 'host':
            return bool(re.search(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}|localhost|\w+\.\w+\.\w+', goal))
        if field == 'db_name':
            return bool(re.search(r'\b\w+_(db|database)\b|\bpostgres\b|\bmysql\b|\bsqlite\b', goal))
        return bool(context.strip())

    def is_sufficient(
        self,
        goal: str,
        observation: dict | None,
        analysis: str | None,
        severity_threshold: float = 0.65,
    ) -> bool:
        """
        Возвращает True если данных достаточно для планирования.
        False если есть gap с severity >= threshold.
        """
        gaps = self.assess(goal, observation, analysis)
        return not any(g.severity >= severity_threshold for g in gaps)

    def to_prompt_hint(self, gaps: list[DataGap], _goal: str) -> str:
        """Формирует подсказку для LLM о пробелах в данных."""
        if not gaps:
            return ''
        lines = ['[INCOMPLETE_DATA] Данных недостаточно для полного выполнения задачи:']
        for gap in gaps[:4]:
            lines.append(f'  • Отсутствует: {gap.description}')
            lines.append(f'    Рекомендация: {gap.suggested_action}')
        lines.append(
            'Если данных нет — используй SEARCH для уточнения или выполни задачу '
            'с разумными допущениями, явно указав их в результате.'
        )
        return '\n'.join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 3. DECISION REVISOR — Необходимость пересмотра решений
# ─────────────────────────────────────────────────────────────────────────────

class RevisionTrigger(Enum):
    REPEATED_FAILURE     = 'repeated_failure'      # план падает ≥N раз подряд
    CONTRADICTING_INFO   = 'contradicting_info'    # новые данные противоречат плану
    ENVIRONMENT_CHANGE   = 'environment_change'    # среда изменилась
    GOAL_DRIFT           = 'goal_drift'            # текущий план ушёл от исходной цели
    STALE_PLAN           = 'stale_plan'            # план не менялся слишком долго
    COST_EXCEEDED        = 'cost_exceeded'         # ресурсы/время превышены без результата


@dataclass
class RevisionDecision:
    should_revise: bool
    trigger: RevisionTrigger | None = None
    confidence: float = 0.0        # насколько уверены в необходимости пересмотра
    explanation: str = ''
    revision_prompt: str = ''      # готовая подсказка для LLM-реплана

    def to_dict(self) -> dict:
        return {
            'should_revise': self.should_revise,
            'trigger':       self.trigger.value if self.trigger else None,
            'confidence':    round(self.confidence, 2),
            'explanation':   self.explanation[:200],
        }


class DecisionRevisor:
    """
    Определяет, нужно ли пересмотреть текущее решение/план агента.

    Триггеры пересмотра:
    1. Повторные неудачи (≥3 подряд) с одинаковым планом → REPEATED_FAILURE
    2. Анализ нового наблюдения противоречит текущему плану → CONTRADICTING_INFO
    3. Ключевые данные среды изменились с момента последнего плана → ENVIRONMENT_CHANGE
    4. Текущий план отклонился от исходной цели по ключевым словам → GOAL_DRIFT
    5. Один и тот же план используется >5 циклов → STALE_PLAN
    6. Потрачено >N ресурсов без реального результата → COST_EXCEEDED

    Не требует LLM — детерминированное решение.
    Генерирует revision_prompt для LLM если пересмотр нужен.
    """

    # Если план используется более этого числа циклов без изменения → STALE
    STALE_THRESHOLD = 5

    # Минимальное число повторных неудач для триггера REPEATED_FAILURE
    FAILURE_THRESHOLD = 3

    # Минимальная уверенность для реплана
    MIN_CONFIDENCE = 0.55

    def __init__(self):
        self._plan_history: list[tuple[float, str]] = []  # (ts, plan_fingerprint)
        self._last_revision_cycle: int = -999
        # Сколько циклов должно пройти между пересмотрами чтобы не зациклиться
        self.min_revision_gap = 3

    def _fingerprint(self, plan: str) -> str:
        """Короткий отпечаток плана для сравнения без хранения полного текста."""
        words = re.findall(r'\w{4,}', (plan or '').lower())
        return '|'.join(sorted(set(words[:20])))

    def assess(
        self,
        goal: str,
        current_plan: str | None,
        analysis: str | None,  # pylint: disable=unused-argument
        consecutive_failures: int,
        cycle_count: int,
        last_observation: dict | None = None,
        prev_observations: list[dict] | None = None,
        resources_spent: dict | None = None,
    ) -> RevisionDecision:
        """
        Основной метод: оценить, нужен ли пересмотр.

        Параметры:
            goal                — текущая цель
            current_plan        — текущий план (строка)
            analysis            — свежий анализ из _analyze()
            consecutive_failures— число неудач подряд
            cycle_count         — номер текущего цикла
            last_observation    — последнее наблюдение
            prev_observations   — предыдущие наблюдения (для ENVIRONMENT_CHANGE)
            resources_spent     — {'api_calls': N, 'tokens': N} (для COST_EXCEEDED)
        """
        no_revision = RevisionDecision(should_revise=False)

        # Антипинг-понг: не пересматриваем слишком часто
        if cycle_count - self._last_revision_cycle < self.min_revision_gap:
            return no_revision

        # ── 1. REPEATED_FAILURE ──────────────────────────────────────────────
        if consecutive_failures >= self.FAILURE_THRESHOLD:
            self._last_revision_cycle = cycle_count
            return RevisionDecision(
                should_revise=True,
                trigger=RevisionTrigger.REPEATED_FAILURE,
                confidence=min(0.95, 0.6 + 0.1 * consecutive_failures),
                explanation=f'{consecutive_failures} неудач подряд — текущий план не работает',
                revision_prompt=self._make_revision_prompt(
                    goal, current_plan, RevisionTrigger.REPEATED_FAILURE,
                    f'{consecutive_failures} циклов без результата',
                ),
            )

        # ── 2. STALE_PLAN ────────────────────────────────────────────────────
        if current_plan:
            fp = self._fingerprint(current_plan)
            self._plan_history.append((time.time(), fp))
            # Держим только последние 20 записей
            self._plan_history = self._plan_history[-20:]
            recent = self._plan_history[-self.STALE_THRESHOLD:]
            if len(recent) >= self.STALE_THRESHOLD:
                unique_fps = {r[1] for r in recent}
                if len(unique_fps) == 1:   # один и тот же план N раз
                    self._last_revision_cycle = cycle_count
                    return RevisionDecision(
                        should_revise=True,
                        trigger=RevisionTrigger.STALE_PLAN,
                        confidence=0.75,
                        explanation=f'Один и тот же план повторяется {self.STALE_THRESHOLD}+ раз',
                        revision_prompt=self._make_revision_prompt(
                            goal, current_plan, RevisionTrigger.STALE_PLAN,
                            f'план не менялся {self.STALE_THRESHOLD} циклов',
                        ),
                    )

        # ── 3. GOAL_DRIFT ────────────────────────────────────────────────────
        if current_plan and goal:
            drift = self._detect_goal_drift(goal, current_plan)
            if drift > 0.7:
                self._last_revision_cycle = cycle_count
                return RevisionDecision(
                    should_revise=True,
                    trigger=RevisionTrigger.GOAL_DRIFT,
                    confidence=drift,
                    explanation='Текущий план значительно отклонился от исходной цели',
                    revision_prompt=self._make_revision_prompt(
                        goal, current_plan, RevisionTrigger.GOAL_DRIFT,
                        'план не соответствует цели',
                    ),
                )

        # ── 4. ENVIRONMENT_CHANGE ────────────────────────────────────────────
        if prev_observations and last_observation:
            changed = self._detect_env_change(prev_observations, last_observation)
            if changed:
                self._last_revision_cycle = cycle_count
                return RevisionDecision(
                    should_revise=True,
                    trigger=RevisionTrigger.ENVIRONMENT_CHANGE,
                    confidence=0.70,
                    explanation='Среда значительно изменилась — старый план устарел',
                    revision_prompt=self._make_revision_prompt(
                        goal, current_plan, RevisionTrigger.ENVIRONMENT_CHANGE,
                        'новые данные из среды требуют другого подхода',
                    ),
                )

        # ── 5. COST_EXCEEDED ────────────────────────────────────────────────
        if resources_spent:
            tokens = resources_spent.get('tokens', 0)
            api_calls = resources_spent.get('api_calls', 0)
            if tokens > 50_000 or api_calls > 30:
                self._last_revision_cycle = cycle_count
                return RevisionDecision(
                    should_revise=True,
                    trigger=RevisionTrigger.COST_EXCEEDED,
                    confidence=0.80,
                    explanation=f'Высокий расход ресурсов: токены={tokens}, api_calls={api_calls}',
                    revision_prompt=self._make_revision_prompt(
                        goal, current_plan, RevisionTrigger.COST_EXCEEDED,
                        'оптимизируй план: он слишком дорогой',
                    ),
                )

        return no_revision

    def _detect_goal_drift(self, goal: str, plan: str) -> float:
        """
        Косинусное-like расстояние по ключевым словам.
        Возвращает 0.0 (нет дрейфа) … 1.0 (план не связан с целью).
        """
        def extract_kw(text: str) -> set[str]:
            return set(w.lower() for w in re.findall(r'\b\w{4,}\b', text) if w.isalpha())

        goal_kw = extract_kw(goal)
        plan_kw = extract_kw(plan)
        if not goal_kw:
            return 0.0
        # Jaccard distance
        intersection = goal_kw & plan_kw
        union = goal_kw | plan_kw
        jaccard = len(intersection) / len(union) if union else 1.0
        return 1.0 - jaccard  # drift = 1 - similarity

    def _detect_env_change(
        self,
        prev_observations: list[dict],
        last_observation: dict,
    ) -> bool:
        """
        Детектирует существенное изменение среды.
        Сравнивает ключевые числовые метрики.
        """
        if not prev_observations:
            return False
        prev = prev_observations[-1]

        # Проверяем изменение success_rate/errors из _progress
        prev_p = prev.get('_progress', {})
        last_p = last_observation.get('_progress', {})

        prev_fails = prev_p.get('consecutive_failures', 0)
        last_fails = last_p.get('consecutive_failures', 0)
        # Резкий рост ошибок — среда изменилась
        if last_fails - prev_fails >= 3:
            return True

        # Изменение hardware-метрик >50%
        prev_hw = prev.get('_hardware', {})
        last_hw = last_observation.get('_hardware', {})
        if prev_hw and last_hw:
            prev_cpu = prev_hw.get('cpu_percent', 0) or 0
            last_cpu = last_hw.get('cpu_percent', 0) or 0
            if abs(last_cpu - prev_cpu) > 50:
                return True

        return False

    def _make_revision_prompt(
        self,
        goal: str,
        current_plan: str | None,
        trigger: RevisionTrigger,
        reason: str,
    ) -> str:
        """Строит промпт для LLM, требующий пересмотра плана."""
        old_plan_preview = ''
        if current_plan:
            # Показываем только первые 300 символов старого плана
            old_plan_preview = f'\nСтарый план (первые 300 символов):\n{current_plan[:300]}\n'

        trigger_instructions = {
            RevisionTrigger.REPEATED_FAILURE: (
                'Текущая стратегия не работает. Попробуй принципиально другой подход:\n'
                '- Разбей задачу иначе\n'
                '- Используй другие инструменты\n'
                '- Проверь, правильно ли понята цель'
            ),
            RevisionTrigger.STALE_PLAN: (
                'Один и тот же план повторяется без прогресса.\n'
                'Сгенерируй качественно новый план с другой последовательностью шагов.'
            ),
            RevisionTrigger.GOAL_DRIFT: (
                'Текущий план ушёл от исходной цели. Вернись к цели и перепланируй.\n'
                'Задай себе: "Что именно должно быть сделано согласно цели?"'
            ),
            RevisionTrigger.ENVIRONMENT_CHANGE: (
                'Условия изменились. Пересмотри план с учётом новых данных.\n'
                'Адаптируй шаги к актуальному состоянию среды.'
            ),
            RevisionTrigger.COST_EXCEEDED: (
                'Расход ресурсов превысил норму. Оптимизируй план:\n'
                '- Убери лишние шаги\n'
                '- Используй кэш/локальные данные\n'
                '- Выполни задачу минимальным числом действий'
            ),
            RevisionTrigger.CONTRADICTING_INFO: (
                'Новые данные противоречат текущему плану. Скорректируй его.\n'
                'Учти актуальную информацию при перепланировании.'
            ),
        }
        instruction = trigger_instructions.get(trigger, 'Пересмотри текущий план.')

        return (
            f'[DECISION_REVISION] Причина пересмотра: {reason}\n'
            f'Триггер: {trigger.value}\n'
            f'{old_plan_preview}'
            f'\n{instruction}\n'
            f'\nЦель: {goal}\n'
            f'Составь НОВЫЙ план в формате исполняемых блоков (```python или ```bash).'
        )
