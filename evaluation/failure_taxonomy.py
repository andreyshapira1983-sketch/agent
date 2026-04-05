# Failure Taxonomy — нормализованная классификация провалов — Слой 25+
# Архитектура автономного AI-агента
# Категоризация ошибок, стандартные recovery-маршруты, лимиты повторов.

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class FailureCategory(Enum):
    TOOL_ERROR          = 'tool_error'
    PERMISSION_ERROR    = 'permission_error'
    TIMEOUT             = 'timeout'
    INVALID_PLAN        = 'invalid_plan'
    VERIFICATION_FAILED = 'verification_failed'
    HALLUCINATED_SUCCESS = 'hallucinated_success'
    MISSING_INFO        = 'missing_info'
    DEPENDENCY_MISSING  = 'dependency_missing'
    ENVIRONMENT_MISMATCH = 'environment_mismatch'
    RATE_LIMIT          = 'rate_limit'
    NETWORK_ERROR       = 'network_error'
    UNKNOWN             = 'unknown'


@dataclass
class RecoveryPolicy:
    """Стандартный маршрут восстановления для категории ошибки."""
    max_retries: int = 2
    requires_replan: bool = False
    backoff_seconds: float = 0.0
    recovery_steps: list[str] = field(default_factory=list)
    escalate_after: int = 3          # после N провалов — эскалация / остановка
    cooldown_seconds: float = 0.0    # пауза перед повтором


# Маршруты восстановления по категориям
RECOVERY_POLICIES: dict[FailureCategory, RecoveryPolicy] = {
    FailureCategory.TOOL_ERROR: RecoveryPolicy(
        max_retries=2,
        requires_replan=False,
        recovery_steps=['retry_same_tool', 'try_alternative_tool'],
        escalate_after=3,
    ),
    FailureCategory.PERMISSION_ERROR: RecoveryPolicy(
        max_retries=1,
        requires_replan=True,
        recovery_steps=['check_credentials', 'skip_step'],
        escalate_after=2,
    ),
    FailureCategory.TIMEOUT: RecoveryPolicy(
        max_retries=2,
        requires_replan=False,
        backoff_seconds=5.0,
        recovery_steps=['retry_with_backoff', 'reduce_scope'],
        escalate_after=3,
    ),
    FailureCategory.INVALID_PLAN: RecoveryPolicy(
        max_retries=0,
        requires_replan=True,
        recovery_steps=['replan_from_scratch'],
        escalate_after=2,
    ),
    FailureCategory.VERIFICATION_FAILED: RecoveryPolicy(
        max_retries=1,
        requires_replan=True,
        recovery_steps=['recheck_output', 'replan_with_constraints'],
        escalate_after=3,
    ),
    FailureCategory.HALLUCINATED_SUCCESS: RecoveryPolicy(
        max_retries=0,
        requires_replan=True,
        recovery_steps=['invalidate_result', 'replan_with_strict_verify'],
        escalate_after=2,
    ),
    FailureCategory.MISSING_INFO: RecoveryPolicy(
        max_retries=1,
        requires_replan=True,
        recovery_steps=['search_for_info', 'ask_user'],
        escalate_after=3,
    ),
    FailureCategory.DEPENDENCY_MISSING: RecoveryPolicy(
        max_retries=1,
        requires_replan=False,
        recovery_steps=['install_dependency', 'skip_step'],
        escalate_after=2,
    ),
    FailureCategory.ENVIRONMENT_MISMATCH: RecoveryPolicy(
        max_retries=1,
        requires_replan=True,
        recovery_steps=['adapt_to_environment', 'fallback_offline'],
        escalate_after=2,
    ),
    FailureCategory.RATE_LIMIT: RecoveryPolicy(
        max_retries=2,
        requires_replan=False,
        backoff_seconds=30.0,
        cooldown_seconds=60.0,
        recovery_steps=['wait_and_retry', 'switch_backend'],
        escalate_after=4,
    ),
    FailureCategory.NETWORK_ERROR: RecoveryPolicy(
        max_retries=3,
        requires_replan=False,
        backoff_seconds=10.0,
        recovery_steps=['retry_with_backoff', 'fallback_offline'],
        escalate_after=4,
    ),
    FailureCategory.UNKNOWN: RecoveryPolicy(
        max_retries=1,
        requires_replan=True,
        recovery_steps=['log_and_replan'],
        escalate_after=2,
    ),
}

