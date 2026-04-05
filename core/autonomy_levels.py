# Autonomy Levels — режимы инициативы и автономии агента-партнёра
# Архитектура: Autonomy Layer → configurable initiative/autonomy levels
# Меморандум (Часть 22): "опасные действия требуют подтверждения — это не недоверие"
# Меморандум (Часть 4.7): "Ночью агент работает фоном только по правилам,
#   которые я заранее одобрил"
#
# Уровни автономии:
#   OBSERVER    — только наблюдение и отчёт, ничего не делает
#   ASSISTANT   — делает по запросу, инициативу не проявляет
#   PARTNER     — проявляет инициативу в безопасных рамках (default)
#   AUTONOMOUS  — полная автономия, human-in-the-loop для critical
#   NIGHT       — ночной режим: фоновая работа по заранее одобренным правилам

from __future__ import annotations

import time
import threading
from enum import Enum
from dataclasses import dataclass, field


class AutonomyLevel(Enum):
    """Уровень автономии агента."""
    OBSERVER   = 'observer'     # только смотрю и отчитываюсь
    ASSISTANT  = 'assistant'    # делаю по запросу
    PARTNER    = 'partner'      # инициатива в безопасных рамках
    AUTONOMOUS = 'autonomous'   # полная автономия (critical → human)
    NIGHT      = 'night'        # ночной фоновый режим


class InitiativeMode(Enum):
    """Режим проявления инициативы."""
    SILENT    = 'silent'        # не говорить пока не спросят
    REACTIVE  = 'reactive'     # отвечать на события, но не предлагать
    PROACTIVE = 'proactive'    # предлагать идеи и follow-up
    ACTIVE    = 'active'       # активно искать задачи и действовать


# ── Конфигурация уровней ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class AutonomyConfig:
    """Конфигурация одного уровня автономии."""
    level: AutonomyLevel
    initiative: InitiativeMode
    can_execute_safe: bool         # может ли выполнять безопасные действия
    can_execute_risky: bool        # может ли выполнять рискованные действия
    can_send_messages: bool        # может ли отправлять сообщения пользователю
    can_start_tasks: bool          # может ли начинать новые задачи сам
    max_actions_per_hour: int      # лимит действий в час
    requires_approval_for: set[str]  # типы действий, требующих approval
    description: str               # человекочитаемое описание


# Предустановленные конфигурации
_AUTONOMY_CONFIGS: dict[AutonomyLevel, AutonomyConfig] = {
    AutonomyLevel.OBSERVER: AutonomyConfig(
        level=AutonomyLevel.OBSERVER,
        initiative=InitiativeMode.SILENT,
        can_execute_safe=False,
        can_execute_risky=False,
        can_send_messages=False,
        can_start_tasks=False,
        max_actions_per_hour=0,
        requires_approval_for={'read', 'query', 'observe', 'write_safe',
                                'write', 'execute_safe', 'execute',
                                'send_message', 'api_call', 'deploy',
                                'delete', 'financial'},
        description="Только наблюдение. Агент не действует, только собирает информацию.",
    ),
    AutonomyLevel.ASSISTANT: AutonomyConfig(
        level=AutonomyLevel.ASSISTANT,
        initiative=InitiativeMode.REACTIVE,
        can_execute_safe=True,
        can_execute_risky=False,
        can_send_messages=True,
        can_start_tasks=False,
        max_actions_per_hour=30,
        requires_approval_for={'execute', 'deploy', 'delete', 'financial',
                                'api_call'},
        description="Работает по запросу. Инициативу не проявляет.",
    ),
    AutonomyLevel.PARTNER: AutonomyConfig(
        level=AutonomyLevel.PARTNER,
        initiative=InitiativeMode.PROACTIVE,
        can_execute_safe=True,
        can_execute_risky=True,
        can_send_messages=True,
        can_start_tasks=True,
        max_actions_per_hour=60,
        requires_approval_for={'deploy', 'delete', 'financial'},
        description="Партнёр: проявляет инициативу, опасные действия требуют подтверждения.",
    ),
    AutonomyLevel.AUTONOMOUS: AutonomyConfig(
        level=AutonomyLevel.AUTONOMOUS,
        initiative=InitiativeMode.ACTIVE,
        can_execute_safe=True,
        can_execute_risky=True,
        can_send_messages=True,
        can_start_tasks=True,
        max_actions_per_hour=120,
        requires_approval_for={'financial'},  # только финансы требуют approval
        description="Полная автономия. Human-in-the-loop только для финансов.",
    ),
    AutonomyLevel.NIGHT: AutonomyConfig(
        level=AutonomyLevel.NIGHT,
        initiative=InitiativeMode.SILENT,
        can_execute_safe=True,
        can_execute_risky=False,
        can_send_messages=False,       # ночью не тревожить
        can_start_tasks=True,          # но может работать фоном
        max_actions_per_hour=20,
        requires_approval_for={'execute', 'deploy', 'delete', 'financial',
                                'send_message', 'api_call'},
        description="Ночной режим: фоновая работа без уведомлений, только безопасные действия.",
    ),
}


