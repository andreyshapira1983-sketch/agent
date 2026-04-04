# Continuous Autonomous Loop (непрерывный цикл работы) — Слой 20
# Архитектура автономного AI-агента
# Основной цикл: observe → analyze → plan → act → evaluate → learn → repair → improve → repeat
# pylint: disable=broad-except

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import traceback
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from evaluation.failure_taxonomy import FailureCategory, FailureTracker, RECOVERY_POLICIES
from knowledge.operational_memory import OperationalMemory
from skills.structured_skills import StructuredSkill, StructuredSkillRegistry


# ── Конфигурация магических чисел цикла ───────────────────────────────────────


@dataclass
class LoopConfig:
    """Все настраиваемые параметры AutonomousLoop в одном месте."""

    # Обучение
    learn_batch_size: int = 7           # LLM-обучение батчем каждые N циклов
    replay_interval: int = 10           # replay каждые N циклов
    replay_failure_batch: int = 5       # мин. провалов для внеочередного replay
    replay_pattern_interval: int = 20   # find_patterns каждые N циклов
    knowledge_verify_interval: int = 3  # верификация знаний каждые N циклов

    # Безопасность
    max_consecutive_failures: int = 10  # стоп после N неудач подряд
    max_repairs_per_cycle: int = 3      # лимит ремонтов за 1 цикл

    # Периодические задачи (интервалы в циклах)
    checkpoint_interval: int = 10       # авто-чекпоинт
    evolution_log_interval: int = 10    # запись эволюции
    goal_review_interval: int = 50      # пересмотр целей
    retrospective_interval: int = 20    # глубокая ретроспектива
    hardware_check_interval: int = 5    # мониторинг железа
    benchmark_interval: int = 50        # бенчмарки
    data_lifecycle_interval: int = 50   # обслуживание базы знаний
    learning_stats_interval: int = 50   # статистика обучения
    capability_scan_interval: int = 100 # поиск capability gaps
    lint_interval: int = 30             # линтер

    # Автогенерация целей
    scan_workdir_interval: int = 5      # сканирование рабочей папки
    goal_from_reflection_interval: int = 10
    goal_from_inventory_interval: int = 20

    # Навыки
    skill_queue_interval: int = 5       # обработка очереди навыков
    skill_prune_interval: int = 3       # проверка слабых навыков

    # Стратегии и self-improvement
    strategy_review_interval: int = 10  # аудит бесполезных стратегий
    self_improve_interval: int = 5      # цикл self-improvement / оптимизация стратегий

    # Upwork
    upwork_fail_threshold: int = 3      # порог неудач для кулдауна
    upwork_cooldown_sec: int = 7200     # кулдаун (секунды)
    job_hunt_interval: int = 10         # поиск задач каждые N циклов


class LoopPhase(Enum):
    OBSERVE   = 'observe'
    ANALYZE   = 'analyze'
    PLAN      = 'plan'
    SIMULATE  = 'simulate'   # новая фаза: проверка плана в песочнице
    ACT       = 'act'
    EVALUATE  = 'evaluate'
    LEARN     = 'learn'
    REPLAY    = 'replay'     # переанализ прошлого опыта
    REPAIR    = 'repair'
    IMPROVE   = 'improve'
    IDLE      = 'idle'
    STOPPED   = 'stopped'


class LoopCycle:
    """Хранит данные одного прохода цикла."""

    def __init__(self, cycle_id: int):
        self.cycle_id = cycle_id
        self.phase = LoopPhase.IDLE
        self.observation: dict | None = None
        self.analysis = None
        self.plan = None
        self.simulation: str | None = None   # результат проверки в sandbox
        self.action_result: Any = None
        self.evaluation: Any = None
        self.learning: Any = None
        self.repair: list | None = None
        self.improvement = None
        self.errors: list[str] = []
        self.success = False
        # Confidence propagation: уверенность каждой фазы (0.0..1.0)
        self.confidence: dict[str, float] = {
            'observe':   1.0,
            'analyze':   1.0,
            'plan':      1.0,
            'simulate':  1.0,
            'act':       1.0,
            'evaluate':  1.0,
        }

    @property
    def overall_confidence(self) -> float:
        """Итоговая уверенность цикла — минимум по всем фазам."""
        return min(self.confidence.values()) if self.confidence else 1.0

    def to_dict(self):
        return {
            'cycle_id': self.cycle_id,
            'phase': self.phase.value,
            'observation': self.observation,
            'analysis': self.analysis,
            'plan': self.plan,
            'simulation': self.simulation,
            'action_result': self.action_result,
            'evaluation': self.evaluation,
            'learning': self.learning,
            'repair': self.repair,
            'improvement': self.improvement,
            'errors': self.errors,
            'success': self.success,
            'confidence': self.confidence,
            'overall_confidence': round(self.overall_confidence, 2),
        }


class JobHunterProtocol(Protocol):
    """Минимальный контракт для job_hunter, используемый в AutonomousLoop."""

    def hunt(self) -> int:
        ...


class LearningQualityTracker:
    """
    Отслеживает качество стратегий через Байесовскую оценку.

    Байесовская модель Beta(hits+1, misses+1):
        quality_score = (hits + 1) / (uses + 2)

        - Новая стратегия (uses=0): score = 1/2 = 0.5  — нейтральный prior
        - 1 успех из 1:   score = 2/3 ≈ 0.67  — осторожно оптимистично
        - 8 из 10:        score = 9/12 = 0.75  — уверенно хорошо
        - 2 из 10:        score = 3/12 = 0.25  — уверенно плохо
        → Нет хардкода 0.5, нет деления на ноль, точность растёт с данными.

    Confidence = min(uses / 10.0, 1.0)
        → 0.0 для новых, 1.0 при 10+ наблюдениях.

    Decay: каждые N вызовов record_use() старые счётчики умножаются на 0.95,
        чтобы свежие данные весили больше устаревших.
    """

    _DECAY_FACTOR  = 0.95   # затухание старых данных
    _DECAY_EVERY   = 20     # раз в N записей применять decay

    def __init__(self):
        self._scores: dict[str, dict] = {}
        # area → {'uses': float, 'hits': float, 'misses': float}
        self._total_records: int = 0

    def record_use(self, area: str, success: bool):
        """Фиксирует использование стратегии и результат."""
        if area not in self._scores:
            self._scores[area] = {'uses': 0.0, 'hits': 0.0, 'misses': 0.0}
        s = self._scores[area]
        s['uses']  += 1
        if success:
            s['hits']  += 1
        else:
            s['misses'] += 1

        self._total_records += 1
        # Периодически затухаем, чтобы свежие данные весили больше
        if self._total_records % self._DECAY_EVERY == 0:
            self._apply_decay()

    def quality_score(self, area: str) -> float:
        """
        Байесовская оценка качества стратегии: 0.0..1.0.

        Формула: (hits + 1) / (uses + 2)  — Beta(hits+1, misses+1) posterior mean.
        Никогда не возвращает хардкод: честная оценка с неопределённостью.
        """
        s = self._scores.get(area)
        if not s:
            return 0.5   # стратегия неизвестна — prior = 0.5
        hits = s['hits']
        uses = s['uses']
        return (hits + 1.0) / (uses + 2.0)

    def confidence(self, area: str) -> float:
        """Уверенность в оценке: 0.0 (нет данных) → 1.0 (10+ наблюдений)."""
        s = self._scores.get(area)
        if not s:
            return 0.0
        return min(s['uses'] / 10.0, 1.0)

    def weighted_score(self, area: str) -> float:
        """Quality, взвешенное по confidence. Новые стратегии тянутся к 0.5."""
        q = self.quality_score(area)
        c = self.confidence(area)
        return q * c + 0.5 * (1.0 - c)

    def get_poor_strategies(self, min_uses: int = 5) -> list[str]:
        """Стратегии, которые НЕ помогают (Байесовский score < 0.25)."""
        return [
            area for area, s in self._scores.items()
            if s['uses'] >= min_uses and self.quality_score(area) < 0.25
        ]

    def get_effective_strategies(self, min_uses: int = 3) -> list[str]:
        """Стратегии, которые реально помогают (Байесовский score > 0.7)."""
        return [
            area for area, s in self._scores.items()
            if s['uses'] >= min_uses and self.quality_score(area) > 0.7
        ]

    def get_ranked(self, min_confidence: float = 0.2) -> list[tuple[str, float, float]]:
        """
        Все стратегии, отсортированные по weighted_score DESC.
        Возвращает список (area, quality, confidence).
        """
        result = []
        for area in self._scores:
            c = self.confidence(area)
            if c >= min_confidence:
                result.append((area, self.quality_score(area), c))
        result.sort(key=lambda x: x[1], reverse=True)
        return result

    def _apply_decay(self):
        """Умножает все счётчики на DECAY_FACTOR — свежие данные важнее."""
        for s in self._scores.values():
            s['uses']   *= self._DECAY_FACTOR
            s['hits']   *= self._DECAY_FACTOR
            s['misses'] *= self._DECAY_FACTOR

    def summary(self) -> dict:
        return {
            area: {
                'uses':       round(s['uses'], 1),
                'hits':       round(s['hits'], 1),
                'quality':    round(self.quality_score(area), 3),
                'confidence': round(self.confidence(area), 2),
            }
            for area, s in self._scores.items()
        }

    def to_dict(self) -> dict:
        return dict(self._scores)

    def load_from_dict(self, data: dict):
        if isinstance(data, dict):
            self._scores.update(data)

    def get_uses(self, area: str) -> float:
        """Количество использований стратегии."""
        s = self._scores.get(area)
        return round(s['uses'], 1) if s else 0.0