# ── Сигнатуры для автоматической классификации ───────────────────────────────

_SIGNATURES: list[tuple[list[str], FailureCategory]] = [
    # permission
    (['permission denied', 'access denied', 'forbidden', '403',
      'unauthorized', '401', 'not authorized', 'credentials'],
     FailureCategory.PERMISSION_ERROR),
    # timeout
    (['timeout', 'timed out', 'deadline exceeded', 'took too long'],
     FailureCategory.TIMEOUT),
    # rate limit
    (['rate limit', 'too many requests', '429', 'quota exceeded',
      'insufficient_quota', 'throttl'],
     FailureCategory.RATE_LIMIT),
    # network
    (['connection refused', 'connection reset', 'network unreachable',
      'dns resolution', 'ssl error', 'connection error', 'socket'],
     FailureCategory.NETWORK_ERROR),
    # dependency
    (['modulenotfounderror', 'no module named', 'import error',
      'package not found', 'command not found', 'not installed'],
     FailureCategory.DEPENDENCY_MISSING),
    # environment
    (['os error', 'platform', 'windows', 'linux', 'wsl',
      'environment variable', 'no such file', 'filenotfounderror'],
     FailureCategory.ENVIRONMENT_MISMATCH),
    # hallucinated success
    (['hallucinated', 'false_status', 'actions_found=0',
      'FAILED_FALSE_STATUS', 'claimed success but'],
     FailureCategory.HALLUCINATED_SUCCESS),
    # verification
    (['verification_failed', 'STEP_EVAL', 'verify:', 'check failed',
      'assertion', 'expected but got', 'FAILED_WRONG_TOOL',
      'FAILED_IRRELEVANT', 'FAILED_SUBSTITUTION'],
     FailureCategory.VERIFICATION_FAILED),
    # invalid plan
    (['plan blocked', 'sandbox', 'BLOCKED', 'unsafe', 'policy violation',
      'no actionable', 'non_actionable', 'empty plan'],
     FailureCategory.INVALID_PLAN),
    # missing info
    (['missing_info', 'not enough context', 'unknown target',
      'need more information', 'unresolved placeholder'],
     FailureCategory.MISSING_INFO),
    # tool error (generic — last)
    (['tool error', 'tool failed', 'execution failed', 'traceback',
      'exception', 'error:', 'stderr'],
     FailureCategory.TOOL_ERROR),
]


@dataclass
class ClassifiedFailure:
    """Результат классификации одного провала."""
    category: FailureCategory
    raw_error: str
    signature_matched: str           # какая сигнатура сработала
    recovery: RecoveryPolicy
    timestamp: float = field(default_factory=time.time)
    context_goal: str = ''