# ── Autonomy Controller ──────────────────────────────────────────────────────

class AutonomyController:
    """
    Контроллер уровней автономии агента.

    Управляет:
        - текущим уровнем автономии
        - автоматическим переключением (расписание, DND)
        - per-user override (Tenant Isolation)
        - проверкой разрешений для действий
        - rate limiting

    Интегрируется с:
        - TrustModel      — trust score влияет на доступные уровни
        - GovernanceLayer  — deny policy применяется поверх autonomy
        - ProactiveMind    — initiative mode управляет "внутренним голосом"
        - HumanApproval    — autonomy определяет, когда спрашивать

    KPI партнёра:
        - насколько уместно проявляет инициативу → initiative modes
        - насколько предсказуем в долгой работе → configurable levels
    """

    def __init__(self, default_level: AutonomyLevel = AutonomyLevel.PARTNER):
        self._global_level: AutonomyLevel = default_level
        self._user_overrides: dict[str, AutonomyLevel] = {}  # user_id → override
        self._schedule: list[dict] = []  # расписание авто-переключения
        self._action_counts: dict[str, list[float]] = {}  # rate limiting
        self._lock = threading.Lock()
        self._change_listeners: list = []

    # ── Level management ──────────────────────────────────────────────────

    @property
    def level(self) -> AutonomyLevel:
        return self._global_level

    @property
    def config(self) -> AutonomyConfig:
        return _AUTONOMY_CONFIGS[self._global_level]

    def set_level(self, level: AutonomyLevel, reason: str = ''):
        """Устанавливает глобальный уровень автономии."""
        with self._lock:
            old = self._global_level
            self._global_level = level
            for listener in self._change_listeners:
                try:
                    listener(old, level, reason)
                except Exception:
                    pass

    def get_level_for_user(self, user_id: str) -> AutonomyLevel:
        """Возвращает уровень автономии для конкретного пользователя."""
        return self._user_overrides.get(user_id, self._global_level)

    def set_user_override(self, user_id: str, level: AutonomyLevel):
        """Персональный override уровня для пользователя."""
        with self._lock:
            self._user_overrides[user_id] = level

    def clear_user_override(self, user_id: str):
        with self._lock:
            self._user_overrides.pop(user_id, None)

    def get_config_for_user(self, user_id: str) -> AutonomyConfig:
        """Возвращает конфигурацию автономии для пользователя."""
        level = self.get_level_for_user(user_id)
        return _AUTONOMY_CONFIGS[level]

    # ── Permission checks ─────────────────────────────────────────────────

    def can_perform(self, action_type: str, user_id: str = '') -> bool:
        """Проверяет, может ли агент выполнить действие при текущем уровне."""
        config = self.get_config_for_user(user_id) if user_id else self.config
        if action_type in config.requires_approval_for:
            return False
        # Проверяем базовые capability
        if not config.can_execute_safe and action_type in {'write_safe', 'create_file', 'search'}:
            return False
        if not config.can_execute_risky and action_type in {'execute', 'deploy', 'delete'}:
            return False
        if not config.can_send_messages and action_type == 'send_message':
            return False
        if not config.can_start_tasks and action_type == 'start_task':
            return False
        return True

    def requires_approval(self, action_type: str, user_id: str = '') -> bool:
        """Проверяет, требуется ли approval для действия."""
        config = self.get_config_for_user(user_id) if user_id else self.config
        return action_type in config.requires_approval_for

    def get_initiative_mode(self, user_id: str = '') -> InitiativeMode:
        """Возвращает текущий режим инициативы."""
        config = self.get_config_for_user(user_id) if user_id else self.config
        return config.initiative

    # ── Rate limiting ─────────────────────────────────────────────────────

    def check_rate_limit(self, user_id: str = '') -> bool:
        """Проверяет, не превышен ли лимит действий в час."""
        config = self.get_config_for_user(user_id) if user_id else self.config
        if config.max_actions_per_hour == 0:
            return False  # observer — ноль действий

        key = user_id or '__global__'
        now = time.time()
        hour_ago = now - 3600

        with self._lock:
            if key not in self._action_counts:
                self._action_counts[key] = []

            # Очистка старых
            self._action_counts[key] = [t for t in self._action_counts[key] if t > hour_ago]

            if len(self._action_counts[key]) >= config.max_actions_per_hour:
                return False

            self._action_counts[key].append(now)
            return True

    # ── Schedule ──────────────────────────────────────────────────────────

    def add_schedule_rule(self, hour_start: int, hour_end: int,
                          level: AutonomyLevel):
        """Добавляет правило расписания: в указанные часы переключать уровень."""
        self._schedule.append({
            'hour_start': hour_start,
            'hour_end': hour_end,
            'level': level,
        })

    def check_schedule(self) -> AutonomyLevel | None:
        """Проверяет расписание и возвращает уровень, если совпадает."""
        import datetime
        current_hour = datetime.datetime.now().hour
        for rule in self._schedule:
            start = rule['hour_start']
            end = rule['hour_end']
            if start <= end:
                if start <= current_hour < end:
                    return rule['level']
            else:  # переход через полночь (напр. 23..7)
                if current_hour >= start or current_hour < end:
                    return rule['level']
        return None

    def apply_schedule(self):
        """Применяет расписание если есть совпадение."""
        scheduled = self.check_schedule()
        if scheduled and scheduled != self._global_level:
            self.set_level(scheduled, reason='schedule')

    # ── Listeners ─────────────────────────────────────────────────────────

    def add_change_listener(self, callback):
        """Добавляет listener для изменений уровня автономии."""
        self._change_listeners.append(callback)

    # ── Serialization ─────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            'global_level': self._global_level.value,
            'user_overrides': {
                uid: lvl.value for uid, lvl in self._user_overrides.items()
            },
            'schedule': self._schedule,
        }

    def load_from_dict(self, data: dict):
        self._global_level = AutonomyLevel(data.get('global_level', 'partner'))
        for uid, lvl in data.get('user_overrides', {}).items():
            try:
                self._user_overrides[uid] = AutonomyLevel(lvl)
            except ValueError:
                continue
        self._schedule = data.get('schedule', [])

    # ── Description ───────────────────────────────────────────────────────

    def describe(self, user_id: str = '') -> dict:
        """Человекочитаемое описание текущего состояния."""
        config = self.get_config_for_user(user_id) if user_id else self.config
        return {
            'level': config.level.value,
            'initiative': config.initiative.value,
            'description': config.description,
            'can_execute_safe': config.can_execute_safe,
            'can_execute_risky': config.can_execute_risky,
            'can_send_messages': config.can_send_messages,
            'can_start_tasks': config.can_start_tasks,
            'max_actions_per_hour': config.max_actions_per_hour,
            'requires_approval_for': sorted(config.requires_approval_for),
        }

    @staticmethod
    def available_levels() -> list[dict]:
        """Описание всех доступных уровней."""
        return [
            {
                'level': cfg.level.value,
                'initiative': cfg.initiative.value,
                'description': cfg.description,
            }
            for cfg in _AUTONOMY_CONFIGS.values()
        ]