class AutonomousLoop:
    """
    Continuous Autonomous Loop — основной цикл работы агента (Слой 20).

    Цикл:
        observe   → получить данные из среды (Perception, Слой 1)
        analyze   → проанализировать через Cognitive Core (Слой 3)
        plan      → построить план действий
        act       → исполнить через Agent System (Слой 4)
        evaluate  → оценить результат (Reflection System, Слой 10)
        learn     → обновить знания (Knowledge System, Слой 2)
        repair    → если были ошибки — саморемонт (Self-Repair, Слой 11)
        improve   → улучшить стратегии (Self-Improvement, Слой 12)
        repeat    → следующий цикл

    Интеграции:
        - Self-Repair (Слой 11)       — автоматический ремонт при ошибках
        - Reflection (Слой 10)        — глубокий анализ результатов
        - Self-Improvement (Слой 12)  — эволюция стратегий
        - PersistentBrain             — сохранение уроков на диск
    """

    def __init__(
        self,
        perception=None,
        cognitive_core=None,
        agent_system=None,
        execution_system=None,
        knowledge_system=None,
        monitoring=None,
        human_approval=None,
        self_repair=None,
        reflection=None,
        self_improvement=None,
        persistent_brain=None,
        learning_system=None,
        experience_replay=None,
        acquisition_pipeline=None,
        sandbox=None,
        tool_layer=None,
        budget_control=None,
        cycle_delay=1.0,
        max_cycles=None,
        state_manager=None,
        goal_manager=None,
        # ── Слои, добавленные в подключение ───────────────────────────────────
        security=None,               # Слой 16: Security
        governance=None,             # Слой 21: Governance / Policy
        ethics=None,                 # Слой 42: Ethical & Value Alignment
        validation=None,             # Слой 24: Data Validation
        evaluation=None,             # Слой 25: Evaluation & Benchmarking
        env_model=None,              # Слой 27: Environment Modeling
        skill_library=None,          # Слой 29: Skill / Capability Library
        task_decomp=None,            # Слой 30: Task Decomposition Engine
        model_manager=None,          # Слой 32: Model Management
        data_lifecycle=None,         # Слой 33: Data Lifecycle Management
        distributed=None,            # Слой 34: Distributed Execution
        capability_discovery=None,   # Слой 35: Capability Discovery
        long_horizon=None,           # Слой 38: Long-Horizon Planning
        attention=None,              # Слой 39: Attention & Focus Management
        temporal=None,               # Слой 40: Temporal Reasoning
        causal=None,                 # Слой 41: Causal Reasoning
        social=None,                 # Слой 43: Social Interaction Model
        hardware=None,               # Слой 44: Hardware Interaction Layer
        identity=None,               # Слой 45: Identity & Self-Model
        knowledge_verifier=None,     # Слой 46: Knowledge Verification
        software_dev=None,           # Слой 7:  Software Development System
        multilingual=None,           # Слой 14: Multilingual Understanding
        orchestration=None,          # Слой 18: Orchestration System
        reliability=None,            # Слой 19: Reliability System
        # ── Саморазвитие: динамическое создание модулей ───────────────────────
        module_builder=None,         # ModuleBuilder  — строит новые .py модули
        agent_spawner=None,          # AgentSpawner   — динамически регистрирует агентов
        goal_generator=None,         # AutonomousGoalGenerator — сам ставит цели
        # ── Telegram-уведомления из цикла ─────────────────────────────────
        telegram_bot=None,           # TelegramBot — для отправки уведомлений
        telegram_chat_id=None,       # int — chat_id куда слать уведомления
        telegram_channel_id=None,    # int/str — канал для автопостинга достижений
        config: LoopConfig | None = None,  # конфигурация магических чисел
    ):
        self.config = config or LoopConfig()
        self.perception = perception
        self.cognitive_core = cognitive_core
        self.agent_system = agent_system
        self.execution_system = execution_system
        self.knowledge_system = knowledge_system
        self.monitoring = monitoring
        self.human_approval = human_approval
        self.self_repair = self_repair
        self.reflection = reflection
        self.self_improvement = self_improvement
        self.persistent_brain = persistent_brain
        self.learning_system = learning_system
        self.experience_replay = experience_replay
        self.acquisition_pipeline = acquisition_pipeline
        self.sandbox = sandbox
        self.tool_layer = tool_layer
        self.budget_control = budget_control
        self.cycle_delay = max(0.5, cycle_delay)
        self.max_cycles = max_cycles
        self.state_manager = state_manager
        self.goal_manager = goal_manager
        # ── Новые слои ────────────────────────────────────────────────────────
        self.security = security
        self.governance = governance
        self.ethics = ethics
        self.validation = validation
        self.evaluation = evaluation
        self.env_model = env_model
        self.skill_library = skill_library
        self.task_decomp = task_decomp
        self.model_manager = model_manager
        self.data_lifecycle = data_lifecycle
        self.distributed = distributed
        self.capability_discovery = capability_discovery
        self.long_horizon = long_horizon
        self.attention = attention
        self.temporal = temporal
        self.causal = causal
        self.social = social
        self.hardware = hardware
        self.identity = identity
        self.knowledge_verifier = knowledge_verifier
        self.software_dev = software_dev
        self.multilingual = multilingual
        self.orchestration = orchestration
        self.reliability = reliability
        self.module_builder = module_builder
        self.agent_spawner = agent_spawner
        self.goal_generator = goal_generator
        self.telegram_bot = telegram_bot
        self.telegram_chat_id = telegram_chat_id
        self.telegram_channel_id = telegram_channel_id
        self._web_interface = None
        self._channel_post_every = 5   # постить не чаще чем раз в 5 успешных циклов
        self._channel_success_count = 0  # счётчик успешных циклов
        self._last_upwork_hash = ""   # хеш последнего содержимого upwork_jobs.txt
        # Публичный контейнер фонового LLM, назначается из build_agent.
        self.background_llm: object | None = None
        # Backward compatibility для старого пути через private-атрибут.
        self._background_llm: object | None = None
        # Кулдаун Upwork-мониторинга: если N цикл подряд неудача — пропускаем
        self._upwork_fail_count: int = 0
        self._UPWORK_FAIL_THRESHOLD = self.config.upwork_fail_threshold     # после 3 неудач — кулдаун
        self._UPWORK_COOLDOWN_SEC = self.config.upwork_cooldown_sec  # 2 часа кулдаун
        # Если Upwork-credentials не настроены — сразу ставим кулдаун на 7 дней
        _upwork_configured = bool(
            os.environ.get('UPWORK_CLIENT_ID') or os.environ.get('UPWORK_ACCESS_TOKEN')
        )
        self._upwork_skip_until: float = (
            0.0 if _upwork_configured else time.time() + 7 * 24 * 3600
        )
        self._skill_training_index = 0
        self._active_training_skill_name: str | None = None
        self._skill_training_last_cycle: dict[str, int] = {}

        self._running = False
        self._cycle_count = 0
        self._current_cycle: LoopCycle | None = None
        self._history: list[LoopCycle] = []
        self._goal = None
        self._consecutive_failures = 0
        # Ограничиваем саморемонт за цикл, чтобы не тратить весь цикл на "лечение себя".
        self._max_repairs_per_cycle = self.config.max_repairs_per_cycle
        # Anti ping-pong: не более 1 fail-closed реплана за N циклов
        self._fail_closed_replan_min_gap_cycles = 5
        self._last_fail_closed_replan_cycle = -10_000
        self.learning_quality = LearningQualityTracker()
        self.job_hunter: JobHunterProtocol | None = None
        self._analysis_cache_signature = None
        self._analysis_cache_value = None
        self._analysis_cache_reuse = 0
        self._last_obs_hash: str = ''            # P3: хэш последнего наблюдения для skip-gate
        self._plan_cache_signature = None
        self._plan_cache_value = None
        self._plan_cache_reuse = 0
        self._last_acquisition_stats: dict = {
            'last_run_cycle': None,
            'queue_size': 0,
            'total': 0,
            'stored': 0,
            'filtered': 0,
            'failed': 0,
        }

        # Состояние прошлого цикла — передаётся в observe следующего
        self._last_cycle_summary: str = ""     # что было сделано
        self._last_cycle_success: bool | None = None
        self._last_cycle_errors: list[str] = []
        self._completed_steps: list[str] = []   # накопленные выполненные шаги

        # Декомпозиция цели (GoalManager)
        self._subgoal_queue: list[str] = []     # очередь подцелей
        self._subgoal_id_queue: list[str] = []  # очередь id подцелей
        self._goal_decomposed: bool = False     # декомпозиция уже сделана?
        self._current_goal_id: str | None = None
        self._subgoal_fail_counts: dict[str, int] = {}  # провалы на подцель
        self._max_subgoal_failures: int = 3              # порог для пере-декомпозиции

        # Граф декомпозиции активной подцели (TaskDecompositionEngine, Слой 30)
        # Персистентен между циклами: агент проходит шаги графа последовательно,
        # не пересоздавая граф каждый раз.
        self._task_graph = None                         # TaskGraph | None
        self._task_graph_goal: str = ''                 # к какой цели граф построен
        self._task_graph_current_id: str | None = None  # исполняемый узел графа

        # ── Буфер отложенного вывода (для синхронизации Telegram ↔ консоль) ──
        # Проблема: при BLOCKED плане консоль выводит ошибку, а Reflection потом
        # переписывает результат для Telegram. Буфер предотвращает рассинхронизацию.
        self._defer_console_output = False      # флаг буферизации
        self._deferred_logs: list[tuple[str, str]] = []  # (level, message)

        # ── Очередь прерываний (Interrupt Handling) ──────────────────────────
        # Каждое прерывание: {'priority': int 1..5, 'event': str, 'source': str}
        # priority=1 — критическое (прерывает текущий цикл немедленно)
        # priority=2 — высокое    (перепланирование до действия)
        # priority=3..5 — обычные (копятся, обрабатываются в следующем цикле)
        self._interrupt_queue: list[dict] = []

        # ── Замыкание петли обучения: ошибки → детерминированный код ──────────
        # Проблема: self_repair/experience_replay сохраняют ТЕКСТ урока, но текст
        # попадает в LLM-контекст и LLM снова генерирует сломанный код.
        # Решение: считаем частоту каждой ошибки. При ≥3 срабатываниях одного
        # паттерна — автоматически регистрируем встроенный шаблон (LOCAL_SKILL),
        # который будет использоваться вместо LLM для этой задачи.
        self._error_hit_counter: dict[str, int] = {}   # norm_pattern → count
        self._error_goal_keywords: dict[str, list[str]] = {}  # norm_pattern → goal words
        self._dynamic_skills: list[tuple[list[str], str]] = []  # runtime-learned skills
        # Performance Logger + Fitness Gate + Champion strategy state
        self._fitness_events: list[dict] = []
        self._champion_strategy: dict[str, dict] = {}
        _mem_dir = '.agent_memory'
        if persistent_brain and hasattr(persistent_brain, 'data_dir'):
            _mem_dir = persistent_brain.data_dir
        self._dynamic_skills_path = os.path.join(_mem_dir, 'local_skills.json')
        self._load_dynamic_skills()

        # ── P1: Failure Taxonomy — классификация провалов ─────────────────────
        self.failure_tracker = FailureTracker(history_size=500)

        # ── P1: Stop Conditions — жёсткие правила остановки бессмысленной работы
        self._action_fingerprints: list[str] = []   # последние fingerprints действий
        self._replan_count: int = 0                   # подряд реплановзов без нового графа
        self._last_verify_results: list[bool] = []    # последние 3 результата verify

        # ── P1: Learn/Replay throttle — урезаем LLM-рефлексию ────────────────
        self._learn_batch_buffer: list[dict] = []     # накопленные данные для батч-обучения
        self._LEARN_BATCH_SIZE = self.config.learn_batch_size                    # обучаться батчем каждые N циклов
        self._last_learn_cycle: int = 0               # последний цикл с learn
        self._REPLAY_INTERVAL = self.config.replay_interval                    # replay каждые N циклов (было 3)
        self._REPLAY_FAILURE_BATCH = self.config.replay_failure_batch                # минимум эпизодов провалов для replay

        # ── P2: Operational Memory — процедурная + failure память ─────────────
        self.operational_memory = OperationalMemory(data_dir=_mem_dir)

        # ── P1: Structured Skills — навыки как исполняемые шаблоны ────────────
        self.structured_skills = StructuredSkillRegistry(data_dir=_mem_dir)
        # Импортируем legacy skills в structured формат
        if self._dynamic_skills:
            self.structured_skills.import_legacy_skills(self._dynamic_skills)

        # ── Адаптивное мышление: конфликты / неполные данные / пересмотр ─────
        from reasoning.adaptive_reasoning import (
            GoalConflictResolver,
            IncompletenessDetector,
            DecisionRevisor,
        )
        self._conflict_resolver   = GoalConflictResolver()
        self._incompleteness      = IncompletenessDetector()
        self._decision_revisor    = DecisionRevisor()
        # Кольцевой буфер последних наблюдений для DetectEnvChange
        self._observation_history: list[dict] = []

        # ActionDispatcher: мост между планом LLM и реальными инструментами
        if tool_layer or execution_system:
            from execution.action_dispatcher import ActionDispatcher
            # Deny-by-default: оборачиваем tool_layer через ToolBroker (§5-§6)
            _enforced_tl = tool_layer
            if tool_layer:
                try:
                    from safety.deny_policy import PolicyEnforcedToolLayer
                    from tools.tool_broker import ToolBroker
                    _broker = ToolBroker(
                        tool_layer=tool_layer,
                        audit_journal=getattr(evaluation, 'audit_journal', None) if evaluation else None,
                        monitoring=monitoring,
                    )
                    _enforced_tl = PolicyEnforcedToolLayer(
                        tool_layer=tool_layer,
                        broker=_broker,
                        worker_id='autonomous_loop',
                    )
                except Exception as _epol:
                    self._log_exc("deny_policy_init", _epol)
                    _enforced_tl = tool_layer
            self.action_dispatcher = ActionDispatcher(
                tool_layer=_enforced_tl,
                execution_system=execution_system,
                monitoring=monitoring,
                llm=getattr(cognitive_core, 'llm', None),
                acquisition_pipeline=acquisition_pipeline,
                module_builder=module_builder,
                security=security,
            )
        else:
            self.action_dispatcher = None

        # TaskExecutor: прямое исполнение через tool_layer без LLM (fallback)
        if tool_layer:
            try:
                from execution.task_executor import TaskExecutor
                _tex_wd = (
                    getattr(tool_layer, 'working_dir', None)
                    or getattr(tool_layer.get('terminal'), 'working_dir', None)
                    or os.getcwd()
                )
                self.task_executor = TaskExecutor(
                    tool_layer=_enforced_tl,  # type: ignore[possibly-unbound]
                    working_dir=_tex_wd,
                )
            except Exception as _e:
                self._log_exc("init", _e)
                self.task_executor = None
        else:
            self.task_executor = None

    # ── Управление циклом ─────────────────────────────────────────────────────

    def set_goal(self, goal):
        """Устанавливает текущую цель агента."""
        self._goal = goal
        # Сбрасываем состояние декомпозиции при смене цели
        self._goal_decomposed = False
        self._subgoal_queue = []
        self._subgoal_id_queue = []
        self._subgoal_fail_counts = {}
        self._current_goal_id = None
        # Сбрасываем граф тактической декомпозиции
        self._task_graph = None
        self._task_graph_goal = ''
        self._task_graph_current_id = None
        # Сбрасываем отпечатки действий — иначе старые остатки
        # вызовут ложный anti-loop при новой цели
        self._action_fingerprints = []
        if self.persistent_brain:
            self.persistent_brain.record_evolution(
                event="goal_set",
                details=f"Новая цель: {str(goal)[:200]}",
            )

    def start(self, goal=None):
        """Запускает автономный цикл."""
        if goal:
            self._goal = goal
        self._running = True
        self._log(f"Автономный цикл запущен. Цель: {str(self._goal)[:80]}...")
        if self.persistent_brain:
            self.persistent_brain.record_evolution(
                event="loop_started",
                details=f"Цель: {str(self._goal)[:200]}",
            )
        self._run_loop()

    def stop(self):
        """Останавливает цикл после текущего прохода."""
        self._running = False
        self._log("Автономный цикл остановлен.")
        if self.persistent_brain:
            self.persistent_brain.record_evolution(
                event="loop_stopped",
                details=f"Циклов выполнено: {self._cycle_count}. "
                        f"Последовательных неудач: {self._consecutive_failures}.",
            )

    def step(self) -> LoopCycle:
        """Выполняет один проход цикла вручную."""
        return self._execute_cycle()

    @property
    def is_running(self) -> bool:
        """Публичное свойство: цикл запущен?"""
        return self._running

    @property
    def current_goal(self):
        """Публичное свойство: текущая цель."""
        return self._goal

    @staticmethod
    def _preview(value, limit: int = 200) -> str:
        """Безопасно приводит любое значение к короткой строке для логов и памяти."""
        if value is None:
            return ""
        return str(value)[:limit]

    def _is_skill_training_mode(self) -> bool:
        """Режим, в котором цикл системно обучает зарегистрированные навыки."""
        goal_text = str(self._goal or '').lower()
        if not goal_text:
            return False
        learn_markers = ('обуч', 'тренир', 'practice', 'train', 'learn')
        skill_markers = ('навык', 'скилл', 'skill', 'skills', 'skilllibrary')
        return any(m in goal_text for m in learn_markers) and any(
            m in goal_text for m in skill_markers
        )

    def _select_training_skill(self):
        """Выбирает следующий навык для тренировки по принципу least-practiced."""
        if not self.skill_library:
            return None
        candidates = self.skill_library.get_training_candidates()
        if not candidates:
            return None
        index = self._skill_training_index % len(candidates)
        skill = candidates[index]
        self._skill_training_index = (self._skill_training_index + 1) % len(candidates)
        self._active_training_skill_name = skill.name
        return skill

    def _get_active_training_skill(self):
        if not self.skill_library or not self._active_training_skill_name:
            return None
        return self.skill_library.get(self._active_training_skill_name)

    def _queue_training_sources_for_skill(self, skill) -> int:
        """Ставит в очередь реальные веб-источники для изучения конкретного навыка."""
        perception = self.perception
        if not self.learning_system or not self.tool_layer or not perception:
            return 0

        last_cycle = self._skill_training_last_cycle.get(skill.name)
        if last_cycle is not None and (self._cycle_count - last_cycle) < 5:
            return 0

        query = (
            f"{skill.name} {skill.description} "
            f"best practices tutorial examples 2026"
        )
        try:
            search_result = self.tool_layer.use('search', query=query, num_results=3)
        except Exception as e:
            self._log_exc("train", e)
            return 0

        if not isinstance(search_result, dict) or not search_result.get('success'):
            self._log(f"[train] Нет результатов поиска для навыка '{skill.name}'.")
            return 0

        queued = 0
        for item in search_result.get('results', [])[:3]:
            url = str(item.get('url', '') or '').strip()
            title = str(item.get('title', '') or '').strip()
            if not url:
                continue

            def _fetch(url=url, title=title, _perception=perception):
                page = _perception.fetch_web(url)
                if not isinstance(page, dict):
                    return ''
                text = str(page.get('text', '') or '').strip()
                page_title = str(page.get('title', '') or title or '').strip()
                if not text:
                    return ''
                return f"Title: {page_title}\nURL: {url}\n\n{text[:7000]}"

            self.learning_system.enqueue(
                source_type='web',
                source_name=f"skill_training:{skill.name}:{title or url}",
                fetch_fn=_fetch,
                tags=['skill_training', skill.name] + list(skill.tags[:3]),
            )
            queued += 1

        if queued:
            self._skill_training_last_cycle[skill.name] = self._cycle_count
            self._log(
                f"[train] В очередь обучения поставлено {queued} источников для навыка '{skill.name}'."
            )
        return queued

    def _has_real_work(self, cycle: LoopCycle) -> bool:
        """Определяет, были ли в цикле реальные исполняемые действия."""
        if isinstance(cycle.action_result, dict):
            exec_r = self._extract_execution_result(cycle.action_result)
            if self.action_dispatcher:
                return self._has_useful_execution(exec_r)

            exec_r = exec_r or cycle.action_result
            if isinstance(exec_r, dict) and 'actions_found' in exec_r:
                return self._has_useful_execution(exec_r)
            return True

        return cycle.action_result is not None and self.action_dispatcher is None

    def _execution_fully_successful(self, cycle: LoopCycle) -> bool:
        """Проверяет, что исполнение действий прошло без частичных провалов."""
        if not self.action_dispatcher:
            return True
        if not isinstance(cycle.action_result, dict):
            return False

        execution = self._extract_execution_result(cycle.action_result)
        if not isinstance(execution, dict):
            return False

        # Полный успех: dispatcher явно пометил execution как success.
        return bool(execution.get('success') is True)

    def _verify_action_results(self, cycle: LoopCycle):
        """Проверяет реальные результаты действий, а не просто отсутствие crash."""
        ar = cycle.action_result
        if not ar or not isinstance(ar, dict):
            return
        execution = self._extract_execution_result(ar)
        if not isinstance(execution, dict):
            return
        results = execution.get('results', [])
        if not results:
            return
        for r in results:
            if not isinstance(r, dict):
                continue
            if not r.get('success'):
                continue
            atype = str(r.get('type', '')).casefold()
            output = str(r.get('output', ''))
            stderr = str(r.get('stderr', ''))

            # 1. bash: stderr содержит реальные ошибки
            if atype == 'bash' and stderr:
                for marker in ('error:', 'fatal:', 'traceback',
                               'permission denied', 'not found'):
                    if marker in stderr.lower():
                        r['success'] = False
                        r['error'] = f"stderr содержит ошибку: {stderr[:200]}"
                        cycle.errors.append(f"verify:bash: {stderr[:120]}")
                        break

            # 2. python: traceback в stdout
            if atype == 'python' and 'Traceback (most recent call last)' in output:
                r['success'] = False
                r['error'] = f"Python traceback в output: {output[-200:]}"
                cycle.errors.append("verify:python: traceback в output")

            # 3. write: проверяем что файл реально создан
            if atype == 'write':
                path = (
                    r.get('input', {}).get('path', '')
                    if isinstance(r.get('input'), dict) else ''
                )
                if path and not os.path.exists(path):
                    r['success'] = False
                    r['error'] = f"Файл не создан: {path}"
                    cycle.errors.append(f"verify:write: файл не создан {path}")

        # Пересчитываем общий success
        if results:
            execution['success'] = all(r.get('success') for r in results if isinstance(r, dict))

        # P2: ActionResultContract — типизированная верификация по контракту
        # ПРИМЕЧАНИЕ: action_dispatcher уже проверяет контракты inline.
        # Здесь — вторая линия проверки для write/bash/python (кроме search,
        # т.к. пустой поиск — не ошибка, а информативный результат).
        try:
            from evaluation.action_contracts import contract_for_action_type, verify_contract
            for r in results:
                if not isinstance(r, dict):
                    continue
                atype = str(r.get('type', '')).casefold()
                # Пропускаем search — пустой результат не является ошибкой
                if atype == 'search':
                    continue
                # Уже проверено action_dispatcher (contract_score присутствует)
                if 'contract_score' in r:
                    continue
                contract = contract_for_action_type(
                    action_type=atype,
                    action_input=str(r.get('input', '')),
                    action_output=str(r.get('output', '')),
                    action_success=r.get('success', False),
                    action_stderr=str(r.get('stderr', '')),
                )
                if contract is None:
                    continue
                vr = verify_contract(contract)
                r['contract_score'] = vr.score
                r['contract_passed'] = vr.passed
                if not vr.passed:
                    r['success'] = False
                    if vr.failed_checks:
                        r['error'] = f"Contract fail ({vr.score:.0%}): {', '.join(vr.failed_checks[:3])}"
                    self._log(
                        f"[verify] Contract FAIL ({vr.score:.0%}): "
                        f"{', '.join(vr.failed_checks[:3])}",
                        level='warning',
                    )
            # Ещё раз пересчитываем success после контрактов
            execution['success'] = all(r.get('success') for r in results if isinstance(r, dict))
        except Exception as _contract_err:
            self._log(
                f"[verify] ActionResultContract warn: {_contract_err}",
                level='warning',
            )

    @staticmethod
    def _is_meta_output_path(path: str) -> bool:
        """Распознаёт файлы отчётности и самокомментирования, не являющиеся полезным прогрессом."""
        normalized = str(path or '').replace('\\', '/').casefold()
        meta_markers = (
            'report', 'progress', 'status', 'strategy', 'analysis',
            'отч', 'прогресс', 'статус', 'стратег', 'анализ',
            'tasks_report', 'task_report', 'performance', 'результаты_анализа',
        )
        return any(marker in normalized for marker in meta_markers)

    @staticmethod
    def _is_allowed_work_artifact(path: str) -> bool:
        """Разрешает только рабочие артефакты, а не произвольные текстовые записи."""
        normalized = str(path or '').replace('\\', '/').strip().casefold()
        if not normalized:
            return False
        if normalized.startswith('outputs/'):
            return False

        allowed_extensions = (
            '.py', '.json', '.yaml', '.yml', '.toml', '.ini', '.cfg',
            '.csv', '.tsv', '.sql', '.md',
        )
        return normalized.endswith(allowed_extensions)

    def _get_useful_results(self, execution_result) -> list[dict]:
        """Возвращает только те результаты действий, которые считаются полезной работой."""
        if not isinstance(execution_result, dict):
            return []
        results = execution_result.get('results', [])
        if not results:
            return []
        return [item for item in results if self._is_useful_result(item)]

    @staticmethod
    def _extract_execution_result(action_result):
        """Нормализует результат _act: поддерживает вложенный и плоский формат dispatch."""
        if not isinstance(action_result, dict):
            return None
        nested = action_result.get('execution')
        if isinstance(nested, dict):
            return nested
        if 'actions_found' in action_result and 'results' in action_result:
            return action_result
        # nested может быть list/str/None — не возвращаем мусор
        return None

    def _has_goal_progress_signal(self, cycle: LoopCycle) -> bool:
        """Проверяет, есть ли сильный сигнал прогресса по подцели."""
        if not cycle.success:
            return False
        return self._has_state_changing_action(cycle, require_success=True)

    def _has_state_changing_action(
        self,
        cycle: LoopCycle,
        require_success: bool = False,
    ) -> bool:
        """Определяет, был ли в цикле сигнал реального изменения состояния."""
        if not self.action_dispatcher:
            return cycle.action_result is not None and (cycle.success or not require_success)
        if not isinstance(cycle.action_result, dict):
            return False

        execution = self._extract_execution_result(cycle.action_result)
        if not isinstance(execution, dict):
            return False

        progress_types = {'bash', 'python', 'write', 'build_module'}
        for item in execution.get('results', []):
            if not isinstance(item, dict):
                continue
            action_type = str(item.get('type', '')).casefold()
            if action_type not in progress_types:
                continue
            if action_type == 'write':
                path = item.get('input', '')
                if not (
                    self._is_allowed_work_artifact(path)
                    and not self._is_meta_output_path(path)
                ):
                    continue
            if not require_success or item.get('success'):
                return True
        return False

    @staticmethod
    def _is_garbage_subgoal(desc: str) -> bool:
        """Проверяет, является ли описание подцели мусором (фрагмент таблицы, слишком короткое и т.д.)."""
        d = desc.strip()
        if not d or len(d) < 10:
            return True
        if d.startswith('|'):                # строка/заголовок markdown-таблицы
            return True
        if d.count('|') > 1:                 # содержит несколько пайпов — таблица
            return True
        if d.endswith('?') and len(d) < 30:  # обрывочный вопрос-фрагмент
            return True
        # Типичные фрагменты заголовков таблиц из LLM
        _TABLE_KEYWORDS = ('критерий успеха', 'приоритет', 'зависит от',
                           'запустить первой', 'подцель №', '--- |', ':---')
        dl = d.lower()
        if any(kw in dl for kw in _TABLE_KEYWORDS):
            return True
        return False

    def _set_subgoal_queue(self, sub_goals) -> None:
        """Синхронизирует локальные очереди описаний и id подцелей."""
        filtered = [sg for sg in sub_goals
                    if not self._is_garbage_subgoal(sg.description)]
        if len(filtered) < len(sub_goals):
            dropped = len(sub_goals) - len(filtered)
            self._log(f"[subgoal_queue] Отфильтровано {dropped} мусорных подцелей из {len(sub_goals)}")
        # Hard-cap: максимум 7 подцелей
        _MAX = 7
        if len(filtered) > _MAX:
            self._log(f"[subgoal_queue] Обрезаю {len(filtered)} подцелей до {_MAX}")
            filtered = filtered[:_MAX]
        self._subgoal_queue = [sg.description for sg in filtered]
        self._subgoal_id_queue = [sg.goal_id for sg in filtered]

    def _pick_next_goal(self) -> str | None:
        """Берёт следующую ожидающую цель из GoalManager.

        Если ожидающих целей нет — просит AutonomousGoalGenerator придумать
        новые из рефлексии, инвентаря способностей и рабочей папки.
        Если и после этого целей нет — возвращает None (idle).
        """
        if not self.goal_manager:
            return None

        # Отмечаем текущую цель как выполненную
        if self._current_goal_id:
            try:
                self.goal_manager.complete(self._current_goal_id)
            except Exception as _e:
                self._log_exc("plan/goal_complete", _e)

        next_goal = self.goal_manager.get_next()

        # Нет ожидающих целей — GoalGenerator генерирует автономно
        # (не требует approval — это обучение и практика, а не модификация кода)
        if not next_goal and self.goal_generator:
            self._log("[plan] Нет ожидающих целей — генерирую новые автономно...")
            try:
                self.goal_generator.generate_from_reflection()
            except Exception as _e:
                self._log_exc("plan/goal_gen", _e)
            try:
                self.goal_generator.generate_from_inventory()
            except Exception as _e:
                self._log_exc("plan/goal_gen", _e)
            try:
                self.goal_generator.generate_tool_practice_goals()
            except Exception as _e:
                self._log_exc("plan/goal_gen", _e)
            try:
                import os as _os
                _wd = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
                self.goal_generator.scan_working_dir(_wd)
            except Exception as _e:
                self._log_exc("plan/goal_gen", _e)
            # Повторно запрашиваем
            next_goal = self.goal_manager.get_next()

        if not next_goal:
            return None

        # Не берём ту же самую цель заново
        if next_goal.goal_id == self._current_goal_id:
            return None

        # Переключаемся на новую цель
        self._goal = next_goal.description
        self._goal_decomposed = False
        self._subgoal_queue = []
        self._subgoal_id_queue = []
        self._subgoal_fail_counts = {}
        self._current_goal_id = next_goal.goal_id
        self._task_graph = None
        self._task_graph_goal = ''
        self._task_graph_current_id = None
        self.goal_manager.activate(next_goal.goal_id)
        self._log(f"[plan] Следующая цель из GoalManager: {next_goal.description[:80]}")
        return next_goal.description

    def _redecompose_goal(self, failed_subgoal: str):
        """Пере-декомпозирует цель после повторных провалов подцели."""
        # Убираем провалившуюся подцель
        if failed_subgoal in self._subgoal_queue:
            idx = self._subgoal_queue.index(failed_subgoal)
            self._subgoal_queue.pop(idx)
            if idx < len(self._subgoal_id_queue):
                failed_id = self._subgoal_id_queue.pop(idx)
                if self.goal_manager and failed_id:
                    try:
                        self.goal_manager.fail(failed_id)
                    except Exception as _e:
                        self._log_exc("plan", _e)

        # Разрешаем повторную декомпозицию
        self._goal_decomposed = False
        self._subgoal_fail_counts.pop(failed_subgoal, None)
        self._log("[plan] Декомпозиция сброшена — следующий PLAN пере-разобьёт цель.")

    def _should_store_cycle_experience(self, cycle: LoopCycle) -> bool:
        """Отсеивает слабые наблюдательные циклы из обучения и replay."""
        return self._has_state_changing_action(cycle)

    def _is_useful_result(self, result: dict) -> bool:
        """Отделяет полезные действия от мета-отчётности, блокировок и нерелевантных поисков."""
        if not isinstance(result, dict) or not result.get('success'):
            return False

        action_type = str(result.get('type', '')).casefold()

        # Явно заблокированные/нерелевантные — не полезны
        err = str(result.get('error', '') or '').upper()
        if 'BLOCKED' in err or 'IRRELEVANT_RESULTS' in err:
            return False
        if result.get('relevant') is False:
            return False
        status = str(result.get('status', '') or '').lower()
        if status in ('blocked', 'non_actionable', 'skipped'):
            return False

        if action_type in {'bash', 'python'}:
            return True
        if action_type == 'write':
            path = result.get('input', '')
            # outputs/ разрешён — мета-отчёты отсеиваются отдельно
            return not self._is_meta_output_path(path)
        if action_type == 'search':
            # Поиск с непустым результатом И без флага нерелевантности
            output = (result.get('output') or '').strip()
            return bool(output)
        if action_type == 'read':
            return False
        if action_type == 'build_module':
            return True
        return False

    def _has_useful_execution(self, execution_result) -> bool:
        """Проверяет, есть ли среди выполненных действий полезный результат."""
        if not isinstance(execution_result, dict):
            return False
        if execution_result.get('actions_found', 0) <= 0:
            return False

        results = execution_result.get('results', [])
        if not results:
            return False
        return any(self._is_useful_result(item) for item in results)

    def _success_rate(self, current_success: bool | None = None) -> float:
        """Считает фактический success rate только по реальным исходам циклов."""
        total = len(self._history)
        success_count = sum(1 for item in self._history if item.success)

        if current_success is not None:
            total += 1
            success_count += int(current_success)

        return (success_count / total) if total else 0.0

    def _log_performance_event(
        self,
        area: str,
        cycle: LoopCycle,
        success_rate: float,
        consecutive_failures: int,
    ) -> dict:
        """Performance Logger: сохраняет метрики цикла для self-improvement контроллера."""
        event = {
            'ts': time.time(),
            'cycle_id': cycle.cycle_id,
            'area': area,
            'success': bool(cycle.success),
            'success_rate': round(float(success_rate), 4),
            'errors': len(cycle.errors),
            'consecutive_failures': int(consecutive_failures),
            'real_work_done': bool(self._has_real_work(cycle)),
        }
        self._fitness_events.append(event)
        if len(self._fitness_events) > 200:
            self._fitness_events = self._fitness_events[-200:]
        return event

    def _fitness_score(self, event: dict) -> float:
        """Считает fitness-score (0..1) для gate-решения о продвижении стратегии."""
        success_rate = float(event.get('success_rate', 0.0))
        errors = int(event.get('errors', 0))
        real_work = 1.0 if bool(event.get('real_work_done')) else 0.0
        consecutive_failures = int(event.get('consecutive_failures', 0))

        error_component = 1.0 - min(errors / 3.0, 1.0)
        score = 0.6 * success_rate + 0.25 * error_component + 0.15 * real_work
        if consecutive_failures >= 3:
            score -= 0.25
        return max(0.0, min(1.0, score))

    def _fitness_gate(
        self,
        area: str,
        proposal,
        cycle: LoopCycle,
        success_rate: float,
        consecutive_failures: int,
    ) -> tuple[bool, float, str]:
        """Fitness Gate: решает Promote/Reject перед применением mutation-стратегии."""
        _ = proposal
        event = self._log_performance_event(
            area=area,
            cycle=cycle,
            success_rate=success_rate,
            consecutive_failures=consecutive_failures,
        )
        score = self._fitness_score(event)

        if not event['real_work_done']:
            return False, score, 'нет полезной работы в цикле'
        if consecutive_failures >= 4:
            return False, score, f'серия неудач ({consecutive_failures})'

        champion = self._champion_strategy.get(area)
        champion_score = float(champion.get('fitness', 0.0)) if champion else 0.0
        threshold = 0.55

        if score < threshold:
            return False, score, f'fitness {score:.2f} ниже порога {threshold:.2f}'
        if champion and score + 0.02 < champion_score:
            return False, score, (
                f'хуже champion ({score:.2f} < {champion_score:.2f})'
            )
        return True, score, 'допущено fitness gate'

    def _approve_self_modification(self, action_type: str, description: str) -> bool:
        """Approval-gate для самомодификации. Без approval — блокируем."""
        if not self.human_approval:
            self._log(
                f'[self-mod] {action_type}: ПРОПУЩЕНО (human_approval не подключён)',
                level='warning',
            )
            return False
        approved = self.human_approval.request_approval(action_type, description)
        if not approved:
            self._log(f'[self-mod] {action_type}: ОТКЛОНЕНО пользователем.')
        return approved

    def _promote_champion(self, area: str, proposal, fitness: float, cycle_id: int):
        """Фиксирует текущую strategy как Champion для области (с approval)."""
        desc = str(getattr(proposal, 'proposed_change', ''))[:200]
        if not self._approve_self_modification(
            'promote_strategy',
            f'Промоция стратегии [{area}] fitness={fitness:.2f}: {desc}',
        ):
            return

        strategy_text = None
        if self.self_improvement and hasattr(self.self_improvement, 'get_strategy'):
            strategy_text = self.self_improvement.get_strategy(area)
        if not strategy_text:
            strategy_text = str(getattr(proposal, 'proposed_change', ''))[:1000]

        self._champion_strategy[area] = {
            'area': area,
            'fitness': round(float(fitness), 4),
            'cycle_id': int(cycle_id),
            'strategy': strategy_text,
            'proposal_priority': int(getattr(proposal, 'priority', 0)),
        }

    # ── Основной цикл ─────────────────────────────────────────────────────────

    _MAX_CONSECUTIVE_FAILURES = 10   # deprecated: используй config.max_consecutive_failures

    def _run_loop(self):
        while self._running:
            # ── Kill switch: файл .agent_kill → немедленная остановка ──────
            if self.action_dispatcher and hasattr(self.action_dispatcher, 'check_kill_switch'):
                kill_ok, kill_reason = self.action_dispatcher.check_kill_switch()
                if not kill_ok:
                    self._log(f"[safety] {kill_reason}", level='error')
                    self.stop()
                    break

            if self.max_cycles and self._cycle_count >= self.max_cycles:
                self._log(f"Достигнут лимит циклов: {self.max_cycles}")
                self.stop()
                break

            # ── Жёсткая остановка при серии неудач ──
            if self._consecutive_failures >= self.config.max_consecutive_failures:
                self._log(
                    f"[safety] {self._consecutive_failures} неудач подряд — "
                    f"аварийная остановка цикла.",
                    level='error',
                )
                self.stop()
                break

            self._execute_cycle()

            # ── Adaptive backpressure: увеличиваем паузу при высокой нагрузке ──
            effective_delay = self.cycle_delay
            try:
                import psutil as _ps
                _cpu = _ps.cpu_percent(interval=0)
                _ram = _ps.virtual_memory().percent
                if _cpu > 90 or _ram > 90:
                    effective_delay = max(effective_delay, 15.0)
                    self._log(
                        f"[backpressure] CPU={_cpu:.0f}% RAM={_ram:.0f}% — "
                        f"пауза {effective_delay:.0f}с",
                        level='warning',
                    )
                elif _cpu > 75 or _ram > 80:
                    effective_delay = max(effective_delay, 5.0)
            except (ImportError, OSError, RuntimeError):
                pass

            if effective_delay > 0:
                # Разбиваем на 1-секундные отрезки, чтобы stop() срабатывал быстро
                elapsed = 0.0
                while self._running and elapsed < effective_delay:
                    time.sleep(1)
                    elapsed += 1.0

    def _execute_cycle(self) -> LoopCycle:
        self._cycle_count += 1
        cycle = LoopCycle(self._cycle_count)
        self._current_cycle = cycle

        # ── Reset per-cycle rate limits ───────────────────────────────────
        if self.action_dispatcher and hasattr(self.action_dispatcher, 'reset_cycle_limits'):
            self.action_dispatcher.reset_cycle_limits()

        if self._is_skill_training_mode():
            training_skill = self._select_training_skill()
            if training_skill:
                self._log(
                    f"[train] Цикл #{cycle.cycle_id}: фокус обучения — '{training_skill.name}'."
                )

        # ── Проверка: деньги на API-ключе кончились → полная остановка цикла ──
        if (self.cognitive_core and
                hasattr(self.cognitive_core, 'brain') and
                self.cognitive_core.brain.quota_is_exhausted):
            self._log(
                "[quota] insufficient_quota — деньги на OpenAI-ключе кончились. "
                "Пополни баланс на platform.openai.com и перезапусти агента.",
                level='error',
            )
            self._running = False
            return cycle

        self._log(f"=== Цикл #{cycle.cycle_id} начат ===")

        # 1. OBSERVE
        cycle.phase = LoopPhase.OBSERVE
        cycle.observation = self._observe()
        # Confidence: данных нет → снижаем уверенность
        if not cycle.observation:
            cycle.confidence['observe'] = 0.5

        # ── INTERRUPT CHECK #1: критическое событие до анализа ────────────────
        if self._check_interrupt(cycle, phase='pre_analyze'):
            return cycle

        # 2. ANALYZE
        cycle.phase = LoopPhase.ANALYZE
        cycle.analysis = self._analyze(cycle.observation)
        # Confidence: анализ вернул пустоту → менее уверены
        if not cycle.analysis:
            cycle.confidence['analyze'] = 0.4
        elif isinstance(cycle.analysis, str) and len(cycle.analysis) < 50:
            cycle.confidence['analyze'] = 0.6

        # ── Начинаем буфер: PLAN и SIMULATE могут быть заблокированы ──────────
        self._start_deferred_output()

        # 3. PLAN
        cycle.phase = LoopPhase.PLAN
        cycle.plan = self._plan(cycle.analysis)
        # Confidence плана = confidence анализа * поправка на наличие плана
        if not cycle.plan:
            cycle.confidence['plan'] = 0.2
        else:
            cycle.confidence['plan'] = round(cycle.confidence['analyze'] * 0.95, 2)

        # 4. SIMULATE — проверка плана в песочнице перед выполнением
        cycle.phase = LoopPhase.SIMULATE
        cycle.simulation = self._simulate(cycle.plan)
        if cycle.simulation == 'BLOCKED':
            cycle.confidence['simulate'] = 0.0
        elif cycle.simulation == 'risky':
            cycle.confidence['simulate'] = 0.5

        # ── Разбираемся с буфером: если план был заблокирован, отбросим логи ───
        if cycle.simulation == 'BLOCKED':
            # Plan был заблокирован sandbox — отбрасываем все логи из PLAN-SIMULATE
            self._discard_deferred_output()
            self._end_deferred_output()
        else:
            # План OK (safe или risky) — выводим все накопленные логи
            self._flush_deferred_output()
            self._end_deferred_output()

        # Проверка бюджета перед исполнением
        if not cycle.plan:
            # План не сформирован (idle — все цели выполнены)
            self._log("[plan] Нет плана — idle цикл, ACT пропущен.")
            cycle.action_result = None
        elif self.budget_control and not self.budget_control.gate():
            exceeded_details = []
            try:
                if hasattr(self.budget_control, 'get_exceeded_details'):
                    exceeded_details = self.budget_control.get_exceeded_details()
            except Exception as _e:
                self._log_exc("budget", _e)
                exceeded_details = []

            details_text = ", ".join(exceeded_details) if exceeded_details else "unknown"
            cycle.errors.append(
                f"budget: лимит ресурсов исчерпан — ACT пропущен ({details_text})"
            )
            self._log(
                f"[budget] Лимит ресурсов исчерпан ({details_text}) — ACT пропущен, "
                "цикл продолжается (budget cooldown)."
            )
            # НЕ останавливаем цикл — агент продолжает работу без LLM-расходов,
            # используя шаблонный план и локальные действия (offline-режим).
            cycle.action_result = None
        elif cycle.simulation == 'BLOCKED':
            cycle.errors.append("simulate: план заблокирован sandbox (UNSAFE или PolicyViolation)")
            self._log("[simulate] План отклонён sandbox. Self-Repair будет обрабатывать.")
            cycle.action_result = None
        else:
            # ── INTERRUPT CHECK #2: критическое событие перед действием ────────
            if self._check_interrupt(cycle, phase='pre_act'):
                return cycle

            # 5. ACT — confidence gate перед исполнением
            cycle.phase = LoopPhase.ACT
            conf, conf_reason = self._confidence_gate(cycle.plan, cycle.cycle_id)
            # Итоговая уверенность перед действием = min(plan, gate)
            cycle.confidence['act'] = round(
                min(cycle.confidence['plan'], conf), 2
            )
            if cycle.confidence['act'] < 0.25:
                # Определяем реальную причину низкой уверенности
                if cycle.confidence.get('plan', 1.0) < 0.25:
                    act_reason = f"слабый план (уверенность плана {cycle.confidence.get('plan', 0):.0%})"
                else:
                    act_reason = conf_reason
                self._log(
                    f"[confidence] НИЗКАЯ уверенность ({cycle.confidence['act']:.0%}): "
                    f"{act_reason}. Действие выполняется, но требует проверки.",
                    level='warning',
                )
                if self.telegram_bot and self.telegram_chat_id:
                    try:
                        self.telegram_bot.send(
                            self.telegram_chat_id,
                            f"⚠️ Цикл #{cycle.cycle_id}: низкая уверенность "
                            f"({cycle.confidence['act']:.0%})\n"
                            f"Причина: {act_reason}\n"
                            "Агент продолжает, накапливает опыт.",
                        )
                    except Exception as _e:
                        self._log_exc("telegram", _e)
            cycle.action_result = self._act(cycle.plan)
            # Верификация реальных результатов действий
            self._verify_action_results(cycle)
            # Синхронизация status после verify (контракты могут понизить execution.success)
            if isinstance(cycle.action_result, dict):
                _exec = cycle.action_result.get('execution', cycle.action_result)
                if isinstance(_exec, dict) and not _exec.get('success', True):
                    cycle.action_result['status'] = 'failed'
            # Confidence после действия: если ошибки — снижаем
            if cycle.errors:
                cycle.confidence['act'] = round(
                    cycle.confidence['act'] * (1.0 - 0.15 * min(len(cycle.errors), 4)), 2
                )

        # ── P1: Stop Conditions — детерминированные правила остановки ─────────
        self._apply_stop_conditions(cycle)

        # 5. EVALUATE
        cycle.phase = LoopPhase.EVALUATE
        cycle.evaluation = self._evaluate(cycle)
        # Confidence оценки наследует итоговую уверенность действия
        cycle.confidence['evaluate'] = cycle.confidence['act']

        # 6. LEARN — извлечь и сохранить урок
        cycle.phase = LoopPhase.LEARN
        cycle.learning = self._learn(cycle)

        # 7. REPLAY — переанализировать прошлый опыт
        cycle.phase = LoopPhase.REPLAY
        self._replay_experience(cycle)

        # 8. REPAIR — если были ошибки, попробовать починить
        cycle.phase = LoopPhase.REPAIR
        cycle.repair = self._repair(cycle)

        # 9. ACQUIRE — проактивное получение знаний
        self._acquire_knowledge(cycle)

        # Уведомляем в Telegram, если найдены новые вакансии Upwork
        self._notify_upwork_jobs()

        # Автономный поиск вакансий (каждые 10 циклов)
        if self._cycle_count % self.config.job_hunt_interval == 0 and self.job_hunter is not None:
            try:
                found = self.job_hunter.hunt()
                if found:
                    self._log(f"[job_hunter] Отправлено {found} подходящих вакансий в Telegram.")
            except Exception as _jh_err:
                self._log_exc("job_hunter", _jh_err)

        # Определяем успех цикла
        # Настоящий успех = реальное действие выполнено (не просто текст от LLM)
        real_work_done = self._has_real_work(cycle)
        execution_ok = self._execution_fully_successful(cycle)
        cycle.success = len(cycle.errors) == 0 and real_work_done and execution_ok

        # Логируем честную диагностику
        if cycle.action_result is not None and (not real_work_done or not execution_ok):
            details = []
            if not real_work_done:
                details.append(
                    "не было полезной работы (только мета/наблюдательные шаги)"
                )
            if not execution_ok:
                details.append("есть частичные или полные ошибки execution")
            self._log(
                "[success] Цикл не засчитан: " + "; ".join(details) + "."
            )

        projected_failures = 0 if cycle.success else self._consecutive_failures + 1

        # 10. IMPROVE — эволюция стратегий
        cycle.phase = LoopPhase.IMPROVE
        cycle.improvement = self._improve(
            cycle,
            success_rate=self._success_rate(current_success=cycle.success),
            consecutive_failures=projected_failures,
        )

        # Счётчик последовательных неудач
        if cycle.success:
            self._consecutive_failures = 0
            # Подцель выполнена — снимаем с очереди
            if self._subgoal_queue and self._has_goal_progress_signal(cycle):
                done_subgoal = self._subgoal_queue.pop(0)
                done_subgoal_id = (
                    self._subgoal_id_queue.pop(0)
                    if self._subgoal_id_queue else None
                )
                self._log(f"[plan] Подцель выполнена: {done_subgoal}")
                if done_subgoal_id and self.goal_manager:
                    try:
                        self.goal_manager.complete(done_subgoal_id)
                        if self._current_goal_id:
                            open_subgoals = self.goal_manager.get_open_subgoals(
                                self._current_goal_id
                            )
                            self._set_subgoal_queue(open_subgoals)
                    except Exception as _e:
                        self._log_exc("plan", _e)
            elif self._subgoal_queue:
                self._log(
                    "[plan] Подцель не закрыта: успешный цикл не дал сильного сигнала "
                    "изменения состояния."
                )
        else:
            self._consecutive_failures = projected_failures
            # Трекинг провалов текущей подцели → пере-декомпозиция
            if self._subgoal_queue:
                current_sg = self._subgoal_queue[0]
                self._subgoal_fail_counts[current_sg] = (
                    self._subgoal_fail_counts.get(current_sg, 0) + 1
                )
                if self._subgoal_fail_counts[current_sg] >= self._max_subgoal_failures:
                    self._log(
                        f"[plan] Подцель провалена "
                        f"{self._max_subgoal_failures} раз: "
                        f"'{current_sg[:60]}' — пере-декомпозиция."
                    )
                    self._redecompose_goal(current_sg)

        # Обновляем состояние "что было сделано" для следующего _observe()
        self._last_cycle_success = cycle.success
        self._last_cycle_errors  = list(cycle.errors)
        if cycle.action_result:
            exec_r = (
                self._extract_execution_result(cycle.action_result)
                if isinstance(cycle.action_result, dict) else None
            )
            if exec_r and exec_r.get('summary'):
                summary = self._preview(exec_r.get('summary'), 200)
            elif isinstance(cycle.action_result, dict):
                summary = self._preview(cycle.action_result.get('result', ''), 200)
            else:
                summary = self._preview(cycle.action_result, 200)
            self._last_cycle_summary = summary if cycle.success else ""
            if cycle.success and summary and summary not in self._completed_steps:
                self._completed_steps.append(summary)
                self._completed_steps = self._completed_steps[-20:]  # держим 20
        else:
            self._last_cycle_summary = ""

        # 11. EVALUATE LEARNING QUALITY — какие знания помогли
        self._evaluate_learning_quality(cycle)

        # 12. REACTIVE LEARNING — триггеры обучения по ситуации
        self._check_learning_triggers(cycle)

        # Сохраняем в persistent brain
        if self.persistent_brain:
            self.persistent_brain.record_cycle(useful=cycle.success)
            if cycle.learning and isinstance(cycle.learning, str):
                self.persistent_brain.record_lesson(
                    goal=str(self._goal)[:200],
                    success=cycle.success,
                    lesson=cycle.learning[:500],
                    context=str(cycle.evaluation)[:300] if cycle.evaluation else "",
                )
            elif cycle.evaluation:
                # Запасной путь: извлекаем осмысленный текст из evaluation
                _eval = cycle.evaluation
                _lesson_text = ''
                if isinstance(_eval, dict):
                    # Приоритет: analysis → lessons → suggestions → result
                    for _key in ('analysis', 'lessons', 'suggestions', 'result'):
                        _val = _eval.get(_key)
                        if _val and isinstance(_val, str) and len(_val) > 10:
                            _lesson_text = _val[:500]
                            break
                        elif _val and isinstance(_val, list):
                            _lesson_text = '; '.join(str(v)[:100] for v in _val[:5])
                            break
                elif isinstance(_eval, str) and len(_eval) > 10:
                    _lesson_text = _eval[:500]
                if _lesson_text and not _lesson_text.startswith('{'):
                    self.persistent_brain.record_lesson(
                        goal=str(self._goal)[:200],
                        success=cycle.success,
                        lesson=_lesson_text,
                        context=f"fallback_eval cycle#{cycle.cycle_id}",
                    )
            # Эволюция: записываем каждый N цикл или при ошибках
            if self._cycle_count % self.config.evolution_log_interval == 0 or cycle.errors:
                self.persistent_brain.record_evolution(
                    event="cycle_complete",
                    details=(
                        f"Цикл #{cycle.cycle_id}: "
                        f"{'успех' if cycle.success else 'неудача'}. "
                        f"Ошибок: {len(cycle.errors)}. "
                        f"Подряд неудач: {self._consecutive_failures}."
                        + (f" Ошибки: {'; '.join(cycle.errors[:2])}"
                           if cycle.errors else "")
                    ),
                )

        # Auto-checkpoint every 10 cycles via state_manager
        if self.state_manager and self._cycle_count % self.config.checkpoint_interval == 0:
            self.state_manager.auto_checkpoint(self)

        # Аудит целей каждые 50 циклов: сколько выполнено, сколько пропущено без попытки
        if self.goal_manager and self._cycle_count % self.config.goal_review_interval == 0:
            try:
                audit = self.goal_manager.audit_goals()
                if audit.get('skipped', 0) > 0:
                    self._log(
                        f"[learn/goal_audit] ⚠ Пропущено без попытки: {audit['skipped']} целей. "
                        f"Выполнено: {audit['completed']}, прогресс был у {audit['progressed']}."
                    )
            except Exception as _e:
                self._log_exc("learn/goal_audit", _e)

        # Ретроспектива + разбор опыта по слоям — каждые 20 циклов
        if self.reflection and self._cycle_count % self.config.retrospective_interval == 0 and self._cycle_count > 0:
            try:
                history_dicts = [c.to_dict() for c in self._history[-60:]]
                retro = self.reflection.generate_retrospective(history_dicts, window=60)
                self._log(
                    f"[retrospective] cycles={retro.get('total')}, "
                    f"success_rate={retro.get('success_rate', 0):.0%}, "
                    f"trend={retro.get('trend')}"
                )
                for rec in retro.get('recommendations', []):
                    self._log(f"[retrospective] → {rec}")
                    # Записываем рекомендацию как инсайт → она попадёт
                    # в build_context() → build_focused_prompt() → промпт LLM
                    try:
                        self.reflection.add_insight(f"[ретроспектива] {rec}")
                    except (AttributeError, TypeError):
                        pass
                # Сохраняем ретроспективу в память
                if self.persistent_brain:
                    self.persistent_brain.record_evolution(
                        event='retrospective',
                        details=str(retro)[:400],
                    )
                # Разбор опыта по слоям
                digest = self.reflection.layer_experience_digest()
                if digest.get('total_reflections', 0) > 0:
                    self._log(
                        f"[layer_digest] problematic={digest.get('most_problematic')}, "
                        f"reliable={digest.get('most_reliable')}, "
                        f"reflections={digest.get('total_reflections')}"
                    )
            except Exception as _e:
                self._log_exc("retrospective", _e)

        # ── Самотестирование по эталонным задачам каждые 50 циклов ─────────────
        # Агент прогоняет по 5 задач за раз (без OAuth), логирует провалы в память.
        _BENCH_EVERY = self.config.benchmark_interval
        if self._cycle_count % _BENCH_EVERY == 0 and self._cycle_count > 0:
            try:
                from skills.self_benchmark import SelfBenchmark
                _bench = SelfBenchmark(web_interface=None, timeout_sec=15.0)
                # Только задачи без OAuth (категории 1-4, 7 частично)
                _no_oauth_ids = [1, 2, 3, 4, 5, 6, 7, 34, 35, 36, 37, 39, 41, 45]
                # Берём срез 5 задач по кругу чтобы не нагружать цикл
                _offset = (self._cycle_count // _BENCH_EVERY - 1) % 3
                _slice  = _no_oauth_ids[_offset * 5 : _offset * 5 + 5]
                # Подключаем web_interface если он доступен
                if hasattr(self, '_web_interface') and self._web_interface:
                    setattr(_bench, '_wi', self._web_interface)
                _bench.run(task_ids=_slice)
                _report  = _bench.report()
                _analysis = _bench.analyze_errors()
                self._log(f"[self_benchmark] цикл {self._cycle_count}:\n{_report}")
                # Сохраняем провалы в постоянную память
                if self.persistent_brain:
                    _results = getattr(_bench, '_results', [])
                    _fails = [r for r in _results
                              if r.status.value in ('fail', 'timeout')]
                    if _fails:
                        _fail_summary = '; '.join(
                            f'#{r.task_id}({r.status.value}): {(r.error or r.reply)[:60]}'
                            for r in _fails
                        )
                        self.persistent_brain.record_evolution(
                            event='benchmark_failures',
                            details=f'Цикл {self._cycle_count}. Провалы: {_fail_summary}',
                        )
                    else:
                        self._log('[self_benchmark] ✅ Все задачи прошли успешно')
            except Exception as _bench_err:
                self._log_exc("self_benchmark", _bench_err)

        # Автопостинг в Telegram-канал при успешном цикле
        if cycle.success:
            self._channel_success_count += 1
            if (self.telegram_channel_id and self.telegram_bot
                    and self._channel_success_count % self._channel_post_every == 0):
                try:
                    self._post_achievement_to_channel(cycle)
                except Exception as _ch_e:
                    self._log_exc("channel", _ch_e)

        cycle.phase = LoopPhase.IDLE
        self._history.append(cycle)
        self._log(
            f"=== Цикл #{cycle.cycle_id} завершён "
            f"({'успех' if cycle.success else 'с ошибками'}) ==="
        )
        return cycle

    # ── Фазы цикла ────────────────────────────────────────────────────────────

    def _observe(self):
        """OBSERVE: получить данные из среды + состояние прошлого цикла."""
        try:
            raw = None
            if self.perception:
                raw = self.perception.get_data()
                self._log(f"[observe] Данные получены: {type(raw).__name__}")

            # Слой 44: Hardware — собираем метрики хоста раз в 5 циклов
            if self.hardware and self._cycle_count % self.config.hardware_check_interval == 0:
                try:
                    metrics = self.hardware.collect()
                    alerts = self.hardware.get_alerts()
                    if alerts:
                        self._log(
                            "[observe/hardware] Аппаратные предупреждения: "
                            + "; ".join(a.get('message', str(a)) for a in alerts[:3])
                        )
                    if isinstance(raw, dict):
                        raw['_hardware'] = metrics.to_dict()
                    # Тренд ресурсов: выводим когда CPU или память растут
                    if hasattr(self.hardware, 'resource_trend_summary'):
                        trend = self.hardware.resource_trend_summary(window=20)
                        cpu_t  = trend.get('cpu', {}).get('trend', 'stable')
                        mem_t  = trend.get('memory', {}).get('trend', 'stable')
                        breaches = trend.get('breach_events', 0)
                        if cpu_t == 'rising' or mem_t == 'rising' or breaches > 5:
                            self._log(
                                f"[observe/hardware] Тренд: CPU={cpu_t}, "
                                f"RAM={mem_t}, превышений пороговых={breaches}/20"
                            )
                except Exception as _e:
                    self._log_exc("observe/hardware", _e)

            # Слой 27: EnvironmentModel — обновляем модель среды
            if self.env_model:
                try:
                    upd: dict = {'cycle': self._cycle_count, 'goal': str(self._goal)}
                    if self._last_cycle_success is not None:
                        upd['last_success'] = self._last_cycle_success
                    self.env_model.update(upd)
                    # Регистрируем реальные сущности в первый цикл и периодически
                    if self._cycle_count == 1 or self._cycle_count % self.config.data_lifecycle_interval == 0:
                        # Инструменты
                        if self.tool_layer:
                            _tool_names = self.tool_layer.list()
                            self.env_model.register_entity('tools', {
                                'available': _tool_names,
                                'count': len(_tool_names),
                            })
                        # Навыки
                        if self.skill_library:
                            _all_skills = self.skill_library.list_all()
                            self.env_model.register_entity('skills', {
                                'count': len(_all_skills),
                                'names': [s.get('name', '') for s in _all_skills][:20],
                            })
                        # Кратковременная память
                        if self.knowledge_system:
                            _lt = self.knowledge_system.long_term_items()
                            _st = self.knowledge_system.get_short_term()
                            self.env_model.register_entity('memory', {
                                'long_term_items': len(_lt) if _lt else 0,
                                'short_term_items': len(_st) if _st else 0,
                            })
                        # Бюджет
                        if self.budget_control:
                            _budget_status = self.budget_control.summary()
                            self.env_model.register_entity('budget', {
                                'spent': _budget_status.get('spent', {}),
                            })
                        # Выходные файлы
                        _outdir = os.path.join(os.path.abspath('.'), 'outputs')
                        if os.path.isdir(_outdir):
                            _files = os.listdir(_outdir)
                            self.env_model.register_entity('outputs', {
                                'count': len(_files),
                                'files': _files[:20],
                            })
                        # Файлы в корне рабочей папки (то что положил пользователь)
                        _wd_root = os.path.dirname(os.path.abspath(__file__))
                        _wd_root = os.path.dirname(_wd_root)  # loop/ → agent/
                        _TASK_EXTS = {'.json', '.txt', '.csv', '.md'}
                        _SKIP_WD = {
                            'requirements.txt', 'log.txt', 'task_log.txt',
                            'process_list.txt', '.env', 'credentials.json',
                            'dynamic_registry.json', 'agent_state.json',
                        }
                        try:
                            _wd_files = [
                                e.name for e in os.scandir(_wd_root)
                                if e.is_file()
                                and os.path.splitext(e.name)[1].lower() in _TASK_EXTS
                                and e.name not in _SKIP_WD
                                and not e.name.startswith(('.', '__'))
                            ]
                            if _wd_files:
                                self.env_model.register_entity('workdir_files', {
                                    'count': len(_wd_files),
                                    'files': _wd_files,
                                })
                        except OSError as _e:
                            self._log_exc("observe/workdir_scan", _e)
                except Exception as _e:
                    self._log_exc("observe/env_model", _e)

            # Слой 40: TemporalReasoning — регистрируем начало цикла
            if self.temporal:
                try:
                    self.temporal.add_event(
                        description=f"Цикл #{self._cycle_count} начат. Цель: {str(self._goal)[:80]}",
                        tags=['cycle_start'],
                    )
                except Exception as _e:
                    self._log_exc("observe/temporal", _e)

            # Слой 43: SocialModel — определяем тон/стиль пользователя если есть входящее сообщение
            if self.social and isinstance(raw, dict) and raw.get('user_message'):
                try:
                    tone = self.social.detect_tone(str(raw['user_message']))
                    if tone not in ('neutral', None):
                        self._log(f"[observe/social] Тон пользователя: {tone}")
                        raw['_user_tone'] = tone
                except Exception as _e:
                    self._log_exc("observe/social", _e)

            # Добавляем контекст прошлого цикла — чтобы не начинать каждый цикл с нуля
            progress_ctx = {
                'prev_cycle_done': self._last_cycle_summary or "",
                'prev_cycle_success': self._last_cycle_success,
                'prev_cycle_errors': self._last_cycle_errors[:3],
                'completed_steps': self._completed_steps[-5:],   # последние 5 шагов
                'cycle_num': self._cycle_count,
                'consecutive_failures': self._consecutive_failures,
            }

            # ── OpMem + FTracker → обогащаем наблюдение ─────────────────
            _goal_str = str(self._goal or '')
            if _goal_str:
                _proc_steps = self.operational_memory.get_successful_steps(_goal_str)
                if _proc_steps:
                    progress_ctx['known_good_steps'] = _proc_steps[:5]
                _dom = self.failure_tracker.dominant_failure()
                if _dom:
                    progress_ctx['dominant_failure'] = _dom.value

            if isinstance(raw, dict):
                raw['_progress'] = progress_ctx
                result_obs = raw
            else:
                # если perception вернул не-dict или None
                result_obs = progress_ctx if raw is None else {'raw': raw, '_progress': progress_ctx}

            # Буфер наблюдений для DecisionRevisor.detect_env_change
            self._observation_history.append(result_obs)
            self._observation_history = self._observation_history[-10:]
            return result_obs

        except Exception as e:
            self._record_error("observe", e)
            return None

    def _analyze(self, observation):
        """ANALYZE: проанализировать наблюдение через Cognitive Core.

        Оптимизация:
          - Кэш по signature (goal+obs): если наблюдение не изменилось → reuse
          - Тривиальные наблюдения (пустые/None/короткие) → skip LLM
          - OperationalMemory: подставляет failure constraints + priority boost
          - FailureTracker: подставляет hint о доминирующих ошибках
        """
        try:
            if self.cognitive_core:
                # ── Trivial observation gate: не тратить LLM на пустоту ──────
                obs_str = str(observation or '').strip()
                if not obs_str or len(obs_str) < 10:
                    self._log("[analyze] Наблюдение тривиальное — LLM пропущен.")
                    return None

                # ── Unchanged observation gate: если obs не изменилось и ошибок нет ─
                obs_hash = hashlib.sha256(obs_str[:2000].encode()).hexdigest()[:16]
                if (
                    obs_hash == self._last_obs_hash
                    and not self._last_cycle_errors
                    and self._analysis_cache_value is not None
                ):
                    self._log("[analyze] Наблюдение не изменилось, ошибок нет — LLM пропущен.")
                    return self._analysis_cache_value
                self._last_obs_hash = obs_hash

                signature = f"goal={str(self._goal)}|obs={obs_str[:2000]}"
                if (
                    self._analysis_cache_value is not None
                    and signature == self._analysis_cache_signature
                    and self._analysis_cache_reuse < 2
                ):
                    self._analysis_cache_reuse += 1
                    self._log(
                        f"[analyze] Использую локальный кэш анализа "
                        f"({self._analysis_cache_reuse}/2)."
                    )
                    return self._analysis_cache_value

                self.cognitive_core.build_context(self._goal)

                # Добавляем контекст памяти если есть
                memory_ctx = ""
                if self.persistent_brain:
                    memory_ctx = self.persistent_brain.get_memory_context()

                # Слой 14: Multilingual — определяем язык цели, нормализуем если нужно
                goal_str = str(self._goal) if self._goal else ""
                if self.multilingual and goal_str:
                    try:
                        lang = self.multilingual.detect_language(goal_str)
                        if lang and lang not in ('ru', 'en', 'unknown'):
                            self._log(f"[analyze/multilingual] Язык цели: {lang}")
                    except Exception as _e:
                        self._log_exc("analyze/multilingual", _e)

                # Слой 39: Attention — фокусируем на ключевых элементах наблюдения
                obs_focus = None
                if self.attention and observation:
                    try:
                        # Подаём сигнал о текущей задаче, затем обновляем фокус
                        self.attention.signal(
                            goal_str[:120],
                            source='analyze',
                            importance=0.8,
                        )
                        items = self.attention.update_focus()
                        if items:
                            obs_focus = "; ".join(
                                str(getattr(i, 'content', i)) for i in items[:3]
                            )
                            self._log(f"[analyze/attention] Фокус: {obs_focus[:80]}")
                    except Exception as _e:
                        self._log_exc("analyze/attention", _e)

                # Цель обрезается до 200 символов — полный текст известен, повтор раздувает промпт
                goal_brief = str(self._goal)[:200]
                prompt = (
                    f"Проанализируй наблюдение: {observation}\n"
                    f"Цель: {goal_brief}"
                )
                if obs_focus:
                    prompt += f"\nКлючевые аспекты (attention): {obs_focus}"
                if memory_ctx:
                    prompt += f"\n\nИз памяти: {memory_ctx}"

                # OpMem + FTracker уже подставляются в _plan() — здесь НЕ дублируем
                # (экономия токенов: constraints и hints только в _plan, где они влияют
                # на выбор действий; в _analyze достаточно observation context из _observe)

                analysis = self.cognitive_core.reasoning(prompt)

                # Слой 41: CausalReasoning — фиксируем причинно-следственные связи ошибок
                if self.causal and self._last_cycle_errors:
                    try:
                        for err in self._last_cycle_errors[:2]:
                            self.causal.add_causal_relation(
                                cause=f"цикл #{self._cycle_count - 1}: {err[:80]}",
                                effect=f"необходим анализ: {goal_str[:60]}",
                            )
                    except Exception as _e:
                        self._log_exc("analyze/causal", _e)

                self._analysis_cache_signature = signature
                self._analysis_cache_value = analysis
                self._analysis_cache_reuse = 0
                self._log("[analyze] Анализ выполнен.")

                # ── IncompletenessDetector: проверяем достаточность данных ──
                try:
                    gaps = self._incompleteness.assess(
                        goal=str(self._goal),
                        observation=observation if isinstance(observation, dict) else None,
                        analysis=str(analysis) if analysis else None,
                    )
                    if gaps:
                        critical = [g for g in gaps if g.severity >= 0.65]
                        if critical:
                            hint = self._incompleteness.to_prompt_hint(gaps, str(self._goal))
                            self._log(
                                f"[analyze/incomplete] Пробелы в данных ({len(critical)} критичных): "
                                + "; ".join(g.field for g in critical[:3])
                            )
                            # Сохраняем hint в analysis для использования в _plan()
                            analysis = f"{analysis}\n\n{hint}" if analysis else hint
                        else:
                            self._log(
                                f"[analyze/incomplete] Некритичных пробелов: {len(gaps)} — продолжаем."
                            )
                except Exception as _ie:
                    self._log_exc("analyze/incomplete", _ie)

                return analysis
            self._log("[analyze] Cognitive Core не подключён, пропуск.")
            return None
        except Exception as e:
            if 'insufficient_quota' in str(e).lower():
                self._log(
                    "[quota] insufficient_quota — деньги на OpenAI-ключе кончились. "
                    "Пополни баланс и перезапусти агента.",
                    level='error',
                )
                self._running = False
                return None
            self._record_error("analyze", e)
            return None

    def _plan(self, analysis):
        try:
            if self.cognitive_core and self._goal:

                # ── GoalConflictResolver: проверяем конфликты активных целей ──
                _conflict_hint = ''
                if self.goal_manager and self._subgoal_queue:
                    try:
                        _active_goals = [
                            (sg, sg, 3)   # (id, desc, priority=MEDIUM)
                            for sg in self._subgoal_queue[:8]
                        ]
                        _conflicts = self._conflict_resolver.detect(_active_goals)
                        if _conflicts:
                            _conflict_hint = self._conflict_resolver.resolve_to_prompt_hint(_conflicts)
                            self._log(
                                f"[plan/conflict] Обнаружено конфликтов: {len(_conflicts)} — "
                                + "; ".join(c.conflict_type.value for c in _conflicts[:3])
                            )
                    except Exception as _ce:
                        self._log_exc("plan/conflict", _ce)

                # ── DecisionRevisor: проверяем необходимость пересмотра ──────
                _revision_hint = ''
                try:
                    _resources = None
                    if self.cognitive_core and hasattr(self.cognitive_core, 'llm'):
                        _llm = self.cognitive_core.llm
                        _resources = {
                            'tokens': getattr(_llm, 'total_tokens', 0),
                            'api_calls': getattr(_llm, '_call_count', 0),
                        }
                    _revision = self._decision_revisor.assess(
                        goal=str(self._goal),
                        current_plan=self._plan_cache_value,
                        analysis=str(analysis) if analysis else None,
                        consecutive_failures=self._consecutive_failures,
                        cycle_count=self._cycle_count,
                        last_observation=(
                            self._observation_history[-1]
                            if self._observation_history else None
                        ),
                        prev_observations=(
                            self._observation_history[:-1]
                            if len(self._observation_history) > 1 else None
                        ),
                        resources_spent=_resources,
                    )
                    if _revision.should_revise:
                        self._log(
                            f"[plan/revision] Пересмотр решения: "
                            f"{_revision.trigger.value if _revision.trigger else 'unknown'} "
                            f"(уверенность={_revision.confidence:.0%}) — {_revision.explanation}"
                        )
                        _revision_hint = _revision.revision_prompt
                        # Сбрасываем кэш плана — нужен новый
                        self._plan_cache_value = None
                        self._plan_cache_reuse = 0
                except Exception as _re:
                    self._log_exc("plan/revision", _re)

                # Сбрасываем кэш плана если предыдущий цикл завершился неудачей:
                # незачем повторять план который уже не сработал
                if self._consecutive_failures > 0 and self._plan_cache_value is not None:
                    self._log(
                        f"[plan] Неудача #{self._consecutive_failures} — кэш плана сброшен."
                    )
                    self._plan_cache_value = None
                    self._plan_cache_reuse = 0

                signature = f"goal={str(self._goal)}|analysis={str(analysis)[:2000]}"
                if (
                    self._plan_cache_value is not None
                    and signature == self._plan_cache_signature
                    and self._plan_cache_reuse < 2
                ):
                    self._plan_cache_reuse += 1
                    self._log(
                        f"[plan] Использую локальный кэш плана "
                        f"({self._plan_cache_reuse}/2)."
                    )
                    return self._plan_cache_value

                # ── Декомпозиция цели (один раз при первом планировании) ──
                if not self._goal_decomposed and self.goal_manager:
                    try:
                        goal_obj = self.goal_manager.add(str(self._goal))
                        self._current_goal_id = goal_obj.goal_id
                        self.goal_manager.activate(goal_obj.goal_id)  # фиксируем: агент взял цель в работу
                        sub_goals = self.goal_manager.get_open_subgoals(goal_obj.goal_id)
                        if sub_goals:
                            self._set_subgoal_queue(sub_goals)
                            self._log(
                                f"[plan] Переиспользую {len(sub_goals)} существующих подцелей: "
                                + ", ".join(g[:50] for g in self._subgoal_queue[:3])
                            )
                        else:
                            sub_goals = self.goal_manager.decompose(goal_obj.goal_id)
                        if sub_goals:
                            self._set_subgoal_queue(sub_goals)
                            self._log(
                                f"[plan] Цель декомпозирована на "
                                f"{len(sub_goals)} подцелей: "
                                + ", ".join(g[:50] for g in self._subgoal_queue[:3])
                            )
                    except Exception as _e:
                        self._log_exc("plan", _e)
                    finally:
                        self._goal_decomposed = True

                # Работаем над первой подцелью из очереди, если есть
                if self._subgoal_queue:
                    active_goal = self._subgoal_queue[0]
                elif self._goal_decomposed:
                    # ── Все подцели выполнены — НЕ повторяем ту же цель ──
                    # Пробуем взять следующую цель из GoalManager
                    _next = self._pick_next_goal()
                    if _next:
                        active_goal = _next
                    else:
                        # Нет ожидающих целей — idle, ждём внешнюю задачу
                        self._log("[plan] Все подцели выполнены, новых целей нет — idle.")
                        return None
                else:
                    active_goal = str(self._goal)

                if self._is_skill_training_mode():
                    training_skill = self._get_active_training_skill()
                    if training_skill:
                        active_goal = (
                            f"Тренировка навыка '{training_skill.name}'. "
                            f"{training_skill.description}. "
                            f"Изучи реальные свежие материалы по теме и затем "
                            f"закрепи стратегию навыка на практике.\n\n"
                            f"Стратегия навыка:\n{training_skill.strategy}"
                        )
                        self._log(
                            f"[plan/train] В работу взят навык '{training_skill.name}'."
                        )
                if self._subgoal_queue:
                    self._log(f"[plan] Текущая подцель: {str(active_goal)[:60]}")

                # ── Вставляем подсказки об конфликтах и пересмотре в active_goal ──
                if _conflict_hint:
                    active_goal = f"{active_goal}\n\n{_conflict_hint}"
                if _revision_hint:
                    # При пересмотре заменяем active_goal готовым промптом пересмотра
                    active_goal = _revision_hint

                # Кулдаун Upwork-мониторинга: если часто падает — исключаем шаг из плана
                _upwork_goal_kw = ('upwork', 'upwork_jobs', 'мониторинг upwork')
                _goal_has_upwork = any(kw in active_goal.lower() for kw in _upwork_goal_kw)
                if _goal_has_upwork and time.time() < self._upwork_skip_until:
                    _skip_until_str = __import__('datetime').datetime.fromtimestamp(
                        self._upwork_skip_until
                    ).strftime('%H:%M')
                    active_goal = (
                        active_goal
                        + f"\n\n[SYSTEM] UPWORK_MONITORING_SKIP: шаг мониторинга Upwork "
                        f"временно пропустить до {_skip_until_str} — слишком много неудач подряд. "
                        f"Переключись на другие шаги из плана."
                    )
                    self._log(
                        f"[upwork] Кулдаун активен до {_skip_until_str} — "
                        f"добавлен skip-hint в active_goal."
                    )

                # Слой 30: TaskDecomposition — тактическая декомпозиция активной подцели
                # (отличие от goal_manager.decompose: goal_manager строит дерево ЦЕЛЕЙ
                #  с отслеживанием статуса; task_decomp строит граф исполнения с
                #  зависимостями между шагами и ролями агентов — тактический уровень)
                #
                # Граф персистентен между циклами: агент проходит его шаг за шагом.
                # При успешном цикле — текущий узел помечается done, берётся следующий.
                # При смене цели или финише всех шагов — граф пересоздаётся.
                import re as _re_decomp
                _goal_has_code = bool(
                    _re_decomp.search(r'```(?:python|bash)', active_goal)
                    or _re_decomp.search(
                        r'^(?:SEARCH|READ|WRITE|BUILD_MODULE):', active_goal, _re_decomp.MULTILINE
                    )
                )
                # Слой 30: декомпозируем только когда задача реально сложная —
                # не атомарная и не уже содержащая код.
                # needs_decomposition() определяет это по структуре задачи.
                _needs_decomp = (
                    self.task_decomp
                    and not _goal_has_code
                    and self.task_decomp.needs_decomposition(active_goal)
                )
                if _needs_decomp:
                    try:
                        _task_decomp = self.task_decomp
                        if _task_decomp is None:
                            raise RuntimeError("task_decomp not configured")
                        # Граф пересоздаётся только при смене цели или его отсутствии
                        _graph_stale = (
                            self._task_graph is None
                            or self._task_graph_goal != active_goal[:120]
                        )
                        if _graph_stale:
                            self._task_graph = _task_decomp.decompose(
                                active_goal, max_subtasks=6
                            )
                        _graph = self._task_graph
                        if _graph is None:
                            raise RuntimeError("task graph not initialized")
                        if _graph_stale:
                            self._task_graph_goal = active_goal[:120]
                            self._task_graph_current_id = None
                            _total_steps = len(_graph.to_list())
                            self._log(
                                f"[plan/task_decomp] Задача разбита на "
                                f"{_total_steps} шагов"
                            )

                        # Когда предыдущий шаг завершился успешно — помечаем его done
                        if (
                            self._task_graph_current_id is not None
                            and self._last_cycle_success
                        ):
                            _graph.mark_done(self._task_graph_current_id)
                            self._task_graph_current_id = None
                            self._log("[plan/task_decomp] Шаг выполнен, перехожу к следующему.")

                        # Берём следующий готовый узел
                        _ready_tasks = _graph.get_ready()
                        if _ready_tasks:
                            _next_task = _ready_tasks[0]

                            # ── Рекурсивная декомпозиция подзадачи ───────────────
                            # Если подзадача сама по себе сложная (не атомарная) И
                            # ещё не была уже разобрана (depth < 2 защищает от зацикливания),
                            # разворачиваем её прямо в текущий граф через expand_node().
                            if (
                                _next_task.depth < 2
                                and not _re_decomp.search(r'```(?:python|bash)', _next_task.goal)
                                and _task_decomp.needs_decomposition(_next_task.goal)
                            ):
                                try:
                                    _sub_graph = _task_decomp.decompose(
                                        _next_task.goal, max_subtasks=4
                                    )
                                    _sub_nodes = getattr(_sub_graph, '_nodes', {})
                                    # Используем именно объекты из _sub_graph
                                    _sub_task_objs = list(_sub_nodes.values())
                                    _expanded = _graph.expand_node(
                                        _next_task.task_id, _sub_task_objs
                                    )
                                    if _expanded:
                                        self._log(
                                            f"[plan/task_decomp] Подзадача '{_next_task.goal[:50]}' "
                                            f"разбита ещё на {len(_sub_task_objs)} шагов"
                                        )
                                    else:
                                        _rej = getattr(_graph, '_rejection_reason', None)
                                        self._log(
                                            f"[plan/task_decomp] Расширение заблокировано: "
                                            f"{_rej or 'unknown'}"
                                        )
                                    # Берём заново из обновлённого графа
                                    _ready_tasks = _graph.get_ready()
                                    if _ready_tasks:
                                        _next_task = _ready_tasks[0]
                                    else:
                                        _next_task = None
                                except Exception as _sub_e:
                                    self._log(
                                        f"[plan/task_decomp] Рекурсивная декомпозиция не удалась: "
                                        f"{_sub_e}"
                                    )

                            if _next_task is not None:
                                self._task_graph_current_id = _next_task.task_id
                                _done_n = sum(
                                    1 for t in _graph.to_list()
                                    if t.get('status') in ('done', 'skipped')
                                )
                                _total_n = len(_graph.to_list())
                                active_goal = _next_task.goal
                                self._log(
                                    f"[plan/task_decomp] Шаг {_done_n + 1}/{_total_n}: "
                                    f"'{active_goal[:70]}'"
                                )
                        elif _graph.all_done():
                            # Все шаги пройдены — сбрасываем граф
                            self._task_graph = None
                            self._task_graph_goal = ''
                            self._task_graph_current_id = None
                            self._log("[plan/task_decomp] Все шаги выполнены.")
                    except Exception as _e:
                        self._log_exc("plan/task_decomp", _e)

                # Слой 32: ModelManager — рекомендуем модель для типа задачи
                if self.model_manager:
                    try:
                        _task_type = 'coding' if any(
                            kw in active_goal.lower()
                            for kw in ('код', 'python', 'скрипт', 'напиши', 'code', 'script')
                        ) else 'reasoning'
                        _profile = self.model_manager.select_for_task(_task_type)
                        if _profile:
                            self._log(
                                f"[plan/model_manager] Рекомендована модель: "
                                f"{_profile.model_id} (tier={_profile.tier.value})"
                            )
                    except Exception as _e:
                        self._log_exc("plan/model_manager", _e)

                # AuditJournal: консультируемся с историей провалов на похожих задачах
                try:
                    from evaluation.audit_journal import get_journal as _get_journal_plan
                    _mem_dir_plan = (
                        getattr(self.persistent_brain, 'data_dir', '.agent_memory')
                        if self.persistent_brain else '.agent_memory'
                    )
                    _journal_plan = _get_journal_plan(_mem_dir_plan)
                    _audit_hint = _journal_plan.get_failure_summary_for_prompt(active_goal)
                    if _audit_hint:
                        active_goal = _audit_hint + '\n\n' + active_goal
                        self._log(
                            '[plan] AuditJournal: найдены прошлые провалы — '
                            'история добавлена в контекст планировщика.'
                        )
                except Exception as _audit_plan_err:
                    self._log(
                        f'[plan] AuditJournal read warn: {_audit_plan_err}',
                        level='warning',
                    )

                # ── P1: Structured Skills — проверяем ДО LLM ─────────────────
                # 1. Exact/fuzzy match по structured skills (центральный контур)
                _structured_plan = None
                _structured_skill_name = None
                try:
                    _ss = self.structured_skills.find_exact(active_goal, threshold=0.5)
                    if _ss:
                        _pre_ok, _pre_reason = _ss.check_preconditions()
                        if _pre_ok:
                            _structured_plan = _ss.get_plan()
                            _structured_skill_name = _ss.name
                            self._log(
                                f"[plan] STRUCTURED_SKILL matched: '{_ss.name}' "
                                f"(score≥0.5, confidence={_ss.confidence:.2f}) → без LLM."
                            )
                        else:
                            self._log(
                                f"[plan] STRUCTURED_SKILL '{_ss.name}' не прошёл "
                                f"precondition: {_pre_reason}"
                            )
                except Exception as _ss_err:
                    self._log_exc("plan", _ss_err)

                if _structured_plan:
                    plan = _structured_plan
                    # Сохраняем имя навыка для record_use в _evaluate
                    self._active_structured_skill = _structured_skill_name
                else:
                    self._active_structured_skill = None

                    # ── P2: Operational Memory — ограничения и приоритеты ─────
                    _mem_constraints = self.operational_memory.get_failure_constraints(active_goal)
                    if _mem_constraints:
                        active_goal = _mem_constraints + '\n\n' + active_goal
                        self._log('[plan] OperationalMemory: failure constraints добавлены.')

                    _priority_steps = self.operational_memory.get_priority_boost(active_goal)
                    if _priority_steps:
                        _boost_text = (
                            '[PROCEDURAL MEMORY] Успешные шаги для похожих задач:\n'
                            + '\n'.join(f'  + {s[:100]}' for s in _priority_steps[:3])
                        )
                        active_goal = _boost_text + '\n\n' + active_goal
                        self._log('[plan] OperationalMemory: priority boost добавлен.')

                    # ── P1: Failure taxonomy hint ─────────────────────────────
                    _fail_hint = self.failure_tracker.to_prompt_hint(
                        str(self._goal), n=3
                    )
                    if _fail_hint:
                        active_goal = _fail_hint + '\n\n' + active_goal

                    # Слой 19: ReliabilitySystem — оборачивает LLM-план в retry
                    # Для фоновых автономных циклов временно подменяем LLM на дешёвый
                    _bg_llm = self.background_llm or getattr(self, '_background_llm', None)
                    _orig_llm = None
                    if _bg_llm and self.cognitive_core:
                        _orig_llm = getattr(self.cognitive_core, 'llm', None)
                        self.cognitive_core.llm = _bg_llm

                    try:
                        if self.reliability:
                            plan = self._local_skill_plan(active_goal) or self.reliability.retry(
                                self.cognitive_core.plan,
                                active_goal,
                                retries=2,
                                delay=1.0,
                                fallback=None,
                                circuit_name='cognitive_core_plan',
                            )
                        else:
                            plan = self._local_skill_plan(active_goal) or self.cognitive_core.plan(active_goal)
                    finally:
                        # Восстанавливаем основной LLM после планирования
                        if _orig_llm and self.cognitive_core:
                            self.cognitive_core.llm = _orig_llm

                # Слой 29: SkillLibrary — ищем готовую стратегию до LLM
                if plan is None and self.skill_library:
                    try:
                        skills = self.skill_library.find(active_goal, top_k=1)
                        if skills:
                            sk = skills[0]
                            self._log(
                                f"[plan/skill_library] Навык найден: '{sk.name}' "
                                f"(success_rate={sk.success_rate})"
                            )
                            plan = sk.strategy
                    except Exception as _e:
                        self._log_exc("plan/skill_library", _e)

                # Слой 38: LongHorizonPlanning — проверяем наличие роадмапа раз в 20 циклов
                if self.long_horizon and self._cycle_count % 20 == 1:
                    try:
                        lh_plan = self.long_horizon.plan(str(self._goal))
                        if lh_plan:
                            self._log(
                                f"[plan/long_horizon] Роадмап обновлён: "
                                f"{str(lh_plan)[:80]}"
                            )
                    except Exception as _e:
                        self._log_exc("plan/long_horizon", _e)

                # Если cognitive_core вернул служебное сообщение (петля, offline и т.п.)
                # — это не план для исполнения, логируем и пробуем сменить подцель
                if isinstance(plan, str) and plan.startswith('[LocalBrain]'):
                    self._log(f"[plan] {plan}")
                    # Петля на текущей подцели — переходим к следующей
                    if self._subgoal_queue:
                        dropped = self._subgoal_queue.pop(0)
                        dropped_id = (
                            self._subgoal_id_queue.pop(0)
                            if self._subgoal_id_queue else None
                        )
                        self._log(
                            f"[plan] Подцель сброшена из-за петли: '{dropped[:60]}'"
                            f"{f' (id={dropped_id})' if dropped_id else ''}"
                        )
                    else:
                        # Нет подцелей — сбрасываем историю петли чтобы дать шанс продолжить
                        if hasattr(self.cognitive_core, 'brain') and self.cognitive_core.brain is not None:
                            history = getattr(self.cognitive_core.brain, '_task_history', None)
                            if history is not None and hasattr(history, 'clear'):
                                history.clear()
                        self._log("[plan] История петли сброшена — следующий цикл будет свежим.")
                    return None

                plan = self._sanitize_plan(plan)

                # Валидация: план должен содержать хотя бы одно исполняемое действие
                if plan and isinstance(plan, str):
                    import re as _re_plan
                    _has_actions = bool(
                        _re_plan.search(r'```(?:bash|python)', plan)
                        or _re_plan.search(
                            r'^(?:SEARCH|READ|WRITE|BUILD_MODULE):', plan, _re_plan.MULTILINE
                        )
                    )
                    if not _has_actions:
                        # Проверяем: это отказ LLM или просто пустой план?
                        _refusal_markers = (
                            # English — OpenAI/Claude refusals
                            "i'm sorry", "i cannot", "i can't", "i am unable",
                            "as an ai", "not able to", "cannot fulfill",
                            "i apologize", "i'm not able", "i am not able",
                            "i won't", "i will not", "i'd rather not",
                            "that's not something i", "this is not something i",
                            "i don't feel comfortable", "i'm afraid i can't",
                            "unfortunately i cannot", "unfortunately, i cannot",
                            "i must decline", "i have to decline",
                            "beyond my capabilities", "outside my capabilities",
                            "i'm designed to", "as a language model",
                            "as an assistant", "ethical guidelines",
                            "against my guidelines", "my guidelines prevent",
                            "i lack the ability", "not within my ability",
                            "i'm not in a position", "i don't have the ability",
                            "not something i am able", "not something i can",
                            "that is not something", "this is not something",
                            # Russian — GPT/Claude refusals
                            "не могу выполнить", "не могу помочь",
                            "к сожалению, не могу", "к сожалению я не могу",
                            "я не могу", "я не в состоянии",
                            "не в моих силах", "это выходит за рамки",
                            "я вынужден отказать", "это противоречит",
                            "не могу этого сделать", "прошу прощения",
                            "мне жаль, но", "сожалею, но",
                        )
                        _is_refusal = any(m in plan.lower() for m in _refusal_markers)
                        if _is_refusal:
                            self._log(
                                f"[plan] LLM отказался от задачи — "
                                f"переформулирую как задачу программирования. "
                                f"Фрагмент: {plan[:80]!r}"
                            )
                            # Переформулируем: просим написать Python-код напрямую
                            _rephrased = (
                                f"Write Python code that accomplishes this task: {active_goal}\n"
                                f"Use only ```python blocks. Save results to files using open(). "
                                f"Do not explain, just write and execute the code."
                            )
                            try:
                                plan = self.cognitive_core.plan(_rephrased)
                                _has_actions_retry = bool(
                                    _re_plan.search(r'```(?:bash|python)', plan or '')
                                    or _re_plan.search(
                                        r'^(?:SEARCH|READ|WRITE|BUILD_MODULE):',
                                        plan or '', _re_plan.MULTILINE,
                                    )
                                )
                                if _has_actions_retry:
                                    self._log("[plan] Переформулировка помогла — план получен.")
                                else:
                                    self._log("[plan] Повторная попытка тоже без блоков — пропускаем.")
                                    self._plan_cache_value = None
                                    return None
                            except Exception as _reph_e:
                                self._log_exc("plan", _reph_e)
                                self._plan_cache_value = None
                                return None
                        else:
                            self._log(
                                f"[plan] Нет исполняемых блоков в плане — "
                                f"принудительный реплан с требованием кода. "
                                f"Фрагмент: {plan[:80]!r}"
                            )
                            # Реплан: явно требуем Python-блоки, не текст
                            _force_code_prompt = (
                                f"Task: {active_goal}\n\n"
                                f"Your previous response was plain text. "
                                f"You MUST respond with ONLY executable blocks.\n"
                                f"Either: ```python``` / ```bash``` as above, OR one line:\n"
                                f"BUILD_MODULE: module_name | what the module should do\n"
                                f"If the task is new code capability — prefer BUILD_MODULE.\n"
                                f"Otherwise use ```python``` that saves artifacts to outputs/ "
                                f"and prints confirmation.\n"
                                f"Do NOT write explanations."
                            )
                            try:
                                _forced_plan = self.cognitive_core.plan(_force_code_prompt) if self.cognitive_core else None
                                _has_actions_forced = bool(
                                    _forced_plan and (
                                        _re_plan.search(r'```(?:bash|python)', _forced_plan)
                                        or _re_plan.search(
                                            r'^(?:SEARCH|READ|WRITE|BUILD_MODULE):',
                                            _forced_plan, _re_plan.MULTILINE,
                                        )
                                    )
                                )
                                if _has_actions_forced:
                                    self._log("[plan] Принудительный реплан дал исполняемые блоки.")
                                    plan = _forced_plan
                                else:
                                    self._log("[plan] Принудительный реплан тоже вернул текст — пропускаем цикл.")
                                    self._plan_cache_value = None
                                    return None
                            except Exception as _force_e:
                                self._log_exc("plan", _force_e)
                                self._plan_cache_value = None
                                return None

                self._plan_cache_signature = signature
                self._plan_cache_value = plan
                self._plan_cache_reuse = 0
                self._log("[plan] План построен.")
                return plan
            self._log("[plan] Cognitive Core или цель не заданы, пропуск.")
            return None
        except Exception as e:
            if 'insufficient_quota' in str(e).lower():
                self._log(
                    "[quota] insufficient_quota — деньги на OpenAI-ключе кончились. "
                    "Пополни баланс и перезапусти агента.",
                    level='error',
                )
                self._running = False
                return None
            self._record_error("plan", e)
            return None

    # ── Санитайзер плана ──────────────────────────────────────────────────────

    def _sanitize_plan(self, plan) -> str | None:
        """Исправляет типичные ошибки форматирования LLM в плане перед dispatch.

        Исправления:
          1. SEARCH:/READ:/WRITE: с отступом внутри ```python``` → выносит наружу
          2. Незакрытые тройные кавычки → добавляет закрывающие ``` в конец
          3. Обрезанный f-string без {} → оборачивает в print()
        """
        if not isinstance(plan, str) or not plan.strip():
            return plan

        import re as _re
        result = plan
        fixes: list[str] = []

        # ── 1. DSL внутри python-блока (с отступом или без) ──────────────────
        _dsl_in_py = _re.compile(
            r'(```python\n)(.*?)(```)',
            _re.DOTALL,
        )
        _dsl_line = _re.compile(
            r'^[ \t]*(SEARCH|READ|WRITE):[ \t]*(.+)$',
            _re.MULTILINE,
        )
        extracted_dsl: list[str] = []

        def _pull_dsl(m: _re.Match) -> str:
            fence_open = m.group(1)
            code       = m.group(2)
            fence_close = m.group(3)
            found: list[str] = []
            def _collect(dm: _re.Match) -> str:
                found.append(f"{dm.group(1)}: {dm.group(2).strip()}")
                return ''
            clean = _dsl_line.sub(_collect, code).strip()
            extracted_dsl.extend(found)
            if clean:
                return f"{fence_open}{clean}\n{fence_close}"
            return ''  # пустой блок — удаляем

        result = _dsl_in_py.sub(_pull_dsl, result)
        if extracted_dsl:
            result = result.rstrip() + '\n' + '\n'.join(extracted_dsl)
            fixes.append(f"DSL-в-python: вынесено {len(extracted_dsl)} команд")

        # ── 2. Незакрытые тройные кавычки ─────────────────────────────────────
        # Считаем ``` — если нечётное, план обрезан: добавляем закрывающий
        if result.count('```') % 2 != 0:
            result = result.rstrip() + '\n```'
            fixes.append("незакрытые тройники: добавлено закрывающее ```")

        if fixes:
            self._log(f"[plan/sanitize] Исправлено: {'; '.join(fixes)}")
        return result

    # ── Локальные навыки (без LLM) ────────────────────────────────────────────
    #
    # Каждая запись: ([ключевые слова], шаблон-код | None)
    #   None  = явно исключить из перехвата (нужен LLM)
    #
    # Шаблоны покрывают задачи, которые LLM стабильно ломает:
    #   • float.percent  — cpu_percent/virtual_memory уже возвращают float
    #   • as_dict(attrs=) — psutil.win_service_iter враппер
    #   • import sys/subprocess — заблокированы sandbox
    #   • SEARCH: внутри python-блока — синтаксическая ошибка
    #   • незакрытые тройные кавычки
    #   • outputs/data.txt не существует
    #
    _LOCAL_SKILLS: list[tuple[list[str], str | None]] = [

        # ── 1. Проверка логов ─────────────────────────────────────────────────
        (
            ['проверь логи', 'проверить логи', 'проверь лог', 'ошибки в логе',
             'аномалии в логе', 'анализ лога', 'log errors', 'check logs'],
            """\
```python
import os
log_path = os.path.join(os.path.abspath('.'), 'log.txt')
errors = []
lines = []
try:
    with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
        lines = f.readlines()[-300:]
    for line in lines:
        if any(w in line for w in ('ERROR', 'FAIL', 'Exception', 'Traceback', 'STUB')):
            errors.append(line.strip())
except FileNotFoundError:
    errors = ['log.txt не найден']
print(f"Проверено строк: {len(lines)}. Проблемных: {len(errors)}")
for e in errors[:15]:
    print(e)
```"""
        ),

        # ── 2. Состояние RAM / CPU / Диск ─────────────────────────────────────
        # LLM часто делает: `cpu = psutil.cpu_percent(); cpu.percent` — ломается
        (
            ['проверь память', 'состояние памяти', 'оцени состояние', 'память агента',
             'использование памяти', 'ram usage', 'memory usage'],
            """\
```python
import psutil, os
vm = psutil.virtual_memory()
cpu_pct = psutil.cpu_percent(interval=0.3)   # уже float, не нужно .percent
disk = psutil.disk_usage(os.path.abspath('.'))
print(f"CPU: {cpu_pct}%")
print(f"RAM: {vm.used // 1024**2} MB / {vm.total // 1024**2} MB ({vm.percent}%)")
print(f"Диск: {disk.used // 1024**3} GB / {disk.total // 1024**3} GB ({disk.percent}%)")
```"""
        ),

        # ── 3. Системная информация (расширенная) ─────────────────────────────
        (
            ['системная информация', 'system info', 'состояние системы', 'статус системы',
             'system status', 'системный статус'],
            """\
```python
import psutil, os
vm = psutil.virtual_memory()
cpu_pct = psutil.cpu_percent(interval=0.3)
disk = psutil.disk_usage(os.path.abspath('.'))
proc_count = len(list(psutil.process_iter()))
print(f"CPU: {cpu_pct}%  |  RAM: {vm.percent}% ({vm.used//1024**2}/{vm.total//1024**2} MB)")
print(f"Disk: {disk.percent}% ({disk.used//1024**3}/{disk.total//1024**3} GB)")
print(f"Процессов: {proc_count}")
```"""
        ),

        # ── 4. Топ процессов по памяти / CPU ──────────────────────────────────
        # LLM ломается на process_iter без try/except (NoSuchProcess)
        (
            ['топ процессов', 'top processes', 'процессы по памяти', 'процессы по cpu',
             'список процессов', 'запущенные процессы', 'ps aux'],
            """\
```python
import psutil
procs = []
for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']):
    try:
        procs.append(p.info)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        continue
by_mem = sorted(procs, key=lambda x: x.get('memory_percent') or 0, reverse=True)[:5]
print(f"Топ-5 по памяти (всего процессов: {len(procs)}):")
for p in by_mem:
    print(f"  [{p.get('pid')}] {p.get('name','?'):25s} mem={p.get('memory_percent') or 0:.1f}%  cpu={p.get('cpu_percent') or 0:.1f}%")
```"""
        ),

        # ── 5. Службы Windows ─────────────────────────────────────────────────
        # LLM ломается на as_dict(attrs=[...]) — параметр называется fields= или без аргументов
        (
            ['службы', 'сервисы', 'windows services', 'список служб', 'win services',
             'запущенные службы', 'win_service'],
            """\
```python
import psutil
services = []
try:
    for svc in psutil.win_service_iter():
        try:
            d = svc.as_dict()   # без аргументов — безопасно
            services.append(d)
        except (OSError, Exception):
            continue
except AttributeError:
    print("Службы Windows недоступны (не Windows или нет прав)")
    services = []
running = [s for s in services if s.get('status') == 'running']
print(f"Служб всего: {len(services)}, запущено: {len(running)}")
for s in running[:10]:
    print(f"  {s.get('name','?'):30s} {s.get('status','?')}")
```"""
        ),

        # ── 6. Использование диска (детально) ────────────────────────────────
        (
            ['использование диска', 'disk usage', 'свободное место', 'диск',
             'disk space', 'место на диске'],
            """\
```python
import psutil, os
path = os.path.abspath('.')
disk = psutil.disk_usage(path)
total_gb = disk.total / 1024**3
used_gb  = disk.used  / 1024**3
free_gb  = disk.free  / 1024**3
print(f"Диск ({path}):")
print(f"  Всего:        {total_gb:.1f} GB")
print(f"  Используется: {used_gb:.1f} GB ({disk.percent}%)")
print(f"  Свободно:     {free_gb:.1f} GB")
```"""
        ),

        # ── 7. Состояние памяти агента (brain / knowledge.json) ───────────────
        # LLM иногда пытается import check_system (не существует) или читает неправильный путь
        (
            ['состояние мозга', 'состояние агента', 'brain status', 'knowledge status',
             'что я знаю', 'объём знаний', 'сколько знаний', 'мои знания'],
            """\
```python
import os, json
brain_dir = os.path.join(os.path.abspath('.'), '.agent_memory')
kfile = os.path.join(brain_dir, 'knowledge.json')
try:
    with open(kfile, 'r', encoding='utf-8') as f:
        brain = json.load(f)
    lt  = brain.get('long_term', {})
    ep  = brain.get('episodic', [])
    sem = brain.get('semantic', {})
    print(f"Состояние памяти агента:")
    print(f"  Долговременная (long_term): {len(lt)} записей")
    print(f"  Эпизодическая  (episodic):  {len(ep)} эпизодов")
    print(f"  Семантическая  (semantic):  {len(sem)} концептов")
    print(f"  Итого: {len(lt) + len(ep) + len(sem)} единиц знания")
except FileNotFoundError:
    print("Файл памяти не найден — агент на холодном старте")
except Exception as e:
    print(f"Ошибка при чтении памяти: {e}")
```"""
        ),

        # ── 8. Содержимое папки outputs/ ──────────────────────────────────────
        # LLM иногда читает outputs/data.txt который не существует
        (
            ['папка outputs', 'содержимое outputs', 'файлы outputs', 'outputs файлы',
             'список файлов', 'что в outputs', 'артефакты'],
            """\
```python
import os
outputs_dir = os.path.join(os.path.abspath('.'), 'outputs')
os.makedirs(outputs_dir, exist_ok=True)
files = []
for name in os.listdir(outputs_dir):
    fp = os.path.join(outputs_dir, name)
    if os.path.isfile(fp):
        files.append((name, os.path.getsize(fp)))
files.sort(key=lambda x: x[1], reverse=True)
print(f"outputs/ — файлов: {len(files)}")
for name, size in files[:20]:
    print(f"  {name}: {size} байт")
```"""
        ),

        # ── 9. Сетевые соединения (без import socket — заблокирован) ──────────
        # LLM часто пытается `import socket` (blocked) вместо psutil.net_connections
        (
            ['сетевые соединения', 'network connections', 'открытые порты', 'сеть агента',
             'net connections', 'сетевая активность'],
            """\
```python
import psutil
conns = []
try:
    conns = psutil.net_connections(kind='inet')
except (psutil.AccessDenied, PermissionError):
    print("Нет прав для просмотра соединений")
    conns = []
established = [c for c in conns if c.status == 'ESTABLISHED']
print(f"Соединений всего: {len(conns)}, активных (ESTABLISHED): {len(established)}")
for c in established[:10]:
    laddr = f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "?"
    raddr = f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else "?"
    print(f"  {laddr} → {raddr}")
```"""
        ),

        # ── 10. Проверить архитектуру / пробелы ────────────────────────────────
        # Агент часто пытается выполнить это через LLM и получает псевдокод
        (
            ['проверь архитектуру', 'оцени архитектуру', 'пробелы в архитектуре',
             'что не работает', 'что умею', 'самооценка', 'self assessment'],
            """\
```python
import os, json
base = os.path.abspath('.')
checks = {
    'knowledge.json':    os.path.join(base, '.agent_memory', 'knowledge.json'),
    'log.txt':           os.path.join(base, 'log.txt'),
    'agent.py':          os.path.join(base, 'agent.py'),
    'outputs/':          os.path.join(base, 'outputs'),
}
print("Проверка наличия ключевых файлов:")
for label, path in checks.items():
    exists = os.path.exists(path)
    size = os.path.getsize(path) if exists and os.path.isfile(path) else None
    status = f"OK ({size} байт)" if size is not None else ("OK (папка)" if exists else "ОТСУТСТВУЕТ")
    print(f"  {label:25s} {status}")

modules = ['core', 'loop', 'execution', 'knowledge', 'learning',
           'self_repair', 'tools', 'communication', 'environment']
print("Модули агента:")
for m in modules:
    path = os.path.join(base, m)
    ok = os.path.isdir(path)
    print(f"  {m:20s} {'OK' if ok else 'НЕТ ПАПКИ'}")
```"""
        ),

        # ── 11. Заголовки разделов (не исполняемые задачи) ───────────────────
        # Перехватываем до LLM: "Работа с Gmail" — не задача, а раздел
        (
            ['работа с gmail', 'работа с google calendar', 'работа с файлами пользователя',
             'контакты и персональные данные', 'работа с контактами', 'работа с pdf',
             'поиск и сбор информации', 'работа с web', 'поиск по источникам',
             'gmail задачи', 'calendar задачи', 'pdf задачи'],
            """\
```python
print("NON_ACTIONABLE: это заголовок раздела, а не исполняемая задача. Пропускаем.")
result = {'success': True, 'status': 'non_actionable', 'skipped': True}
```"""
        ),

        # ── 12. Текущее время в городе (быстро, без LLM и таймаута) ──────────
        (
            ['время в тель', 'время в нью', 'время в москв', 'время в лондон',
             'время в берлин', 'время в париж', 'time in tel', 'time in new york',
             'time in moscow', 'time in london', 'который час', 'текущее время',
             'сколько времени в'],
            """\
```python
import datetime, zoneinfo
cities = [
    ('Тель-Авив', 'Asia/Jerusalem'),
    ('Нью-Йорк', 'America/New_York'),
    ('Москва', 'Europe/Moscow'),
    ('Лондон', 'Europe/London'),
    ('Берлин', 'Europe/Berlin'),
    ('Париж', 'Europe/Paris'),
]
print("Текущее время в городах:")
for name, tz_name in cities:
    tz = zoneinfo.ZoneInfo(tz_name)
    now = datetime.datetime.now(tz)
    print(f"  {name:12s} {now.strftime('%H:%M %Z')}")
```"""
        ),

        # ── 13. Gmail / email задачи → явный BLOCKED без web-search ──────────
        # Предотвращает замену Gmail-задачи на web-поиск по тексту задачи
        (
            ['найди непрочитанные письма', 'непрочитанных письм', 'письма от',
             'создай черновик письма', 'найди письма с вложением', 'найди письма по ярлыку',
             'архивируй письм', 'ответь на письм', 'найди письма за', 'составь черновик'],
            """\
```python
import os
has_creds = bool(os.environ.get('EMAIL_USERNAME') or os.environ.get('GMAIL_USER'))
if has_creds:
    print("EMAIL: credentials настроены. Используйте email tool для работы с почтой.")
else:
    print("BLOCKED: для работы с Gmail нужен EMAIL_USERNAME и EMAIL_PASSWORD в .env или credentials.json")
    print("Задача не может быть выполнена через web-поиск.")
```"""
        ),

        # ── 14. Google Calendar → явный BLOCKED/выполнение ───────────────────
        (
            ['свободные окна в календаре', 'создай событие в', 'перенеси встречу',
             'ответь на приглашение', 'ближайшие встречи', 'найди встречу по',
             'занятость на', 'расписание встреч'],
            """\
```python
import os
has_creds = bool(os.environ.get('GOOGLE_CALENDAR_CREDENTIALS') or
                 os.path.exists('config/credentials.json'))
if has_creds:
    print("CALENDAR: credentials настроены. Используйте calendar tool для работы.")
else:
    print("BLOCKED: для работы с Google Calendar нужен credentials.json с OAuth токеном")
    print("Задача не может быть выполнена через web-поиск.")
```"""
        ),

        # ── Явные исключения (фразы для которых нужен LLM) ───────────────────
        (
            ['изучи', 'изучить', 'узнай о', 'исследуй', 'найди информацию о',
             'расскажи о', 'объясни', 'что такое'],
            None,   # LLM нужен для выбора темы поиска
        ),
    ]

    def _local_skill_plan(self, goal: str) -> str | None:
        """Возвращает готовый план без LLM, если задача из известного шаблона.

        Порядок проверки:
          1. Dynamic skills — навыки, выученные агентом во время работы (персистент).
          2. Static LOCAL_SKILLS — встроенные шаблоны (определены в коде).

        Возвращает None если шаблон не подходит → цикл идёт к LLM как обычно.
        """
        goal_l = goal.lower()
        # 1. Dynamic skills (выучены из ошибок — приоритет)
        for keywords, template in self._dynamic_skills:
            if any(kw in goal_l for kw in keywords):
                matched = next(kw for kw in keywords if kw in goal_l)
                # SECURITY: AST-валидация dynamic skill перед исполнением
                _code_block = re.search(r'```python\n(.+?)```', template, re.DOTALL)
                if _code_block:
                    try:
                        from safety.hardening import ContentSanitizer
                        _ok, _reason = ContentSanitizer.validate_python(_code_block.group(1))
                        if not _ok:
                            self._log(
                                f"[plan] DYNAMIC_SKILL '{matched}' заблокирован: {_reason}"
                            )
                            continue
                    except ImportError:
                        self._log(
                            f"[plan] DYNAMIC_SKILL '{matched}' заблокирован: "
                            f"ContentSanitizer недоступен"
                        )
                        continue
                self._log(
                    f"[plan] DYNAMIC_SKILL: ключевое слово '{matched}' → "
                    f"выученный шаблон (без LLM)."
                )
                return template
        # 2. Static built-in skills
        for keywords, template in self._LOCAL_SKILLS:
            if template is None:
                continue  # явно исключённые — не перехватываем
            if any(kw in goal_l for kw in keywords):
                matched = next(kw for kw in keywords if kw in goal_l)
                self._log(
                    f"[plan] LOCAL_SKILL: ключевое слово '{matched}' → "
                    f"встроенный шаблон (без LLM)."
                )
                return template
        return None

    def _simulate(self, plan):
        """SIMULATE: проверка плана в песочнице перед реальным выполнением.
        Возвращает 'BLOCKED' если план небезопасен, иначе вердикт ('safe'/'risky'/None)."""
        if not plan:
            return None
        if not self.sandbox:
            self._log("[simulate] Sandbox не подключён — пропуск симуляции.")
            return None
        try:
            from environment.sandbox import SandboxResult
            plan_str = str(plan)[:500]
            run = self.sandbox.simulate_action(plan_str)

            self._log(f"[simulate] Вердикт: {run.verdict.value}"
                      + (f" | Побочные эффекты: {run.side_effects}" if run.side_effects else ""))

            # Только UNSAFE блокирует выполнение — RISKY выполняется с предупреждением
            if run.verdict == SandboxResult.UNSAFE:
                # Сохраняем в память
                if self.persistent_brain:
                    self.persistent_brain.record_evolution(
                        event="sandbox_blocked",
                        details=f"Цикл #{self._cycle_count}: план заблокирован. "
                                f"Причина: {run.error or 'UNSAFE verdict'}",
                    )
                return 'BLOCKED'

            if run.verdict == SandboxResult.RISKY and run.side_effects:
                self._log(f"[simulate] RISKY — выполняю с осторожностью: "
                          f"{', '.join(run.side_effects[:3])}")

            # Слой 41: CausalReasoning — проверяем известные паттерны провала
            if self.causal:
                try:
                    prediction = self.causal.predict(plan_str)
                    bad = [
                        e for e in prediction.get('direct_effects', [])
                        if any(kw in e['effect'].lower()
                               for kw in ('ошибк', 'провал', 'fail', 'error', 'crash'))
                        and e.get('confidence', 0) >= 0.6
                    ]
                    if bad:
                        top = bad[0]
                        self._log(
                            f"[simulate/causal] Известный паттерн провала: "
                            f"'{top['effect']}' (уверенность={top['confidence']:.0%})"
                        )
                except Exception as _e:
                    self._log_exc("simulate/causal", _e)

            return run.verdict.value
        except Exception as e:
            self._record_error("simulate", e)
            return None  # При ошибке симуляции — не блокируем, продолжаем

    def _act(self, plan):
        """
        ACT: исполнить план.

        Шаг 1 (LLM): Agent System генерирует развёрнутый план действий.
        Шаг 2 (реальное исполнение): ActionDispatcher извлекает исполняемые блоки
            из текста плана (```bash, ```python, SEARCH:, READ:, WRITE:) и запускает
            их через ToolLayer / ExecutionSystem.
        """
        try:
            if not plan:
                self._log("[act] Нет плана для исполнения.")
                return None

            plan_str_check = str(plan)

            # Слой 21: Governance — проверяем политику перед действием
            if self.governance:
                try:
                    gov = self.governance.check(plan_str_check[:500],
                                                context={'goal': str(self._goal)})
                    if gov.get('allowed') is False:
                        reason = gov.get('reason', 'политика запрещает')
                        self._log(f"[act/governance] ЗАБЛОКИРОВАНО: {reason}")
                        if self._current_cycle:
                            self._current_cycle.errors.append(
                                f'GOVERNANCE_BLOCKED: {reason}'
                            )
                        return None
                except Exception as _e:
                    self._log_exc("act/governance", _e)

            # Слой 42: Ethics — этическая проверка
            if self.ethics:
                try:
                    eth = self.ethics.evaluate(plan_str_check[:2000],
                                               context={'goal': str(self._goal)})
                    if not self.ethics.is_allowed(plan_str_check[:2000]):
                        self._log(
                            f"[act/ethics] ЗАБЛОКИРОВАНО (score={getattr(eth, 'score', '?')}): "
                            f"{getattr(eth, 'reasoning', '')[:80]}"
                        )
                        if self._current_cycle:
                            self._current_cycle.errors.append(
                                f'ETHICS_BLOCKED: score={getattr(eth, "score", "?")}, '
                                f'{getattr(eth, "reasoning", "")[:80]}'
                            )
                        return None
                except Exception as _e:
                    self._log_exc("act/ethics", _e)

            # Слой 16: Security — аудит действия (контент-фильтр покрыт Governance выше)
            if self.security:
                try:
                    self.security.audit(
                        action='act_plan',
                        resource=str(self._goal)[:80],
                        success=True,
                    )
                except Exception as _e:
                    self._log_exc("act/security", _e)

            # ── Pre-exec: OpMem проверяет блокированные шаги ────────────────
            _blocked = self.operational_memory.get_blocked_steps(
                str(self._goal or ''), threshold=3
            )
            if _blocked:
                plan_lower = str(plan).lower()
                for b in _blocked:
                    blocked_step = str(b['step']).lower()
                    if blocked_step and blocked_step[:30] in plan_lower:
                        self._log(
                            f"[act] OpMem БЛОК: шаг '{b['step'][:60]}' "
                            f"проваливался {b['count']}x ({b['category']})"
                            + (f" → вместо: {b['recovery'][:80]}" if b.get('recovery') else ''),
                            level='warning',
                        )

            # ── Шаг 1: LLM-агент уточняет план (опционально) ──────────────────
            llm_result = None
            if self.agent_system:
                llm_result = self.agent_system.handle({
                    'goal': str(self._goal),
                    'plan': plan,
                    'role': 'planning',
                })
                self._log(f"[act] Agent System: {llm_result.get('status')}")

            # ── Шаг 2: извлекаем и исполняем реальные действия ────────────────
            if self.action_dispatcher:
                # Сканируем сам план + текст ответа LLM (уточнений)
                plan_text   = str(plan or '')
                refine_text = str(llm_result.get('result', '') if llm_result else '')
                # Если уточнённый план пустой или не содержит команд — используем оригинал
                def _has_exec(t: str) -> bool:
                    return any(
                        m in t for m in (
                            'SEARCH:', 'READ:', 'WRITE:', 'BUILD_MODULE:',
                            '```bash', '```python',
                        )
                    )
                text_to_scan = (
                    (refine_text + '\n' + plan_text) if _has_exec(refine_text)
                    else plan_text
                )
                exec_result = self.action_dispatcher.dispatch(
                    text_to_scan,
                    goal=str(self._goal or ''),
                    fail_closed_on_semantic_reject=True,
                )

                # FAIL-CLOSED: semantic gate отклонил действия — делаем мгновенный реплан
                # в текущем цикле и пробуем выполнить один раз заново.
                if exec_result.get('semantic_rejected'):
                    _can_replan = (
                        (self._cycle_count - self._last_fail_closed_replan_cycle)
                        >= self._fail_closed_replan_min_gap_cycles
                    )
                    if _can_replan:
                        self._log(
                            "[act/fail_closed] План отклонён semantic gate — запускаю реплан.",
                            level='warning',
                        )
                        self._last_fail_closed_replan_cycle = self._cycle_count
                        if self._current_cycle:
                            self._current_cycle.errors.append(
                                "act: semantic gate rejected plan, replanning"
                            )

                        _rej = [
                            str(r.get('error', ''))
                            for r in exec_result.get('results', [])
                            if isinstance(r, dict) and str(r.get('error', '')).startswith('SEMANTIC_GATE_REJECTED')
                        ][:3]
                        _rej_text = '\n'.join(f"- {x}" for x in _rej) if _rej else '- unknown'

                        replan_goal = (
                            f"Цель: {self._goal}\n\n"
                            "Предыдущий план отклонён semantic gate.\n"
                            "Сформируй НОВЫЙ релевантный план без нерелевантных действий.\n"
                            "Учитывай причины отклонения:\n"
                            f"{_rej_text}"
                        )

                        replanned = None
                        try:
                            if self.cognitive_core:
                                replanned = self.cognitive_core.plan(replan_goal)
                        except Exception as _re:
                            self._log_exc("act/fail_closed", _re)

                        if replanned:
                            exec_result = self.action_dispatcher.dispatch(
                                str(replanned),
                                goal=str(self._goal or ''),
                                fail_closed_on_semantic_reject=True,
                            )
                            exec_result['replanned_once'] = True
                            if exec_result.get('semantic_rejected'):
                                self._log(
                                    "[act/fail_closed] Реплан снова отклонён semantic gate.",
                                    level='error',
                                )
                            elif exec_result.get('success') and self._current_cycle:
                                try:
                                    self._current_cycle.errors.remove(
                                        "act: semantic gate rejected plan, replanning"
                                    )
                                except ValueError:
                                    pass
                    else:
                        _left = self._fail_closed_replan_min_gap_cycles - (
                            self._cycle_count - self._last_fail_closed_replan_cycle
                        )
                        self._log(
                            f"[act/fail_closed] Реплан пропущен (cooldown): ещё {_left} циклов.",
                            level='warning',
                        )
                        exec_result['replan_skipped_cooldown'] = True

                if llm_result:
                    llm_result['execution'] = exec_result
                    if exec_result['actions_found'] == 0:
                        llm_result['status'] = 'failed'

                # Слой 24: DataValidator — проверяем структуру результата dispatch
                if self.validation and isinstance(exec_result, dict):
                    try:
                        vr = self.validation.validate(
                            exec_result,
                            schema={
                                'actions_found': {'type': int, 'required': True},
                                'success':       {'type': bool, 'required': True},
                            },
                        )
                        if not vr.is_valid:
                            self._log(f"[act/validation] Некорректный результат: {vr}")
                    except Exception as _e:
                        self._log_exc("act/validation", _e)

                if exec_result['actions_found'] > 0:
                    n_ok  = sum(1 for r in exec_result['results'] if isinstance(r, dict) and r.get('success'))
                    n_all = exec_result['actions_found']
                    if n_ok == n_all:
                        _act_verdict = "Все успешны."
                    elif exec_result['success']:
                        _act_verdict = f"Частичный успех ({n_ok} ок, {n_all - n_ok} ошибок)."
                    else:
                        _act_verdict = "Есть ошибки — см. execution."
                    self._log(f"[act] Исполнено {n_ok}/{n_all} действий. {_act_verdict}")
                    # ── Слой 45: Identity — записываем статистику реальных действий ──
                    if self.identity:
                        _type_success: dict[str, list[bool]] = {}
                        for _r in exec_result.get('results', []):
                            if not isinstance(_r, dict):
                                continue
                            _t = str(_r.get('type', 'unknown')).casefold()
                            _type_success.setdefault(_t, []).append(bool(_r.get('success')))
                        for _t, _outcomes in _type_success.items():
                            for _ok in _outcomes:
                                try:
                                    self.identity.record_action_stats(_t, _ok)
                                except Exception as _e:
                                    self._log_exc("identity", _e)
                    # ── Слой 35: CapabilityDiscovery — grounding для find_gaps() ──
                    if self.capability_discovery and hasattr(self.capability_discovery, 'record_action_result'):
                        for _r in exec_result.get('results', []):
                            if not isinstance(_r, dict):
                                continue
                            _t = str(_r.get('type', 'unknown')).casefold()
                            try:
                                self.capability_discovery.record_action_result(_t, bool(_r.get('success')))
                            except Exception as _e:
                                self._log_exc("capability", _e)
                    # Пробрасываем ошибки действий в cycle.errors → self-repair их увидит
                    if not exec_result['success'] and self._current_cycle:
                        for _r in exec_result.get('results', []):
                            if not isinstance(_r, dict):
                                continue
                            if not _r.get('success') and _r.get('error'):
                                self._current_cycle.errors.append(
                                    f"act:{_r.get('type','?')}: {_r['error']}"
                                )

                    # Отслеживаем неудачи Upwork-мониторинга для автокулдауна
                    _upwork_kw = ('upwork', 'upwork_jobs', 'upwork jobs')
                    _upwork_fail = any(
                        any(kw in str(_r.get('input', '')).lower() or kw in str(_r.get('error', '')).lower()
                            for kw in _upwork_kw)
                        for _r in exec_result.get('results', [])
                        if isinstance(_r, dict) and not _r.get('success')
                    )
                    if _upwork_fail:
                        self._upwork_fail_count += 1
                        if self._upwork_fail_count >= self._UPWORK_FAIL_THRESHOLD:
                            self._upwork_skip_until = time.time() + self._UPWORK_COOLDOWN_SEC
                            self._upwork_fail_count = 0
                            self._log(
                                f"[upwork] {self._UPWORK_FAIL_THRESHOLD} неудач подряд — "
                                f"кулдаун {self._UPWORK_COOLDOWN_SEC//3600}ч."
                            )
                    elif exec_result['success']:
                        self._upwork_fail_count = 0
                    # Сохраняем сводку в память (для следующего цикла)
                    if self.knowledge_system and exec_result.get('summary'):
                        self.knowledge_system.add_short_term({
                            'type':    'execution_result',
                            'summary': exec_result['summary'],
                            'cycle':   self._cycle_count,
                        })

                    if llm_result:
                        if not exec_result['success']:
                            llm_result['status'] = 'failed'
                        return llm_result
                    return exec_result

                self._log(
                    "[act] ActionDispatcher не нашёл исполняемых действий в плане.",
                    level='warning',
                )
                self._log(
                    "[efficiency] Расход LLM на план/ACT без dispatch: ответ не содержит "
                    "SEARCH:/READ:/WRITE:/BUILD_MODULE:, ни ```bash/python``` — "
                    "следующий цикл получит жёсткий реплан из _plan.",
                    level='warning',
                )

                # Fallback: TaskExecutor — прямое исполнение цели без LLM
                if self.task_executor and self._goal:
                    try:
                        tex_result = self.task_executor.execute(str(self._goal))
                        self._log(
                            f"[act/task_executor] type={tex_result.get('task_type')} "
                            f"success={tex_result.get('success')} "
                            f"file={tex_result.get('file', '')}"
                        )
                        if llm_result:
                            llm_result['execution'] = tex_result
                            if not tex_result.get('success'):
                                llm_result['status'] = 'failed'
                            return llm_result
                        return tex_result
                    except Exception as _te:
                        self._log_exc("act/task_executor", _te)

            # Слой 18: OrchestrationSystem — если очередь задач не пуста, берём следующую
            if self.orchestration:
                try:
                    ot = self.orchestration.run_next()
                    if ot:
                        status_value = getattr(ot.status, 'value', str(ot.status))
                        self._log(
                            f"[act/orchestration] Выполнена задача из очереди: "
                            f"'{ot.goal[:60]}' → {status_value}"
                        )
                except Exception as _e:
                    self._log_exc("act/orchestration", _e)

            # Нет dispatcher или нет блоков — возвращаем LLM-результат как раньше
            if llm_result:
                return llm_result

            self._log("[act] Agent System не подключён и ActionDispatcher отсутствует.")
            return None

        except Exception as e:
            self._record_error("act", e)
            return None

    def _evaluate(self, cycle: LoopCycle):
        """EVALUATE: оценить результат через Reflection System + StepEvaluator."""
        try:
            strong_cycle = self._should_store_cycle_experience(cycle)

            # ── StepEvaluator: честная диагностика шага ──────────────────────
            try:
                from evaluation.step_evaluator import evaluate_step as _eval_step
                exec_r = (
                    self._extract_execution_result(cycle.action_result)
                    if isinstance(cycle.action_result, dict) else None
                )
                step_v = _eval_step(
                    goal=str(self._goal or ''),
                    result=cycle.action_result,
                    execution_result=exec_r,
                )
                # Логируем verdict
                verdict_symbol = '✅' if step_v.passed else '❌'
                self._log(
                    f"[evaluate] {verdict_symbol} StepVerdict={step_v.verdict} "
                    f"score={step_v.score:.2f} intent={step_v.intent}"
                )
                if step_v.issues:
                    for issue in step_v.issues:
                        self._log(f"[evaluate]   ⚠ {issue}", level='warning')
                # Если evaluator говорит WRONG_TOOL/IRRELEVANT/SUBSTITUTION →
                # помечаем ошибку в cycle.errors, чтобы cycle.success стал False
                if not step_v.passed and step_v.verdict not in ('NON_ACTIONABLE', 'BLOCKED'):
                    if step_v.verdict not in cycle.errors:
                        cycle.errors.append(f'STEP_EVAL: {step_v.verdict}')

                # ── AuditJournal: записываем результат шага для памяти ────────
                try:
                    from evaluation.audit_journal import get_journal as _get_journal
                    _mem_dir = (
                        getattr(self.persistent_brain, 'data_dir', '.agent_memory')
                        if self.persistent_brain else '.agent_memory'
                    )
                    _journal = _get_journal(_mem_dir)
                    _actual_tool = 'unknown'
                    if exec_r and isinstance(exec_r, dict):
                        _tool_types = {
                            str(r.get('type', ''))
                            for r in exec_r.get('results', [])
                            if r.get('type')
                        }
                        if _tool_types:
                            _actual_tool = ','.join(sorted(_tool_types))
                    _journal.record_task(
                        goal=str(self._goal or ''),
                        intent=str(step_v.intent),
                        verdict=str(step_v.verdict),
                        score=float(step_v.score),
                        issues=list(step_v.issues),
                        tool_used=_actual_tool,
                    )
                    if not step_v.passed:
                        self._log(
                            f"[evaluate] AuditJournal: зафиксирован провал "
                            f"'{step_v.verdict}' для цели '{str(self._goal or '')[:60]}'"
                        )
                except Exception as _jlog_err:
                    self._log(
                        f"[evaluate] AuditJournal write warn: {_jlog_err}",
                        level='warning',
                    )
            except Exception as _eval_err:
                self._log_exc("evaluate", _eval_err)

            # ── P1: Рефлексия только на провалах и сомнительных успехах ─────────
            # «Сомнительный успех» = strong_cycle, но есть частичные ошибки или
            # confidence < 0.6 — агент должен разобраться, почему не было гладко.
            _is_failure = bool(cycle.errors)
            _is_dubious_success = (
                strong_cycle
                and not _is_failure
                and cycle.confidence.get('act', 1.0) < 0.6
            )
            _needs_reflection = _is_failure or _is_dubious_success or not strong_cycle

            if self.reflection and self._goal and _needs_reflection:
                ref_result = self.reflection.reflect(
                    goal=str(self._goal),
                    result=cycle.action_result,
                    context={
                        'plan': str(cycle.plan)[:300] if cycle.plan else None,
                        'errors': cycle.errors,
                        'observation': str(cycle.observation)[:200] if cycle.observation else None,
                        'weak_cycle': not strong_cycle,
                        'failure_summary': self.failure_tracker.to_prompt_hint(
                            str(self._goal), n=3
                        ),
                    },
                    allow_insights=strong_cycle and _is_failure,
                )
                self._log(
                    f"[evaluate] Рефлексия выполнена "
                    f"(триггер: {'failure' if _is_failure else 'dubious_success' if _is_dubious_success else 'weak'})."
                )
                eval_result = ref_result
            elif self.reflection and self._goal and not _needs_reflection:
                # Чистый успех — дешёвая оценка без LLM
                eval_result = {
                    'goal': str(self._goal),
                    'goal_achieved': True,
                    'analysis': 'Цикл выполнен успешно, рефлексия пропущена (экономия LLM).',
                    'lessons': [],
                    'suggestions': '',
                }
                self._log("[evaluate] Чистый успех — LLM-рефлексия пропущена.")
            elif self.cognitive_core and cycle.action_result and _is_failure:
                # Fallback: через Cognitive Core только на провалах
                eval_result = self.cognitive_core.reasoning(
                    f"Оцени результат действия: {cycle.action_result}\n"
                    f"Цель была: {self._goal}\n"
                    f"Цель достигнута? Что можно улучшить?"
                )
                self._log("[evaluate] Оценка выполнена (fallback).")
            else:
                eval_result = None

            # ── P1: StructuredSkill — record_use после оценки ─────────────
            if getattr(self, '_active_structured_skill', None):
                _skill_success = len(cycle.errors) == 0 and self._has_real_work(cycle)
                _skill_name: str = self._active_structured_skill  # type: ignore[assignment]
                self.structured_skills.record_use(
                    _skill_name, _skill_success
                )
                self._log(
                    f"[evaluate] StructuredSkill '{self._active_structured_skill}' → "
                    f"{'success' if _skill_success else 'fail'}"
                )
                self._active_structured_skill = None

            # Слой 25: Evaluation — записываем KPI цикла
            if self.evaluation and strong_cycle:
                try:
                    success_val = 1.0 if (
                        len(cycle.errors) == 0
                        and self._has_real_work(cycle)
                        and self._execution_fully_successful(cycle)
                    ) else 0.0
                    self.evaluation.record_kpi('cycle_success', success_val)
                    self.evaluation.record_kpi('errors_count', len(cycle.errors))
                    self._log(f"[evaluate/evaluation] KPI записан: success={success_val}")
                except Exception as _e:
                    self._log_exc("evaluate/evaluation", _e)

            # Слой 45: Identity — обновляем модель производительности
            if self.identity:
                try:
                    is_success = (
                        len(cycle.errors) == 0
                        and self._has_real_work(cycle)
                        and self._execution_fully_successful(cycle)
                    )
                    self.identity.record_performance(
                        task=str(self._goal)[:100],
                        success=is_success,
                    )
                except Exception as _e:
                    self._log_exc("evaluate/identity", _e)

            # Слой 41: CausalReasoning — фиксируем исход цикла как следствие плана
            if self.causal and cycle.plan and cycle.action_result:
                try:
                    plan_short = str(cycle.plan)[:60]
                    result_short = str(cycle.action_result)[:60]
                    self.causal.add_causal_relation(
                        cause=f"план: {plan_short}",
                        effect=f"результат: {result_short}",
                        strength=0.7 if not cycle.errors else 0.4,
                    )
                except Exception as _e:
                    self._log_exc("evaluate/causal", _e)

            # Слой 29: SkillLibrary — фиксируем результат использованного навыка
            if self.skill_library and cycle.plan:
                try:
                    is_success = len(cycle.errors) == 0 and self._has_real_work(cycle)
                    if self._is_skill_training_mode() and self._active_training_skill_name:
                        training_skill = self.skill_library.get(self._active_training_skill_name)
                        if training_skill:
                            training_skill.record_use(success=is_success)
                    else:
                        found = self.skill_library.find(str(self._goal)[:80], top_k=1)
                        if found:
                            found[0].record_use(success=is_success)
                except Exception as _e:
                    self._log_exc("evaluate/skill_library", _e)

            return eval_result
        except Exception as e:
            self._record_error("evaluate", e)
            return None

    def _learn(self, cycle: LoopCycle):
        """LEARN: извлечь урок через LearningSystem + записать эпизод в ExperienceReplay.

        Оптимизация (P1): LLM-обучение вызывается не каждый цикл, а:
            - при success=False (провалы важны)
            - при «новом типе ошибки» (определяется failure_taxonomy)
            - раз в _LEARN_BATCH_SIZE циклов батчем
        Эпизод ВСЕГДА записывается в replay (дешёвая операция без LLM).
        """
        try:
            if not self._should_store_cycle_experience(cycle):
                self._log(
                    "[learn] Слабый цикл без изменяющего действия — не сохраняю "
                    "в память и replay."
                )
                return None

            goal_str = str(self._goal) if self._goal else ""
            plan_str = str(cycle.plan)[:300] if cycle.plan else ""
            result_str = str(cycle.action_result)[:300] if cycle.action_result else ""
            eval_str = str(cycle.evaluation)[:300] if cycle.evaluation else ""

            is_success = (
                len(cycle.errors) == 0
                and self._has_real_work(cycle)
                and self._execution_fully_successful(cycle)
            )

            # ── Классифицируем ошибки через failure taxonomy ──────────────────
            is_new_error_type = False
            for err in cycle.errors:
                # STOP:* — мета-ошибки (CATEGORY_REPEAT, NO_EFFECT и т.д.).
                # Не записываем в failure_tracker, чтобы не создавать
                # самоусиливающийся цикл (мета-ошибка → unknown → ещё мета-ошибка).
                if str(err).startswith('STOP:'):
                    continue
                cf = self.failure_tracker.record(err, goal=goal_str)
                # Записываем в operational_memory
                self.operational_memory.record_failure(
                    goal=goal_str,
                    failed_step=plan_str[:100],
                    category=cf.category.value,
                    signature=cf.signature_matched,
                )
                # Новый тип ошибки = первое появление этой категории
                if self.failure_tracker.consecutive_count(cf.category) == 1:
                    is_new_error_type = True

            if is_success:
                self.failure_tracker.record_success()
                # Записываем успешную процедуру в operational_memory
                self.operational_memory.record_procedure(
                    goal=goal_str,
                    steps=[plan_str],
                    success=True,
                    cycles_used=1,
                )

            # ── Решаем: запускать ли дорогое LLM-обучение ─────────────────────
            cycles_since_learn = self._cycle_count - self._last_learn_cycle
            should_learn_now = (
                not is_success                           # провал → учимся
                or is_new_error_type                     # новый тип ошибки → учимся
                or cycles_since_learn >= self._LEARN_BATCH_SIZE  # батч по расписанию
            )

            # Извлекаем детальные tool-вызовы из action_result
            tool_actions = []
            _ar = cycle.action_result
            if isinstance(_ar, dict):
                for r in _ar.get('results', []):
                    if isinstance(r, dict):
                        tool_actions.append({
                            'tool': r.get('type', 'unknown'),
                            'success': r.get('success', False),
                            'error': str(r.get('error', ''))[:200] if r.get('error') else None,
                            'input_short': str(r.get('input', ''))[:200],
                            'output_short': str(r.get('output', ''))[:200],
                        })

            # Слой 33: DataLifecycle — обслуживание базы знаний раз в 50 циклов
            if self.data_lifecycle and self._cycle_count % self.config.data_lifecycle_interval == 0:
                try:
                    stats = self.data_lifecycle.run_maintenance()
                    self._log(f"[learn/data_lifecycle] Обслуживание: {stats}")
                except Exception as _e:
                    self._log_exc("learn/data_lifecycle", _e)

            # Слой 29: SkillLibrary — записываем навык из успешного опыта
            if self.skill_library and is_success and plan_str:
                try:
                    if self._is_skill_training_mode() and self._active_training_skill_name:
                        self._log(
                            f"[learn/skill_library] Прогресс навыка '{self._active_training_skill_name}' "
                            f"зафиксирован без создания новых навыков."
                        )
                    else:
                        self.skill_library.learn_from_experience(
                            task=goal_str[:100],
                            solution=plan_str[:300],
                            success=True,
                        )
                        self._log("[learn/skill_library] Навык обновлён из опыта.")
                except Exception as _e:
                    self._log_exc("learn/skill_library", _e)

            # 1. Записываем эпизод в ExperienceReplay с детальными tool-вызовами (дёшево, без LLM)
            if self.experience_replay:
                # actions = реальные tool-вызовы (если есть), иначе текст плана
                ep_actions = []
                if tool_actions:
                    for ta in tool_actions:
                        status = 'OK' if ta['success'] else f"FAIL: {ta['error'] or '?'}"
                        ep_actions.append(f"[{ta['tool']}] {status} | {ta['input_short'][:100]}")
                else:
                    ep_actions = [plan_str]
                self.experience_replay.add(
                    goal=goal_str,
                    actions=ep_actions,
                    outcome=result_str,
                    success=is_success,
                    context={
                        "cycle_id": cycle.cycle_id,
                        "evaluation": eval_str,
                        "errors": cycle.errors,
                        "tool_details": tool_actions,
                    },
                )
                _n_tools = len(tool_actions)
                self._log(f"[learn] Эпизод записан в ExperienceReplay ({_n_tools} tool-вызовов).")

            # Fallback: записываем эпизод в KnowledgeSystem
            elif self.knowledge_system:
                self.knowledge_system.record_episode(
                    task=goal_str,
                    action=plan_str,
                    result=result_str,
                    success=is_success,
                    notes=eval_str,
                )

            # ── P1: LLM-обучение только если should_learn_now ────────────────
            if not should_learn_now:
                self._log(
                    f"[learn] Успешный цикл, не новый тип ошибки — "
                    f"LLM-обучение отложено (до батча через "
                    f"{self._LEARN_BATCH_SIZE - cycles_since_learn} циклов)."
                )
                return None

            self._last_learn_cycle = self._cycle_count

            # 2. Извлекаем структурированные знания через LearningSystem
            if self.learning_system and cycle.evaluation:
                # Формируем детальный отчёт включая каждый tool-вызов
                tool_report = ""
                if tool_actions:
                    lines = []
                    for ta in tool_actions:
                        status = '✅' if ta['success'] else '❌'
                        line = f"  {status} {ta['tool']}: {ta['input_short'][:150]}"
                        if ta['error']:
                            line += f" → ошибка: {ta['error'][:100]}"
                        lines.append(line)
                    tool_report = "\nИнструменты:\n" + "\n".join(lines) + "\n"
                content = (
                    f"Цель: {goal_str}\n"
                    f"План: {plan_str}\n"
                    f"{tool_report}"
                    f"Результат: {result_str}\n"
                    f"Оценка: {eval_str}\n"
                    f"Ошибки: {cycle.errors}"
                )
                entry = self.learning_system.learn_from(
                    content=content,
                    source_type="cycle",
                    source_name=f"cycle_{cycle.cycle_id}",
                    tags=["autonomous_loop", "experience"],
                )
                lesson = entry.get("extracted", "")
                if isinstance(lesson, dict):
                    lesson = lesson.get("summary", "") or lesson.get("text", "") or str(lesson)
                lesson = str(lesson) if lesson else ""
                if lesson:
                    self._log(f"[learn] Урок (LearningSystem): {str(lesson)[:80]}...")
                    # Слой 46: KnowledgeVerification — верифицируем раз в 3 цикла
                    if self.knowledge_verifier and self._cycle_count % self.config.knowledge_verify_interval == 0:
                        try:
                            vr = self.knowledge_verifier.verify(str(lesson)[:200])
                            status = getattr(vr, 'status', '?')
                            self._log(f"[learn/verifier] Верификация урока: {status}")
                        except Exception as _e:
                            self._log_exc("learn/verifier", _e)
                return lesson

            # Fallback: через reasoning (проходит через CognitiveCore pipeline)
            if self.cognitive_core and cycle.evaluation:
                lesson = self.cognitive_core.reasoning(
                    f"На основе этого цикла работы извлеки один главный урок "
                    f"(1-2 предложения):\n"
                    f"Цель: {goal_str}\n"
                    f"Результат: {result_str}\n"
                    f"Оценка: {eval_str}\n"
                    f"Ошибки: {cycle.errors}",
                )
                self._log(f"[learn] Урок (fallback): {lesson[:80]}...")
                if self.knowledge_system and lesson:
                    self.knowledge_system.store_long_term(
                        f"lesson_cycle_{cycle.cycle_id}", lesson,
                        source='autonomous_loop', trust=0.6,
                    )
                return lesson

            self._log("[learn] Нет данных для обучения.")
            return None
        except Exception as e:
            self._record_error("learn", e)
            return None

    # ── Interrupt Handling ────────────────────────────────────────────────────

    def push_interrupt(self, event: str, priority: int = 2, source: str = 'external') -> None:
        """Добавить прерывание в очередь.

        priority:
          1 – критическое: немедленно прерывает текущий цикл
          2 – высокое: вызывает перепланирование до фазы ACT
          3+ – обычное: обрабатывается в начале следующего цикла
        """
        self._interrupt_queue.append({
            'priority': int(priority),
            'event': str(event),
            'source': str(source),
        })
        # Сортируем по приоритету: меньше = важнее
        self._interrupt_queue.sort(key=lambda x: x['priority'])
        self._log(
            f"[interrupt] Получено прерывание p={priority} от «{source}»: {event[:120]}",
            level='warning' if priority <= 2 else 'info',
        )

    def _check_interrupt(self, cycle: 'LoopCycle', phase: str) -> bool:
        """Обрабатывает прерывания из очереди.

        Возвращает True, если цикл должен быть прерван немедленно.
        """
        if not self._interrupt_queue:
            return False

        top = self._interrupt_queue[0]

        if top['priority'] == 1:
            # Критическое — прерываем цикл немедленно
            self._interrupt_queue.pop(0)
            cycle.errors.append(
                f"interrupt[p1]: цикл прерван из-за критического события — {top['event'][:120]}"
            )
            self._log(
                f"[interrupt] КРИТИЧЕСКОЕ p=1 ({top['source']}): {top['event'][:120]}. "
                "Цикл прерван.",
                level='warning',
            )
            if self.telegram_bot and self.telegram_chat_id:
                try:
                    self.telegram_bot.send(
                        self.telegram_chat_id,
                        f"🚨 Цикл #{cycle.cycle_id} ПРЕРВАН\n"
                        f"Событие: {top['event'][:200]}\n"
                        f"Источник: {top['source']}",
                    )
                except Exception as _e:
                    self._log_exc("telegram", _e)
            return True

        if top['priority'] == 2 and phase == 'pre_act':
            # Высокое — перепланирование до действия
            self._interrupt_queue.pop(0)
            self._log(
                f"[interrupt] Высокий приоритет p=2 ({top['source']}): {top['event'][:120]}. "
                "Перепланирование…",
                level='warning',
            )
            # Инжектируем событие в контекст цикла через анализ
            if cycle.analysis is not None:
                injection = f"\n[INTERRUPT] {top['event']}"
                if isinstance(cycle.analysis, str):
                    cycle.analysis = cycle.analysis + injection
                # Перепланируем
                cycle.plan = self._plan(cycle.analysis)
                cycle.confidence['plan'] = round(cycle.confidence.get('plan', 0.7) * 0.8, 2)
            return False

        # priority >= 3 — в начале следующего цикла _observe увидит очередь
        return False

    def _clear_low_priority_interrupts(self) -> None:
        """Удаляет из очереди прерывания с приоритетом >= 3 (обычные)."""
        self._interrupt_queue = [i for i in self._interrupt_queue if i['priority'] < 3]

    def _confidence_gate(self, plan: str | None, _cycle_id: int) -> tuple[float, str]:
        """
        Оценивает уверенность агента перед исполнением плана (Этап 3 архитектуры:
        self-model ограничения — где агент уверен, а где обязан эскалировать).

        Возвращает (confidence: float 0..1, reason: str).
        Низкая уверенность (< 0.25) → предупреждение в Telegram + лог.

        Факторы снижения уверенности:
          - план содержит маркеры неопределённости ("не уверен", "unclear", etc.)
          - последние N циклов завершались с ошибками
          - бюджет почти исчерпан (< 15% осталось)
        """
        confidence = 0.75  # базовая уверенность агента
        reasons: list[str] = []

        plan_text = (plan or '').lower()

        # 1. Маркеры неопределённости в тексте плана
        import re as _re_conf
        _uncertainty_re = _re_conf.compile(
            r'не уверен|unclear|не знаю|затрудняюсь|недостаточно данных|'
            r'требует уточнения|невозможно определить|неизвестно|'
            r"i'm not sure|i don't know|uncertain|insufficient data",
            _re_conf.IGNORECASE,
        )
        if _uncertainty_re.search(plan_text):
            confidence -= 0.30
            reasons.append("план содержит маркеры неопределённости")

        # 2. Последние 5 циклов — смотрим процент ошибок
        if self._history:
            recent = self._history[-5:]
            error_count = sum(
                1 for c in recent
                if not c.success
            )
            if error_count >= 3:
                confidence -= 0.25
                reasons.append(f"последние циклы: {error_count}/5 с ошибками")
            elif error_count >= 2:
                confidence -= 0.10
                reasons.append(f"последние циклы: {error_count}/5 с ошибками")

        # 3. Бюджет почти исчерпан
        if self.budget_control:
            try:
                status = self.budget_control.get_status()
                money_status = status.get('money', {}) if isinstance(status, dict) else {}
                spent = float(money_status.get('spent', 0.0))
                limit = float(money_status.get('limit', 0.0) or 0.0)
                if limit > 0 and spent / limit > 0.85:
                    confidence -= 0.15
                    reasons.append(f"бюджет исчерпан на {spent/limit:.0%}")
            except Exception as _e:
                self._log_exc("confidence", _e)

        confidence = round(max(0.0, min(1.0, confidence)), 2)
        reason = "; ".join(reasons) if reasons else "всё в норме"
        return confidence, reason

    def _repair(self, cycle: LoopCycle):
        """REPAIR: если были ошибки — автоматический саморемонт."""
        if not cycle.errors:
            return None  # Всё ок, ремонт не нужен

        if not self.self_repair:
            self._log("[repair] Self-Repair не подключён, ошибки не обработаны.")
            return None

        try:
            import re as _re
            repairs = []
            seen_patterns: set[str] = set()
            for error_msg in cycle.errors:
                if len(repairs) >= self._max_repairs_per_cycle:
                    self._log(
                        f"[repair] Лимит ремонтов за цикл достигнут: {self._max_repairs_per_cycle}",
                        level='warning',
                    )
                    break

                msg_lower = str(error_msg).lower()
                if 'semantic gate rejected plan, replanning' in msg_lower:
                    self._log(
                        f"[repair] Пропуск мета-ошибки: {self._preview(error_msg, 80)}",
                        level='warning',
                    )
                    continue

                # STOP:* / STEP_EVAL — мета-ошибки (оценки / внутренние стопы),
                # не аппаратный/кодовый сбой. Не эскалировать.
                if msg_lower.startswith(('stop:', 'step_eval:')):
                    self._log(
                        f"[repair] Пропуск оценочной ошибки (quality assessment): "
                        f"{self._preview(error_msg, 80)}",
                        level='warning',
                    )
                    continue

                norm = self._normalize_error_pattern(str(error_msg))
                if norm in seen_patterns:
                    self._log(
                        f"[repair] Дубликат ошибки в цикле пропущен: {self._preview(error_msg, 80)}",
                        level='warning',
                    )
                    continue
                seen_patterns.add(norm)

                # Извлекаем путь к файлу из аннотации traceback, если есть
                _fm = _re.search(r'\[FILE:([^\]]+)\]', error_msg)
                component = _fm.group(1) if _fm else f"loop_phase_{cycle.phase.value}"
                incident = self.self_repair.report_failure(
                    failure_type=self.self_repair.classify_from_message(
                        error_msg.lower()
                    ),
                    description=error_msg,
                    component=component,
                    context={'cycle_id': cycle.cycle_id, 'goal': str(self._goal)},
                )
                repairs.append(incident)
                self._log(
                    f"[repair] Инцидент: {self._preview(incident.get('result', '?'), 40)} — "
                    f"{self._preview(incident.get('action_taken', ''), 80)}"
                )

            # Логируем эволюцию только если что-то реально починили
            fixed_count = sum(1 for r in repairs if r.get('result') == 'fixed')
            if fixed_count > 0 and self.persistent_brain:
                self.persistent_brain.record_evolution(
                    event="self_repair",
                    details=f"Цикл #{cycle.cycle_id}: "
                            f"исправлено {fixed_count} из {len(repairs)} ошибок. "
                            f"Типы: {'; '.join(set(r.get('failure_type','?') for r in repairs))}",
                )

            # ── FTracker + OpMem: записываем результаты ремонта ─────────
            for inc in repairs:
                ft = inc.get('failure_type', 'unknown')
                desc = str(inc.get('description', ''))[:200]
                was_fixed = inc.get('result') == 'fixed'
                if was_fixed:
                    # Ремонт удался → record_success + записать recovery в OpMem
                    self.failure_tracker.record_success()
                    action_taken = str(inc.get('action_taken', ''))[:200]
                    if action_taken:
                        self.operational_memory.record_recovery(
                            goal=str(self._goal or ''),
                            failed_step=desc[:100],
                            category=ft,
                            recovery=action_taken,
                        )
                else:
                    # Ремонт не удался → record в FTracker
                    self.failure_tracker.record(
                        error_msg=desc,
                        goal=str(self._goal or ''),
                    )

            # ── Замыкание петли: отслеживаем частоту ошибок ─────────────
            self._track_error_patterns(cycle.errors, cycle)

            return repairs
        except Exception as e:
            self._record_error("repair", e)
            return None

    # ── Замыкание петли обучения ──────────────────────────────────────────────

    # Словарь: фрагмент ошибки → (ключевые слова для цели, готовый код)
    # Когда одна и та же ошибка встречается ≥3 раз — автоматически регистрируется
    # как dynamic skill и персистируется в .agent_memory/local_skills.json
    _CODIFIABLE_ERROR_MAP: list[tuple[str, list[str], str]] = [
        (
            "'float' object has no attribute",
            ['cpu percent', 'ram percent', 'memory percent',
             'использование памят', 'памят', 'загрузка cpu', 'загрузка памят'],
            '```python\nimport psutil, os\nvm = psutil.virtual_memory()\ncpu_pct = psutil.cpu_percent(interval=0.3)\ndisk = psutil.disk_usage(os.path.abspath("."))\nprint(f"CPU: {cpu_pct}%  RAM: {vm.percent}%  Disk: {disk.percent}%")\n```',
        ),
        (
            "as_dict() got an unexpected keyword argument",
            ['службы', 'сервисы', 'windows services', 'win_service'],
            '```python\nimport psutil\nservices = []\ntry:\n    for svc in psutil.win_service_iter():\n        try: services.append(svc.as_dict())\n        except (OSError, Exception): continue\nexcept AttributeError: pass\nrunning = [s for s in services if s.get("status") == "running"]\nprint(f"Служб: {len(services)}, запущено: {len(running)}")\n```',
        ),
        (
            "запрещён в sandbox",
            ['проверь систему', 'check system', 'системная проверка', 'статус агента'],
            '```python\nimport os\nbase = os.path.abspath(".")\nprint("Агент работает в:", base)\nprint("Файлы:", [f for f in os.listdir(base) if f.endswith(".py")][:5])\n```',
        ),
        (
            "No such file or directory",
            ['outputs', 'данные', 'data', 'файл результата', 'сохранить результат'],
            '```python\nimport os\noutdir = os.path.join(os.path.abspath("."), "outputs")\nos.makedirs(outdir, exist_ok=True)\nfiles = os.listdir(outdir)\nprint(f"outputs/ содержит {len(files)} файлов:", files[:10])\n```',
        ),
        (
            "process no longer exists",
            ['топ процессов', 'список процессов', 'ps aux', 'запущенные процессы'],
            '```python\nimport psutil\nprocs = []\nfor p in psutil.process_iter(["pid","name","memory_percent","cpu_percent","status"]):\n    try: procs.append(p.info)\n    except (psutil.NoSuchProcess, psutil.AccessDenied): continue\nprint(f"Процессов: {len(procs)}")\nfor p in sorted(procs, key=lambda x: x.get("memory_percent") or 0, reverse=True)[:5]:\n    print(f"  {p.get("name","?")} mem={p.get("memory_percent") or 0:.1f}%")\n```',
        ),
    ]

    @staticmethod
    def _normalize_error_pattern(msg: str) -> str:
        """Нормализует сообщение об ошибке — убирает переменные части (пути, числа, имена)."""
        import re as _re
        s = msg.lower()
        s = _re.sub(r"'[^']{1,80}'", "'?'", s)     # строки в кавычках
        s = _re.sub(r'"[^"]{1,80}"', '"?"', s)     # строки в двойных кавычках
        s = _re.sub(r'\d+', 'N', s)                  # числа
        s = _re.sub(r'[a-z]:\\[^\s]+', 'PATH', s)   # Windows-пути
        s = _re.sub(r'/[^\s]+', 'PATH', s)            # Unix-пути
        s = _re.sub(r'\s+', ' ', s).strip()
        return s[:120]

    def _track_error_patterns(self, errors: list[str], _cycle: 'LoopCycle'):
        """Считает частоту каждой ошибки. При достижении порога — авто-кодифицирует."""
        if not errors:
            return
        # Ограничиваем рост словарей ошибок (защита от утечки памяти)
        if len(self._error_hit_counter) > 200:
            top_keys = sorted(
                self._error_hit_counter,
                key=lambda k: self._error_hit_counter[k],
                reverse=True,
            )[:100]
            self._error_hit_counter = {
                k: self._error_hit_counter[k] for k in top_keys
            }
            self._error_goal_keywords = {
                k: v for k, v in self._error_goal_keywords.items()
                if k in self._error_hit_counter
            }
        goal_words = [
            w for w in (str(self._goal or '') + ' ' +
                        str(self._subgoal_queue[0] if self._subgoal_queue else '')).lower().split()
            if len(w) > 3
        ][:8]
        for error_msg in errors:
            key = self._normalize_error_pattern(error_msg)
            self._error_hit_counter[key] = self._error_hit_counter.get(key, 0) + 1
            # Накапливаем ключевые слова цели связанные с этой ошибкой
            existing = self._error_goal_keywords.get(key, [])
            for w in goal_words:
                if w not in existing:
                    existing.append(w)
            self._error_goal_keywords[key] = existing[:12]
            count = self._error_hit_counter[key]
            if count == 3:
                self._log(
                    f"[learn] ПАТТЕРН ОШИБКИ встречен {count} раз: "
                    f"{error_msg[:80]} — пытаюсь авто-кодифицировать."
                )
                self._try_codify_error(key, error_msg)

    def _try_codify_error(self, norm_key: str, error_msg: str):
        """Если ошибка известна — регистрирует готовый шаблон. Иначе — генерирует."""
        # Сначала проверяем hardcoded шаблоны
        for err_fragment, skill_keywords, skill_code in self._CODIFIABLE_ERROR_MAP:
            if err_fragment.lower() in error_msg.lower():
                goal_kws = self._error_goal_keywords.get(norm_key, [])
                combined_keywords = list(dict.fromkeys(skill_keywords + goal_kws))
                self.register_dynamic_skill(
                    keywords=combined_keywords,
                    code=skill_code,
                    trigger_error=error_msg[:120],
                )
                return

        # Для неизвестных ошибок — генерируем эвристический шаблон
        goal_kws = self._error_goal_keywords.get(norm_key, [])
        if not goal_kws:
            self._log(
                f"[learn] Неизвестный паттерн (нет ключевых слов): {error_msg[:80]}"
            )
            return

        fix_hint = self._infer_fix_hint(error_msg)
        if fix_hint:
            code_template = fix_hint['code']
            keywords = list(dict.fromkeys(goal_kws + fix_hint.get('keywords', [])))
            self.register_dynamic_skill(
                keywords=keywords,
                code=code_template,
                trigger_error=error_msg[:120],
            )
            self._log(
                f"[learn] AUTO-SKILL сгенерирован для: {error_msg[:60]} "
                f"→ [{', '.join(keywords[:3])}]"
            )
        else:
            self._log(
                f"[learn] Неизвестный паттерн (не кодифицирован): {error_msg[:80]}"
            )

    @staticmethod
    def _infer_fix_hint(error_msg: str) -> dict | None:
        """Эвристически определяет тип ошибки и возвращает шаблон исправления."""
        msg = error_msg.lower()
        if 'timeout' in msg or 'timed out' in msg:
            return {
                'keywords': ['timeout', 'retry'],
                'code': (
                    '```python\n'
                    'print("Timeout detected in previous operation.")\n'
                    'print("Retrying with reduced scope in next cycle.")\n'
                    '```'
                ),
            }
        if 'permission' in msg or 'access denied' in msg:
            return {
                'keywords': ['permission', 'access'],
                'code': (
                    '```python\n'
                    'import os\n'
                    'cwd = os.getcwd()\n'
                    'can_write = os.access(cwd, os.W_OK)\n'
                    'print(f"Working directory: {cwd}")\n'
                    'print(f"Write permission: {can_write}")\n'
                    '```'
                ),
            }
        if 'not found' in msg or 'no such file' in msg or 'filenotfounderror' in msg:
            return {
                'keywords': ['file', 'missing', 'path'],
                'code': (
                    '```python\n'
                    'import os\n'
                    'os.makedirs("outputs", exist_ok=True)\n'
                    'print(f"Директория outputs/ создана")\n'
                    '```'
                ),
            }
        if 'connection' in msg or 'network' in msg or 'refused' in msg:
            return {
                'keywords': ['network', 'connection', 'retry'],
                'code': (
                    '```python\n'
                    'print("Network/connection error detected.")\n'
                    'print("Will retry in next cycle with fallback.")\n'
                    '```'
                ),
            }
        if 'memory' in msg or 'oom' in msg or 'killed' in msg:
            return {
                'keywords': ['memory', 'oom'],
                'code': (
                    '```python\n'
                    'import gc\n'
                    'collected = gc.collect()\n'
                    'print(f"Memory cleanup: {collected} objects collected")\n'
                    '```'
                ),
            }
        return None

    def register_dynamic_skill(
        self,
        keywords: list[str],
        code: str,
        trigger_error: str = '',
    ):
        """Регистрирует новый навык, выученный во время работы.

        Добавляет в runtime-список и сохраняет на диск.
        При следующем запуске навык будет загружен автоматически.
        """
        # Не регистрируем дубликаты по первому ключевому слову
        anchor = keywords[0] if keywords else ''
        for existing_kws, _ in self._dynamic_skills:
            if anchor in existing_kws:
                return
        self._dynamic_skills.append((keywords, code))
        self._log(
            f"[learn] DYNAMIC_SKILL зарегистрирован: [{', '.join(keywords[:3])}] "
            f"— теперь эта задача выполняется без LLM."
        )
        # P1: дублируем в StructuredSkillRegistry для приоритетного поиска
        try:
            structured = StructuredSkill.from_legacy(keywords, code)
            if not self.structured_skills.has(structured.name):
                self.structured_skills.register(structured)
        except Exception as _ss_err:
            self._log_exc("learn", _ss_err)
        # Сохраняем на диск
        try:
            os.makedirs(os.path.dirname(self._dynamic_skills_path) or '.', exist_ok=True)
            existing: list[dict] = []
            if os.path.exists(self._dynamic_skills_path):
                with open(self._dynamic_skills_path, 'r', encoding='utf-8') as f:
                    existing = json.load(f)
            # Проверяем дубликат в файле
            if not any(e.get('keywords', [''])[0] == anchor for e in existing):
                existing.append({
                    'keywords': keywords,
                    'code': code,
                    'trigger_error': trigger_error,
                    'registered_at': time.time(),
                })
                with open(self._dynamic_skills_path, 'w', encoding='utf-8') as f:
                    json.dump(existing, f, ensure_ascii=False, indent=2)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            self._log(f"[learn] Не удалось сохранить dynamic skill: {e}")

    def _load_dynamic_skills(self):
        """Загружает навыки, выученные в прошлых сессиях, из .agent_memory/local_skills.json."""
        try:
            if not os.path.exists(self._dynamic_skills_path):
                return
            with open(self._dynamic_skills_path, 'r', encoding='utf-8') as f:
                entries = json.load(f)
            for entry in entries:
                kws = entry.get('keywords', [])
                code = entry.get('code', '')
                if kws and code:
                    self._dynamic_skills.append((kws, code))
            if self._dynamic_skills:
                self._log(
                    f"[learn] Загружено {len(self._dynamic_skills)} выученных навыков "
                    f"из {self._dynamic_skills_path}"
                )
        except (OSError, json.JSONDecodeError, ValueError):
            pass  # Файл повреждён или ещё не существует — игнорируем

    # ── P1: Stop Conditions — детерминированные правила остановки ────────────
    def _apply_stop_conditions(self, cycle: LoopCycle):
        """Детерминированные правила аварийной остановки цикла.

        Правила (стоп-условия) — без LLM:
          1. Action fingerprint duplication: одно и то же действие >2 раз подряд.
          2. Verify fail streak: 2 подряд провала верификации → принудительный реплан.
          3. Replan counter: 3 реплана подряд без нового task_graph → эскалация.
          4. Utility gate: действие не изменило состояние среды → skip.
        """
        ar = cycle.action_result

        # --- 1. Action fingerprint dedup ---
        fp = self._action_fingerprint(ar)
        self._action_fingerprints.append(fp)
        # храним только последние 10
        if len(self._action_fingerprints) > 10:
            self._action_fingerprints = self._action_fingerprints[-10:]
        # если последние 3 одинаковы — дублирование
        if len(self._action_fingerprints) >= 3 and len(set(self._action_fingerprints[-3:])) == 1:
            cycle.errors.append(
                'STOP:ACTION_REPEAT — одно и то же действие выполнено 3+ раз подряд'
            )
            self._plan_cache_value = None
            self._plan_cache_reuse = 0
            self._log(
                '[stop] Action fingerprint повторяется 3+ раз — план сброшен.',
                level='warning',
            )

        # --- 2. Verify fail streak ---
        verify_passed = len(cycle.errors) == 0
        self._last_verify_results.append(verify_passed)
        if len(self._last_verify_results) > 3:
            self._last_verify_results = self._last_verify_results[-3:]
        # 2 подряд провала → mandatory replan
        if (
            len(self._last_verify_results) >= 2
            and not any(self._last_verify_results[-2:])
        ):
            self._plan_cache_value = None
            self._plan_cache_reuse = 0
            self._replan_count += 1
            self._log(
                f'[stop] 2 подряд провала верификации — реплан #{self._replan_count}.',
                level='warning',
            )

        # --- 3. Replan counter overflow ---
        if self._replan_count >= 3:
            cycle.errors.append(
                'STOP:REPLAN_OVERFLOW — 3 реплана без прогресса, эскалация'
            )
            self._log(
                '[stop] 3 реплана без нового графа — эскалация.',
                level='error',
            )
            # Сброс счётчика для следующего «окна»
            self._replan_count = 0

        # Сброс replan_count при успешном verify
        if verify_passed:
            self._replan_count = 0

        # --- 4. Utility gate: действие ничего не изменило ---
        if ar and isinstance(ar, dict) and not cycle.errors:
            exec_r = self._extract_execution_result(ar)
            if isinstance(exec_r, dict):
                results = exec_r.get('results', [])
                if results and not any(
                    r.get('success') and r.get('output', '').strip()
                    for r in results
                ):
                    cycle.errors.append(
                        'STOP:NO_EFFECT — действие не произвело наблюдаемого эффекта'
                    )
                    self._log('[stop] Действие без эффекта — помечено как ошибка.')

        # --- 5. FTracker: 5+ consecutive ошибок одной категории → stop ---
        _dom = self.failure_tracker.dominant_failure()
        if _dom:
            _goal_failures = self.failure_tracker.goal_failure_summary(
                str(self._goal or '')
            )
            _dom_count = _goal_failures.get(_dom.value, 0)
            if _dom_count >= 5:
                cycle.errors.append(
                    f'STOP:CATEGORY_REPEAT — категория {_dom.value} '
                    f'повторилась {_dom_count}x подряд'
                )
                self._log(
                    f'[stop] FTracker: {_dom.value} повторяется {_dom_count}x — эскалация.',
                    level='error',
                )

    @staticmethod
    def _action_fingerprint(action_result) -> str:
        """Детерминированный fingerprint результата действия для dedup."""
        if not action_result:
            return 'empty'
        raw = json.dumps(action_result, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _replay_experience(self, _cycle: LoopCycle):
        """REPLAY: переанализировать прошлый опыт через ExperienceReplay.

        Оптимизация (P1): replay запускается не каждый 3-й цикл, а:
            - каждые _REPLAY_INTERVAL (10) циклов
            - или когда накопилось _REPLAY_FAILURE_BATCH (5) провалов
            - каждые 20 циклов — find_patterns (было 10)
        """
        if not self.experience_replay:
            return None
        try:
            buffer_size = self.experience_replay.size
            if buffer_size < 3:
                return None

            # Считаем накопленные провалы с последнего replay
            failure_count = sum(
                1 for ep in self.experience_replay.recent_episodes(self._REPLAY_INTERVAL)
                if not ep.success
            )
            by_schedule = (self._cycle_count % self._REPLAY_INTERVAL == 0)
            by_failures = (failure_count >= self._REPLAY_FAILURE_BATCH)

            if not by_schedule and not by_failures:
                return None

            # Фокус: при накоплении провалов — анализируем только провалы
            focus = 'failures' if by_failures else 'mixed'
            lessons = self.experience_replay.replay(
                n=min(5, buffer_size), focus=focus
            )

            if lessons:
                self._log(
                    f"[replay] Извлечено {len(lessons)} уроков "
                    f"(триггер: {'failures_batch' if by_failures else 'schedule'})."
                )
                if self.reflection:
                    for lesson in lessons:
                        text = lesson.get('lesson', '')
                        if text:
                            self.reflection.add_insight(text)

            # Каждые N циклов ищем паттерны
            if self._cycle_count % self.config.replay_pattern_interval == 0:
                patterns = self.experience_replay.find_patterns()
                if patterns:
                    self._log(f"[replay] Найдено {len(patterns)} паттернов.")
                    if self.self_improvement:
                        for pat in patterns[:3]:
                            pat_text = pat.get('pattern', '')
                            if pat_text:
                                self.self_improvement.propose(
                                    area='experience_pattern',
                                    current_behavior=pat_text[:200],
                                    proposed_change=(
                                        f'Избегать паттерна: {pat_text[:150]}'
                                    ),
                                    rationale='Обнаружен в опыте через find_patterns()',
                                    priority=2,
                                )
                    if self.persistent_brain:
                        self.persistent_brain.record_evolution(
                            event="patterns_found",
                            details=f"Найдено {len(patterns)} паттернов в опыте. "
                                    f"Примеры: {str(patterns[:2])[:200]}",
                        )

            # P3: Auto-skill generation — каждые N циклов ищем повторяющиеся цепочки
            if self._cycle_count % self.config.replay_pattern_interval == 0:
                try:
                    candidates = self.experience_replay.extract_repeating_chains(
                        min_occurrences=3, min_actions=2,
                    )
                    for cand in candidates[:5]:
                        skill = self.structured_skills.create_from_chain(cand)
                        if skill:
                            self._log(
                                f"[replay] AUTO_SKILL создан: '{skill.name}' "
                                f"(из {cand['count']} повторений цепочки)"
                            )
                            # Связь: auto-skill → OperationalMemory (процедурная)
                            self.operational_memory.record_procedure(
                                goal=cand.get('goal_pattern', skill.name),
                                steps=skill.steps,
                                success=True,
                                cycles_used=1,
                            )
                except Exception as _asc_err:
                    self._log(
                        f"[replay] Auto-skill chain warn: {_asc_err}",
                        level='warning',
                    )

            # Связь: replay-уроки из провалов → OperationalMemory (failure)
            if lessons:
                try:
                    for lesson in lessons:
                        if not lesson.get('success', True):
                            ep_id = lesson.get('episode_id', '')
                            ep = self.experience_replay.get_episode(ep_id)
                            if ep:
                                failed_step = (
                                    ep.actions[-1] if ep.actions
                                    else str(ep.outcome)[:100]
                                )
                                self.operational_memory.record_failure(
                                    goal=ep.goal,
                                    failed_step=str(failed_step)[:200],
                                    category='unknown',
                                    signature=lesson.get('lesson', '')[:100],
                                )
                        # Успешные уроки с рекомендациями → процедурная память
                        elif lesson.get('recommendation'):
                            ep_id = lesson.get('episode_id', '')
                            ep = self.experience_replay.get_episode(ep_id)
                            if ep and ep.actions:
                                self.operational_memory.record_procedure(
                                    goal=ep.goal,
                                    steps=[str(a) for a in ep.actions[:10]],
                                    success=True,
                                )
                except Exception as _lm_err:
                    self._log(
                        f"[replay] Lesson→OperationalMemory warn: {_lm_err}",
                        level='warning',
                    )

            return lessons
        except Exception as e:
            self._record_error("replay", e)
            return None

    def _acquire_knowledge(self, _cycle: LoopCycle):
        """ACQUIRE: проактивное получение новых знаний."""
        # Обрабатываем накопленную очередь LearningSystem (каждые 5 циклов)
        if self.learning_system:
            try:
                # Тренировка навыков: каждые 3 цикла берём наименее практикованный навык
                if self._cycle_count % self.config.skill_prune_interval == 0 and self.skill_library:
                    training_skill = self._select_training_skill()
                    if training_skill:
                        self._queue_training_sources_for_skill(training_skill)
                elif self._is_skill_training_mode():
                    training_skill = self._get_active_training_skill()
                    if training_skill:
                        self._queue_training_sources_for_skill(training_skill)

                queue = getattr(self.learning_system, '_queue', [])
                if queue and (self._cycle_count % self.config.skill_queue_interval == 0 or self._is_skill_training_mode()):
                    self._log(f"[acquire] LearningSystem: обрабатываю {len(queue)} источников из очереди.")
                    results = self.learning_system.process_queue()
                    # Извлекаем реальное содержимое, не только len()
                    n_proc = len(results)
                    n_extracted = sum(1 for r in results if r.get('extracted'))
                    topics = [r.get('source_name', '')[:40] for r in results if r.get('source_name')]
                    self._log(
                        f"[acquire] LearningSystem: обработано {n_proc}, "
                        f"извлечено знаний: {n_extracted}. "
                        + (f"Темы: {', '.join(topics[:5])}" if topics else "")
                    )
                    # Раз в 50 циклов — сводная статистика накопленных знаний
                    if self._cycle_count % self.config.learning_stats_interval == 0 and hasattr(self.learning_system, 'get_stats'):
                        _ls_stats = self.learning_system.get_stats()
                        self._log(
                            f"[acquire] LearningSystem всего выучено: "
                            f"{_ls_stats.get('total_learned', 0)} записей, "
                            f"по типам: {_ls_stats.get('by_source_type', {})}"
                        )
            except Exception as _e:
                self._log_exc("acquire", _e)

        if not self.acquisition_pipeline:
            return None
        try:
            # Acquisition каждые 10 циклов (тяжёлая операция)
            if self._cycle_count % 10 != 0:
                return None

            # Запускаем пайплайн для накопленных источников
            queue_size = (
                self.acquisition_pipeline.queue_size()
                if hasattr(self.acquisition_pipeline, 'queue_size')
                else len(getattr(self.acquisition_pipeline, '_queue', []))
            )
            if queue_size > 0:
                results = self.acquisition_pipeline.run(max_sources=3)
                total_processed = int(results.get('total', 0)) if isinstance(results, dict) else 0
                stored_count = int(results.get('stored', 0)) if isinstance(results, dict) else 0
                filtered_count = int(results.get('filtered', 0)) if isinstance(results, dict) else 0
                failed_count = int(results.get('failed', 0)) if isinstance(results, dict) else 0

                self._last_acquisition_stats = {
                    'last_run_cycle': self._cycle_count,
                    'queue_size': queue_size,
                    'total': total_processed,
                    'stored': stored_count,
                    'filtered': filtered_count,
                    'failed': failed_count,
                }

                self._log(
                    f"[acquire] Обработано {total_processed} источников, "
                    f"сохранено {stored_count}."
                )
                if results and self.persistent_brain:
                    self.persistent_brain.record_evolution(
                        event="knowledge_acquired",
                        details=f"Получено знаний из {total_processed} источников "
                                f"(цикл #{self._cycle_count}).",
                    )
                return results
            else:
                # Очередь пуста — проактивно ищем источники по текущей цели
                topic = None
                try:
                    if self.goal_manager:
                        # get_active — правильное имя метода в GoalManager
                        g = getattr(self.goal_manager, 'get_active', lambda: None)()
                        if g is None:
                            g = getattr(self.goal_manager, 'get_active_goal', lambda: None)()
                        if g:
                            topic = str(getattr(g, 'description', None) or
                                        getattr(g, 'text', None) or g)[:120]
                    # Subgoal как более конкретная тема (если есть)
                    if self._subgoal_queue:
                        _sg = str(self._subgoal_queue[0]).strip()
                        if _sg and len(_sg) < 120:
                            topic = _sg
                    # Fallback: используем текущую цель цикла
                    if not topic and self._goal:
                        topic = str(self._goal)[:120]
                    # Если тема выглядит как мета-инструкция (>80 символов и содержит
                    # многошаговые директивы), выбираем осмысленный термин из ротации
                    _META_MARKERS = ('в каждом цикле', 'делай следующее', 'по порядку:')
                    if topic and (
                        len(topic) > 80
                        and any(m in topic.lower() for m in _META_MARKERS)
                    ):
                        _DISCOVERY_TOPICS = [
                            'автономные агенты машинное обучение',
                            'нейронные сети глубокое обучение',
                            'Python программирование инструменты',
                            'архитектура ИИ систем LLM',
                            'обработка естественного языка NLP',
                            'reinforcement learning агенты',
                            'алгоритмы оптимизации данные',
                            'программная инженерия паттерны',
                        ]
                        topic = _DISCOVERY_TOPICS[self._cycle_count % len(_DISCOVERY_TOPICS)]
                except Exception as _e:
                    self._log_exc("acquire", _e)

                # FTracker: если есть доминирующая категория ошибок — ищем знания по ней
                _acq_dom = self.failure_tracker.dominant_failure()
                if _acq_dom and topic:
                    _cat_topic = _acq_dom.value.replace('_', ' ')
                    topic = f"{topic} решение проблемы {_cat_topic}"
                    self._log(f"[acquire] FTracker: discovery направлен на '{_cat_topic}'")

                if topic and hasattr(self.acquisition_pipeline, 'discover'):
                    self._log(f"[acquire] Очередь пуста — авто-дискавери по теме: {topic[:60]}")
                    try:
                        found = self.acquisition_pipeline.discover(topic, n=3)
                        # HuggingFace: ищем модели по теме (nur wenn Backend включён)
                        if hasattr(self.acquisition_pipeline, 'add_huggingface_models') and \
                                getattr(self.acquisition_pipeline, '_huggingface', None):
                            hf_found = self.acquisition_pipeline.add_huggingface_models(
                                query=topic[:100], limit=3
                            )
                            found = (found or []) + (hf_found or [])
                        # GitHub: ищем репозитории по теме
                        _github_backend = getattr(self.acquisition_pipeline, '_github', None)
                        if _github_backend:
                            try:
                                gh_results = _github_backend.search_repos(
                                    query=topic[:100], sort='stars', per_page=3
                                )
                                items = gh_results.get('items', []) if isinstance(gh_results, dict) else []
                                for repo in items:
                                    repo_url = repo.get('html_url', '')
                                    repo_desc = repo.get('description', '') or ''
                                    if repo_url:
                                        src = self.acquisition_pipeline.add_source(
                                            repo_url,
                                            source_type='github',
                                            tags=['github', 'code'] + topic.split()[:3],
                                        )
                                        if src:
                                            # Сразу заполняем описанием — не читаем весь repo
                                            src.raw_content = (
                                                f"GitHub repo: {repo.get('full_name', repo_url)}\n"
                                                f"Stars: {repo.get('stargazers_count', 0)}\n"
                                                f"Language: {repo.get('language', 'unknown')}\n"
                                                f"Description: {repo_desc}\n"
                                                f"URL: {repo_url}"
                                            )
                                            from knowledge.acquisition_pipeline import SourceStatus
                                            src.status = SourceStatus.PROCESSING
                                            found = (found or []) + [src]
                            except Exception as _ghe:
                                self._log_exc("acquire", _ghe)
                        if found:
                            self._log(f"[acquire] Дискавери: добавлено {len(found)} источников.")
                            # Сразу обрабатываем найденные источники
                            results = self.acquisition_pipeline.run(max_sources=3)
                            stored_count = int(results.get('stored', 0)) if isinstance(results, dict) else 0
                            self._log(f"[acquire] Дискавери: сохранено {stored_count} знаний.")
                            self._last_acquisition_stats = {
                                'last_run_cycle': self._cycle_count,
                                'queue_size': len(found),
                                'total': int(results.get('total', 0)) if isinstance(results, dict) else 0,
                                'stored': stored_count,
                                'filtered': int(results.get('filtered', 0)) if isinstance(results, dict) else 0,
                                'failed': int(results.get('failed', 0)) if isinstance(results, dict) else 0,
                                'auto_discovered': True,
                            }
                            return results
                    except Exception as _de:
                        self._log_exc("acquire", _de)

                self._last_acquisition_stats = {
                    'last_run_cycle': self._cycle_count,
                    'queue_size': 0,
                    'total': 0,
                    'stored': 0,
                    'filtered': 0,
                    'failed': 0,
                }
                self._log("[acquire] Очередь источников пуста.")
                return None
        except Exception as e:
            self._record_error("acquire", e)
            return None

    @staticmethod
    def _is_real_jobs_content(content: str) -> bool:
        """Проверяет, содержит ли файл реальные вакансии, а не мусор/плейсхолдеры."""
        if len(content) < 80:
            return False
        # DSL-команды или код-блоки — это артефакты LLM-плана, не настоящие вакансии
        junk_markers = (
            '```',
            'READ:',
            'WRITE:',
            'SEARCH:',
            'CONTENT:',
            'PYTHON:',
            'BASH:',
            'New job listings with budget',
            'budget and links',
            'Название работы:\n',          # шаблонная строка без значения
        )
        for marker in junk_markers:
            if marker in content:
                return False
        # Должна быть хоть одна строка с реальными данными (URL или $-бюджет или «бюджет»)
        import re as _re
        has_link = bool(_re.search(r'https?://', content))
        has_budget = bool(_re.search(r'\$\d|бюджет|budget|\d+\s*(usd|\$)', content, _re.IGNORECASE))
        return has_link or has_budget

    def _notify_upwork_jobs(self):
        """Отправляет Telegram-уведомление, если файл upwork_jobs.txt изменился."""
        if not self.telegram_bot or not self.telegram_chat_id:
            return
        try:
            jobs_file = os.path.join('outputs', 'upwork_jobs.txt')
            if not os.path.exists(jobs_file):
                return
            with open(jobs_file, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read().strip()
            if not content:
                return
            content_hash = hashlib.md5(content.encode('utf-8')).hexdigest()
            if content_hash == self._last_upwork_hash:
                return
            self._last_upwork_hash = content_hash
            # Отправляем только если это настоящие вакансии, а не мусор/плейсхолдеры
            if not self._is_real_jobs_content(content):
                self._log("[notify] upwork_jobs.txt содержит мусор — уведомление пропущено.")
                return
            # Обрезаем до 3800 символов (лимит Telegram ~4096)
            msg = "🔔 <b>Новые вакансии Upwork:</b>\n\n" + content[:3800]
            self.telegram_bot.send(self.telegram_chat_id, msg)
            self._log("[notify] Upwork-вакансии отправлены в Telegram.")
        except Exception as e:
            self._log_exc("notify", e)

    def _evaluate_learning_quality(self, cycle: LoopCycle):
        """
        Оценивает, какие выученные стратегии были использованы в этом цикле
        и помогли ли они (успех/неудача).

        Стратегии с низким качеством → удаляются.
        Стратегии с высоким качеством → усиливаются.
        """
        try:
            # Фиксируем стратегии из self_improvement (основной путь)
            _consulted_count = 0
            if self.self_improvement and hasattr(self.self_improvement, 'pop_consulted'):
                consulted = self.self_improvement.pop_consulted() or []
                for area in consulted:
                    self.learning_quality.record_use(area, success=cycle.success)
                    _consulted_count += 1

            # FALLBACK: если pop_consulted не дал данных — выводим область из цели
            if _consulted_count == 0:
                goal_str = str(getattr(self, '_goal', '') or '').lower()
                # Определяем область по ключевым словам цели
                _area_map = [
                    (['pdf', 'документ', 'report', 'отчёт'],          'document_creation'),
                    (['excel', 'xlsx', 'spreadsheet', 'таблиц'],       'spreadsheet'),
                    (['python', 'код', 'script', 'code'],              'code_execution'),
                    (['поиск', 'search', 'найди', 'find'],             'web_search'),
                    (['email', 'письм', 'отправ'],                     'email'),
                    (['файл', 'file', 'запис', 'write', 'создай'],     'file_io'),
                    (['анализ', 'analys', 'данн', 'data'],             'data_analysis'),
                    (['перевод', 'translat', 'язык'],                  'translation'),
                    (['telegram', 'бот', 'сообщ', 'message'],         'telegram'),
                    (['график', 'chart', 'визуал', 'plot'],            'visualization'),
                    (['upwork', 'вакансия', 'freelan', 'заказ'],       'upwork'),
                ]
                area = 'general'
                for keywords, label in _area_map:
                    if any(kw in goal_str for kw in keywords):
                        area = label
                        break
                self.learning_quality.record_use(area, success=cycle.success)

                # Также записываем тип плана (bash/python/search/text)
                plan_str = str(getattr(cycle, 'plan', '') or '').lower()
                if '```python' in plan_str:
                    self.learning_quality.record_use('plan_python', success=cycle.success)
                elif '```bash' in plan_str:
                    self.learning_quality.record_use('plan_bash', success=cycle.success)
                elif 'search:' in plan_str:
                    self.learning_quality.record_use('plan_search', success=cycle.success)
                else:
                    self.learning_quality.record_use('plan_text', success=cycle.success)

            # Каждые N циклов — проверяем бесполезные стратегии
            if self._cycle_count % self.config.strategy_review_interval == 0:
                poor = self.learning_quality.get_poor_strategies(min_uses=5)
                if poor and self.self_improvement:
                    for area in poor:
                        score = self.learning_quality.quality_score(area)
                        self._log(
                            f"[quality] Стратегия '{area}' бесполезна "
                            f"(quality={score:.0%}) → удаляю"
                        )
                        # Удаляем из strategy_store
                        store = getattr(self.self_improvement, '_strategy_store', {})
                        store.pop(area, None)
                        # Добавляем инсайт
                        if self.reflection:
                            self.reflection.add_insight(
                                f"Стратегия '{area}' удалена — качество {score:.0%} "
                                f"после {self.learning_quality.get_uses(area)} использований"
                            )

                effective = self.learning_quality.get_effective_strategies(min_uses=3)
                if effective:
                    # Показываем топ-5 по quality_score, только счётчик — без спама
                    top5 = sorted(
                        effective,
                        key=self.learning_quality.quality_score,
                        reverse=True,
                    )[:5]
                    self._log(
                        f"[quality] Эффективных стратегий: {len(effective)}. "
                        f"Топ-5: "
                        f"{', '.join(f'{a}({self.learning_quality.quality_score(a):.0%})' for a in top5)}"
                    )

        except Exception as e:
            self._log_exc("quality", e)

    def _check_learning_triggers(self, cycle: LoopCycle):
        """
        Реактивные триггеры обучения — обучение по ситуации, не по таймеру.

        Триггеры:
            1. Серия неудач (>=3) → срочный replay неудач + рефлексия
            2. Первый успех после неудач → закрепить паттерн успеха
            3. Новая область/тип задачи → проактивный поиск знаний
            4. Повторяющаяся ошибка → целенаправленное обучение
        """
        try:
            # Триггер 1: Серия неудач — экстренный анализ ошибок
            if self._consecutive_failures >= 3 and self.experience_replay:
                self._log("[trigger] Серия из 3+ неудач → экстренный replay ошибок")
                failure_lessons = self.experience_replay.replay(
                    n=min(5, self.experience_replay.size),
                    focus='failures'
                )
                if failure_lessons and self.reflection:
                    for lesson in failure_lessons:
                        text = lesson.get('lesson', '')
                        if text:
                            self.reflection.add_insight(
                                f"[URGENT] Из серии неудач: {text}"
                            )
                # Применяем уроки сразу
                if failure_lessons:
                    self.experience_replay.apply_lessons(failure_lessons)

            # Триггер 2: Первый успех после серии неудач → закрепить
            if (cycle.success and self._consecutive_failures == 0
                    and self._cycle_count > 1
                    and self.experience_replay):
                # Проверяем была ли серия неудач до этого
                recent = list(self._history)[-5:]
                recent_fails = sum(1 for c in recent if not c.success)
                if recent_fails >= 2:
                    self._log("[trigger] Успех после неудач → закрепляю паттерн")
                    success_lessons = self.experience_replay.replay(
                        n=3, focus='successes'
                    )
                    if success_lessons and self.reflection:
                        for lesson in success_lessons:
                            text = lesson.get('lesson', '')
                            if text:
                                self.reflection.add_insight(
                                    f"[SUCCESS PATTERN] {text}"
                                )

            # Триггер 3: Новая область задачи → проактивный поиск знаний
            if (self._goal and self.learning_system
                    and self._cycle_count == 1):
                # На первом цикле с новой целью — проверяем есть ли знания
                goal_str = str(self._goal).lower()
                known = False
                if self.knowledge_system:
                    relevant = self.knowledge_system.get_relevant_knowledge(goal_str)
                    known = bool(relevant)
                if not known:
                    self._log(f"[trigger] Новая область '{goal_str[:50]}' → "
                              f"нет знаний, запрос на обучение")
                    # Ставим в очередь LearningSystem
                    self.learning_system.enqueue(
                        source_type='goal_research',
                        source_name=str(self._goal)[:200],
                        tags=['auto_trigger', 'new_area'],
                    )

            # Триггер 4: Повторяющаяся ошибка → целенаправленный анализ
            if cycle.errors and len(self._history) >= 3:
                current_errors = set(
                    e.split(':')[0] for e in cycle.errors if ':' in e
                )
                if current_errors:
                    repeat_count = 0
                    for prev in self._history[-5:]:
                        prev_errors = set(
                            e.split(':')[0] for e in prev.errors if ':' in e
                        )
                        if current_errors & prev_errors:
                            repeat_count += 1
                    if repeat_count >= 2 and self.reflection:
                        self._log(f"[trigger] Повторяющаяся ошибка ({repeat_count}x) "
                                  f"→ целенаправленная рефлексия")
                        self.reflection.add_insight(
                            f"[REPEATING ERROR] Ошибка '{list(current_errors)[0]}' "
                            f"повторяется {repeat_count}+ раз. "
                            f"Нужна смена подхода."
                        )
        except Exception as e:
            self._log_exc("trigger", e)

    def _improve(
        self,
        cycle: LoopCycle,
        success_rate: float | None = None,
        consecutive_failures: int | None = None,
    ):
        """IMPROVE: эволюция — обновить стратегии на основе накопленного опыта."""
        try:
            if not self.self_improvement:
                # Fallback: через Cognitive Core
                if self.cognitive_core and cycle.evaluation:
                    improvement = self.cognitive_core.strategy_generator(
                        f"Улучши стратегию на основе оценки: {cycle.evaluation}\n"
                        f"Цель: {self._goal}"
                    )
                    self._log("[improve] Стратегия обновлена (через CognitiveCore).")
                    if improvement and self.persistent_brain:
                        self.persistent_brain.record_evolution(
                            event="strategy_evolved",
                            details=f"Fallback CognitiveCore. "
                                    f"Цикл #{self._cycle_count}: {str(improvement)[:150]}",
                        )
                    return improvement
                return None

            # Используем SelfImprovement — полноценная эволюция
            # Каждые N циклов анализируем и генерируем предложения
            if self._cycle_count % self.config.self_improve_interval == 0:
                # ── Skills hygiene: удаляем навыки с плохой статистикой ────
                _weak_skills = [
                    s for s in self.structured_skills.all_skills()
                    if s.use_count >= 5 and s.success_rate < 0.3
                ]
                # Удаляем навыки с очень плохой статистикой
                for ws in _weak_skills:
                    self.structured_skills.remove(ws.name)
                    self._log(
                        f"[improve] Навык '{ws.name}' удалён "
                        f"(success_rate={ws.success_rate:.0%} после {ws.use_count} использований)"
                    )
                proposals = self.self_improvement.analyse_and_propose(max_proposals=3)
                if proposals:
                    self._log(
                        f"[improve] Сгенерировано {len(proposals)} предложений по улучшению."
                    )
                    # Автоприменяем только после Fitness Gate (Promote/Reject).
                    baseline_success_rate = (
                        self._success_rate(current_success=cycle.success)
                        if success_rate is None else success_rate
                    )
                    baseline_failures = (
                        self._consecutive_failures
                        if consecutive_failures is None else consecutive_failures
                    )
                    for p in proposals:
                        if p.priority <= 2:
                            approve, fit, reason = self._fitness_gate(
                                area=p.area,
                                proposal=p,
                                cycle=cycle,
                                success_rate=baseline_success_rate,
                                consecutive_failures=baseline_failures,
                            )
                            if approve:
                                if not self._approve_self_modification(
                                    'strategy_apply',
                                    f'Применить стратегию [{p.area}]: '
                                    f'{getattr(p, "proposed_change", "")[:120]}',
                                ):
                                    self._log(
                                        f"[fitness_gate] BLOCKED [{p.area}] "
                                        f"применение не одобрено.",
                                        level='warning',
                                    )
                                    continue
                                applied = self.self_improvement.apply(p)
                                if applied:
                                    self._promote_champion(
                                        area=p.area,
                                        proposal=p,
                                        fitness=fit,
                                        cycle_id=cycle.cycle_id,
                                    )
                                    self._log(
                                        f"[fitness_gate] PROMOTE [{p.area}] "
                                        f"fitness={fit:.2f}"
                                    )
                                    if self.persistent_brain:
                                        self.persistent_brain.record_evolution(
                                            event="strategy_evolved",
                                            details=f"Область: {p.area}, "
                                                    f"fitness={fit:.2f}, "
                                                    f"изменение: {p.proposed_change[:100]}",
                                        )
                            else:
                                p.status = 'rejected'
                                p.result = f"Rejected by fitness gate: {reason}"
                                self._log(
                                    f"[fitness_gate] REJECT [{p.area}] "
                                    f"fitness={fit:.2f}: {reason}"
                                )

            # Оптимизируем стратегию раз в 5 циклов, или при 3+ неудачах подряд
            _should_optimise = (
                self._cycle_count % self.config.self_improve_interval == 0
                or (consecutive_failures is not None and consecutive_failures >= 3)
                or (consecutive_failures is None and self._consecutive_failures >= 3)
            )
            if cycle.evaluation and _should_optimise:
                actual_success_rate = (
                    self._success_rate(current_success=cycle.success)
                    if success_rate is None else success_rate
                )
                actual_failures = (
                    self._consecutive_failures
                    if consecutive_failures is None else consecutive_failures
                )
                strategy = self.self_improvement.optimise_strategy(
                    area="autonomous_loop",
                    performance_data={
                        "success_rate": actual_success_rate,
                        "cycle_id": cycle.cycle_id,
                        "errors": len(cycle.errors),
                        "consecutive_failures": actual_failures,
                        "real_work_done": self._has_real_work(cycle),
                        "cycle_success": cycle.success,
                        # FTracker + Skills контекст для оптимизатора
                        "failure_categories": self.failure_tracker.goal_failure_summary(
                            str(self._goal or '')
                        ),
                        "structured_skills_count": len(self.structured_skills.all_skills()),
                        "replan_count": self._replan_count,
                    },
                )
                # Применяем сразу (optimise_strategy только создаёт proposal, но не применяет без auto_apply)
                _pending_loop = self.self_improvement.pending_proposals(
                    area='autonomous_loop',
                )
                if _pending_loop:
                    candidate = _pending_loop[-1]
                    approve, fit, reason = self._fitness_gate(
                        area='autonomous_loop',
                        proposal=candidate,
                        cycle=cycle,
                        success_rate=actual_success_rate,
                        consecutive_failures=actual_failures,
                    )
                    if approve:
                        if not self._approve_self_modification(
                            'strategy_apply',
                            f'Применить стратегию [autonomous_loop]: '
                            f'{getattr(candidate, "proposed_change", "")[:120]}',
                        ):
                            self._log(
                                "[fitness_gate] BLOCKED [autonomous_loop] "
                                "применение не одобрено.",
                                level='warning',
                            )
                        else:
                            applied = self.self_improvement.apply(candidate)
                            if applied:
                                self._promote_champion(
                                    area='autonomous_loop',
                                    proposal=candidate,
                                    fitness=fit,
                                    cycle_id=cycle.cycle_id,
                                )
                                self._log(
                                    f"[fitness_gate] PROMOTE [autonomous_loop] "
                                    f"fitness={fit:.2f}"
                                )
                            else:
                                self._log(
                                    "[fitness_gate] APPLY failed after gate approval "
                                    "(autonomous_loop)."
                            )
                    else:
                        candidate.status = 'rejected'
                        candidate.result = f"Rejected by fitness gate: {reason}"
                        self._log(
                            f"[fitness_gate] REJECT [autonomous_loop] "
                            f"fitness={fit:.2f}: {reason}"
                        )
                self._log("[improve] Стратегия оптимизирована.")
                if strategy and self.persistent_brain:
                    self.persistent_brain.record_evolution(
                        event="strategy_optimised",
                        details=f"Цикл #{cycle.cycle_id}, "
                                f"success_rate={actual_success_rate:.0%}, "
                                f"ошибок={len(cycle.errors)}. "
                                f"Новая стратегия: {str(strategy)[:120]}",
                    )
                return strategy

            # Слой 40: Temporal — фиксируем завершение цикла
            if self.temporal:
                try:
                    is_ok = len(cycle.errors) == 0 and self._has_real_work(cycle)
                    self.temporal.add_event(
                        description=f"Цикл #{self._cycle_count} завершён. Успех: {is_ok}",
                        tags=['cycle_end'],
                    )
                except Exception as _e:
                    self._log_exc("improve/temporal", _e)

            # Слой 35: CapabilityDiscovery — сканируем возможности раз в 100 циклов
            if self.capability_discovery and self._cycle_count % self.config.capability_scan_interval == 0:
                try:
                    found = self.capability_discovery.scan_installed()
                    self._log(
                        f"[improve/capability_discovery] Найдено {len(found)} возможностей."
                    )
                except Exception as _e:
                    self._log_exc("improve/capability_discovery", _e)

            # Слой 7: SoftwareDev — линтинг собственного кода раз в 30 циклов
            if self.software_dev and self._cycle_count % self.config.lint_interval == 0:
                try:
                    lint = self.software_dev.run_linter('.')
                    status = 'OK' if lint.success else lint.output[:120]
                    self._log(f"[improve/software_dev] Линтер: {status}")
                except Exception as _e:
                    self._log_exc("improve/software_dev", _e)

            # ── ДИНАМИЧЕСКОЕ СОЗДАНИЕ МОДУЛЕЙ ────────────────────────────────
            # CapabilityDiscovery нашёл gaps? — передаём в ModuleBuilder
            if self.module_builder and self.capability_discovery:
                try:
                    gaps = None
                    if hasattr(self.capability_discovery, 'find_gaps'):
                        gaps = self.capability_discovery.find_gaps()
                    elif hasattr(self.capability_discovery, 'get_missing_capabilities'):
                        gaps = self.capability_discovery.get_missing_capabilities()
                    if gaps:
                        _gap_str = str(gaps[:3]) if isinstance(gaps, list) else str(gaps)
                        self._log(f"[improve/evolution] Выявлены capability gaps: {_gap_str}")
                        _gap_desc = _gap_str[:300]
                        # Добавляем цель создать модуль (требует approval)
                        if self.goal_generator and self.goal_manager:
                            try:
                                if self._approve_self_modification(
                                    'module_from_gap',
                                    f'Создать модуль из capability gap: {_gap_desc[:100]}',
                                ):
                                    self.goal_generator.propose_from_gap(_gap_desc)
                            except Exception as _ge:
                                self._log_exc("improve/goal_gen", _ge)
                except Exception as _e:
                    self._log_exc("improve/evolution", _e)

            # Сканирование рабочей папки на новые файлы (каждые 5 циклов)
            # Требует approval — пользователь должен подтвердить авто-цели из файлов
            if self.goal_generator and self._cycle_count % self.config.scan_workdir_interval == 0:
                try:
                    _wd = os.path.dirname(os.path.abspath(__file__))
                    _wd = os.path.dirname(_wd)
                    new_file_goals = self.goal_generator.scan_working_dir(_wd)
                    if new_file_goals:
                        if self.human_approval:
                            _desc = '; '.join(g[:80] for g in new_file_goals[:3])
                            _approved = self.human_approval.request_approval(
                                'auto_goal_from_file',
                                f"scan_workdir нашёл {len(new_file_goals)} файл(ов).\n{_desc}",
                            )
                            if not _approved:
                                self._log(
                                    '[improve/scan_workdir] Авто-цели из файлов ОТКЛОНЕНЫ.',
                                    level='warning',
                                )
                                # Отменяем уже добавленные цели
                                if self.goal_manager:
                                    for _fg in new_file_goals:
                                        try:
                                            self.goal_manager.remove_goal_by_description(_fg)
                                        except Exception as _e:
                                            self._log_exc("improve/remove_rejected_goal", _e)
                            else:
                                self._log(
                                    f"[improve/scan_workdir] {len(new_file_goals)} "
                                    f"авто-целей из файлов одобрены."
                                )
                        else:
                            self._log(
                                f"[improve/scan_workdir] {len(new_file_goals)} "
                                f"файлов → цели (human_approval не подключён!).",
                                level='warning',
                            )
                except Exception as _e:
                    self._log_exc("improve/scan_workdir", _e)

            # Автогенерация целей из рефлексии (автономно, без approval)
            if self.goal_generator and self.reflection and self._cycle_count % self.config.goal_from_reflection_interval == 0:
                try:
                    self.goal_generator.generate_from_reflection()
                except Exception as _e:
                    self._log_exc("improve/goal_gen", _e)

            # Автогенерация целей из инвентаря способностей (автономно, без approval)
            if self.goal_generator and self.identity and self._cycle_count % self.config.goal_from_inventory_interval == 0:
                try:
                    self.goal_generator.generate_from_inventory()
                except Exception as _e:
                    self._log_exc("improve/goal_gen/inventory", _e)

            return None
        except Exception as e:
            self._record_error("improve", e)
            return None

    # ── Вспомогательные ────────────────────────────────────────────────────────

    def _record_error(self, phase: str, exc: Exception):
        """Записывает ошибку в цикл и лог с traceback."""
        msg = f"{phase}: {type(exc).__name__}: {exc}"
        if self._current_cycle:
            self._current_cycle.errors.append(msg)
        self._log_exc(phase, exc)

    # ── История и статистика ──────────────────────────────────────────────────

    def get_history(self) -> list:
        return [c.to_dict() for c in self._history]

    def get_current_cycle(self) -> dict | None:
        return self._current_cycle.to_dict() if self._current_cycle else None

    @property
    def cycle_count(self) -> int:
        return self._cycle_count

    @property
    def acquisition_status(self) -> dict:
        """Статистика последнего запуска acquisition-пайплайна."""
        return dict(self._last_acquisition_stats)

    # ── Logging с отложенным выводом (для синхронизации с Telegram) ─────────

    def _post_achievement_to_channel(self, _cycle: 'LoopCycle'):
        """Публикует пост о достижении в Telegram-канал."""

        # Собираем что было сделано
        goal_text = str(self._goal)[:120] if self._goal else 'автономная задача'

        # Ищем созданные файлы в outputs/
        _wd = (
            getattr(self, '_working_dir', None)
            or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        _outputs_dir = os.path.join(_wd, 'outputs')
        _new_files = []
        try:
            if os.path.exists(_outputs_dir):
                _now = time.time()
                for _f in os.listdir(_outputs_dir):
                    _fp = os.path.join(_outputs_dir, _f)
                    if os.path.isfile(_fp) and (_now - os.path.getmtime(_fp)) < 120:
                        _new_files.append(_f)
        except Exception as _e:
            self._log_exc("observe", _e)

        # Статистика
        total_cycles = self._cycle_count
        success_rate = int(self._success_rate(current_success=True) * 100)

        # Формируем пост
        _file_line = ''
        if _new_files:
            _file_line = '\n📁 <b>Создано:</b> ' + ', '.join(f'<code>{f}</code>' for f in _new_files[:5])

        _emoji_map = [
            (1,  '🌱'), (5,  '⚡'), (10, '🔥'),
            (25, '🚀'), (50, '💎'), (100, '🏆'),
        ]
        _emoji = '✅'
        for threshold, em in _emoji_map:
            if self._channel_success_count >= threshold:
                _emoji = em

        post = (
            f"{_emoji} <b>Агент выполнил задачу #{self._channel_success_count}</b>\n\n"
            f"🎯 <b>Цель:</b> {goal_text}"
            f"{_file_line}\n\n"
            f"📊 Цикл #{total_cycles} | Успешность: {success_rate}%\n\n"
            f"🤖 <i>Autonomous AI Agent — 48 tools | GPT-5.1 + Claude + Qwen</i>\n"
            f"💼 <a href='https://www.upwork.com/services/product/2038909844504654059'>Заказать на Upwork</a>"
        )

        if not self.telegram_bot:
            return
        self.telegram_bot.send(
            self.telegram_channel_id,
            post,
            parse_mode='HTML',
        )
        self._log(f"[channel] Пост #{self._channel_success_count} опубликован в канал")

    def _log(self, message: str, level: str = 'info'):
        """Логирует сообщение. Если включена буферизация — добавляет в очередь."""
        if self._defer_console_output:
            # Буферизуем сообщение (не выводим сейчас)
            self._deferred_logs.append((level, message))
        else:
            # Выводим сразу
            if self.monitoring:
                from monitoring.monitoring import LogLevel as _LL
                _lvl = {'debug': _LL.DEBUG, 'warning': _LL.WARNING, 'error': _LL.ERROR, 'critical': _LL.CRITICAL}.get(level, _LL.INFO)
                self.monitoring.log(message, level=_lvl)
            else:
                print(f"[AutonomousLoop] {message}")

    def _log_exc(self, context: str, exc: Exception):
        """Логирует исключение с полным traceback."""
        tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
        tb_text = ''.join(tb[-3:])  # last 3 frames — compact but enough
        self._log(f"[{context}] {type(exc).__name__}: {exc}\n{tb_text}", level='error')

    def _start_deferred_output(self):
        """Начинает буферизацию логов (для фазы PLAN-SIMULATE)."""
        self._defer_console_output = True
        self._deferred_logs = []

    def _flush_deferred_output(self):
        """Выводит все буферизованные логи на консоль."""
        if not self._deferred_logs:
            return
        for _level, msg in self._deferred_logs:
            if self.monitoring:
                from monitoring.monitoring import LogLevel as _LL
                _lvl = {'debug': _LL.DEBUG, 'warning': _LL.WARNING, 'error': _LL.ERROR, 'critical': _LL.CRITICAL}.get(_level, _LL.INFO)
                self.monitoring.log(msg, level=_lvl)
            else:
                print(f"[AutonomousLoop] {msg}")
        self._deferred_logs = []

    def _discard_deferred_output(self):
        """Отбрасывает все буферизованные логи (используется при BLOCKED плане)."""
        self._deferred_logs = []

    def _end_deferred_output(self):
        """Завершает буферизацию и отключает флаг."""
        self._defer_console_output = False