class FailureTracker:
    """
    Отслеживает историю провалов, считает повторы по категориям,
    принимает решения о recovery / replan / эскалации.

    Используется в AutonomousLoop для замены свободно-текстового анализа ошибок
    на детерминированный маршрут.
    """

    def __init__(self, history_size: int = 500):
        self._history: list[ClassifiedFailure] = []
        self._history_size = history_size
        # category → running count (сбрасывается при успехе)
        self._consecutive: dict[FailureCategory, int] = {}
        # goal_hash → {category → count} — провалы по конкретной цели
        self._per_goal: dict[str, dict[FailureCategory, int]] = {}
        # error_hash → consecutive count (для детекции одинаковых ошибок)
        self._error_hash_consecutive: dict[str, int] = {}
        self._last_error_hash: str = ''

    # ── Классификация ─────────────────────────────────────────────────────────

    @staticmethod
    def classify(error_msg: str) -> tuple[FailureCategory, str]:
        """Классифицирует текст ошибки в категорию. Возвращает (category, matched_sig)."""
        error_lower = error_msg.lower()
        for signatures, category in _SIGNATURES:
            for sig in signatures:
                if sig.lower() in error_lower:
                    return category, sig
        return FailureCategory.UNKNOWN, ''

    def record(self, error_msg: str, goal: str = '') -> ClassifiedFailure:
        """Классифицирует ошибку, записывает в историю, возвращает результат."""
        category, matched = self.classify(error_msg)
        recovery = RECOVERY_POLICIES.get(category, RECOVERY_POLICIES[FailureCategory.UNKNOWN])

        cf = ClassifiedFailure(
            category=category,
            raw_error=error_msg[:500],
            signature_matched=matched,
            recovery=recovery,
            context_goal=goal[:200],
        )
        self._history.append(cf)
        if len(self._history) > self._history_size:
            self._history = self._history[-self._history_size:]

        # Обновляем consecutive count
        self._consecutive[category] = self._consecutive.get(category, 0) + 1

        # Error-hash dedup: считаем подряд одинаковые ошибки
        err_hash = self._compute_error_hash(error_msg)
        if err_hash == self._last_error_hash:
            self._error_hash_consecutive[err_hash] = (
                self._error_hash_consecutive.get(err_hash, 1) + 1
            )
        else:
            self._last_error_hash = err_hash
            self._error_hash_consecutive[err_hash] = 1

        # Per-goal tracking
        goal_key = goal[:100].strip().lower()
        if goal_key:
            if goal_key not in self._per_goal:
                self._per_goal[goal_key] = {}
            self._per_goal[goal_key][category] = (
                self._per_goal[goal_key].get(category, 0) + 1
            )

        return cf

    def record_success(self):
        """Сбрасывает consecutive counters при успехе."""
        self._consecutive.clear()
        self._error_hash_consecutive.clear()
        self._last_error_hash = ''

    # ── Запросы ───────────────────────────────────────────────────────────────

    def should_replan(self, category: FailureCategory) -> bool:
        """Нужен ли replan для данной категории?"""
        policy = RECOVERY_POLICIES.get(category, RECOVERY_POLICIES[FailureCategory.UNKNOWN])
        consecutive = self._consecutive.get(category, 0)
        return policy.requires_replan or consecutive > policy.max_retries

    def should_escalate(self, category: FailureCategory) -> bool:
        """Достигнут ли лимит для эскалации (остановка / запрос к пользователю)?"""
        policy = RECOVERY_POLICIES.get(category, RECOVERY_POLICIES[FailureCategory.UNKNOWN])
        consecutive = self._consecutive.get(category, 0)
        return consecutive >= policy.escalate_after

    def get_recovery_steps(self, category: FailureCategory) -> list[str]:
        """Стандартные шаги recovery для категории."""
        return RECOVERY_POLICIES.get(
            category, RECOVERY_POLICIES[FailureCategory.UNKNOWN]
        ).recovery_steps

    def get_backoff(self, category: FailureCategory) -> float:
        """Время ожидания перед повтором (секунды)."""
        return RECOVERY_POLICIES.get(
            category, RECOVERY_POLICIES[FailureCategory.UNKNOWN]
        ).backoff_seconds

    def goal_failure_summary(self, goal: str) -> dict[str, int]:
        """Сколько провалов по каждой категории для данной цели."""
        goal_key = goal[:100].strip().lower()
        raw = self._per_goal.get(goal_key, {})
        return {cat.value: count for cat, count in raw.items()}

    def recent_categories(self, n: int = 10) -> list[FailureCategory]:
        """Последние N категорий провалов."""
        return [cf.category for cf in self._history[-n:]]

    def dominant_failure(self, n: int = 10) -> Optional[FailureCategory]:
        """Наиболее частая категория провалов за последние N записей."""
        cats = self.recent_categories(n)
        if not cats:
            return None
        from collections import Counter
        most_common = Counter(cats).most_common(1)
        return most_common[0][0] if most_common else None

    def consecutive_count(self, category: FailureCategory) -> int:
        """Public accessor: количество подряд ошибок данной категории."""
        return self._consecutive.get(category, 0)

    def is_repeated_error(self, threshold: int = 3) -> bool:
        """Одна и та же ошибка повторилась >= threshold раз подряд."""
        if not self._last_error_hash:
            return False
        return self._error_hash_consecutive.get(self._last_error_hash, 0) >= threshold

    def repeated_error_count(self) -> int:
        """Сколько раз подряд повторилась последняя ошибка."""
        if not self._last_error_hash:
            return 0
        return self._error_hash_consecutive.get(self._last_error_hash, 0)

    @staticmethod
    def _compute_error_hash(error_msg: str) -> str:
        """Нормализует и хэширует ошибку: числа, пути, id → placeholder."""
        import hashlib
        normalized = error_msg.strip().lower()
        # Убираем переменные части: числа, UUID, пути
        normalized = re.sub(r'0x[0-9a-f]+', '<addr>', normalized)
        normalized = re.sub(r'[0-9a-f]{8,}', '<id>', normalized)
        normalized = re.sub(r'\d+', '<N>', normalized)
        normalized = re.sub(r'[/\\][^\s]+', '<path>', normalized)
        return hashlib.md5(normalized.encode()).hexdigest()[:12]

    def reset_goal_category(self, goal: str, category: FailureCategory) -> None:
        """Public accessor: сброс счётчика категории для цели."""
        goal_key = goal[:100].strip().lower()
        if goal_key in self._per_goal:
            self._per_goal[goal_key].pop(category, None)

    def to_prompt_hint(self, goal: str = '', n: int = 5) -> str:
        """Генерирует hint для промпта LLM на основе истории категоризированных провалов."""
        recent = self._history[-n:]
        if not recent:
            return ''
        lines = []
        for cf in recent:
            lines.append(f"- [{cf.category.value}] {cf.raw_error[:100]}")
        recovery_hint = ''
        dominant = self.dominant_failure(n)
        if dominant:
            steps = self.get_recovery_steps(dominant)
            if steps:
                recovery_hint = (
                    f"\nДоминирующий тип ошибки: {dominant.value}. "
                    f"Рекомендуемые шаги: {', '.join(steps)}."
                )
        return (
            f"[FAILURE HISTORY] Последние {len(recent)} провалов:\n"
            + '\n'.join(lines)
            + recovery_hint
        )

    def summary(self) -> dict:
        from collections import Counter
        cats = Counter(cf.category.value for cf in self._history)
        return {
            'total_failures': len(self._history),
            'by_category': dict(cats),
            'consecutive': {
                cat.value: count
                for cat, count in self._consecutive.items()
                if count > 0
            },
        }

    # ── Persistence ───────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Сериализует состояние для PersistentBrain."""
        return {
            'history': [
                {
                    'category': cf.category.value,
                    'raw_error': cf.raw_error[:300],
                    'signature_matched': cf.signature_matched,
                    'timestamp': cf.timestamp,
                    'context_goal': cf.context_goal[:200],
                }
                for cf in self._history[-200:]  # храним последние 200
            ],
            'per_goal': {
                g: {cat.value: cnt for cat, cnt in cats.items()}
                for g, cats in self._per_goal.items()
            },
        }

    def load_from_dict(self, data: dict) -> None:
        """Восстанавливает состояние из сохранённых данных."""
        if not isinstance(data, dict):
            return
        for item in data.get('history', []):
            try:
                cat = FailureCategory(item['category'])
            except (ValueError, KeyError):
                cat = FailureCategory.UNKNOWN
            policy = RECOVERY_POLICIES.get(cat, RECOVERY_POLICIES[FailureCategory.UNKNOWN])
            cf = ClassifiedFailure(
                category=cat,
                raw_error=item.get('raw_error', ''),
                signature_matched=item.get('signature_matched', ''),
                recovery=policy,
                timestamp=item.get('timestamp', 0.0),
                context_goal=item.get('context_goal', ''),
            )
            self._history.append(cf)
        # Обрезаем до лимита
        if len(self._history) > self._history_size:
            self._history = self._history[-self._history_size:]
        # Восстанавливаем per_goal
        for g, cats in data.get('per_goal', {}).items():
            restored: dict[FailureCategory, int] = {}
            for cat_str, cnt in cats.items():
                try:
                    restored[FailureCategory(cat_str)] = int(cnt)
                except (ValueError, TypeError):
                    pass
            if restored:
                self._per_goal[g] = restored
