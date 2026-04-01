# Model Management Layer (управление моделями) — Слой 32
# Архитектура автономного AI-агента
# Выбор модели под задачу, переключение моделей,
# обновление, управление версиями, контроль стоимости.


import time
from enum import Enum


class ModelTier(Enum):
    LIGHT    = 'light'     # быстрые и дешёвые (haiku, gpt-4o-mini)
    STANDARD = 'standard'  # баланс (sonnet, gpt-4o)
    HEAVY    = 'heavy'     # мощные и дорогие (opus, o1)
    LOCAL    = 'local'     # локальные модели (ollama, llama.cpp)


class ModelProfile:
    """Профиль одной LLM-модели."""

    def __init__(self, model_id: str, name: str, tier: ModelTier,
                 provider: str, cost_per_1k_tokens: float = 0.0,
                 context_window: int = 8192, capabilities: list | None = None):
        self.model_id = model_id
        self.name = name
        self.tier = tier
        self.provider = provider
        self.cost_per_1k_tokens = cost_per_1k_tokens
        self.context_window = context_window
        self.capabilities = capabilities or []   # ['reasoning', 'coding', 'vision', ...]
        self.use_count = 0
        self.total_tokens = 0
        self.registered_at = time.time()

    def estimate_cost(self, tokens: int) -> float:
        return round(tokens / 1000 * self.cost_per_1k_tokens, 6)

    def record_use(self, tokens: int):
        self.use_count += 1
        self.total_tokens += tokens

    def to_dict(self):
        return {
            'model_id': self.model_id,
            'name': self.name,
            'tier': self.tier.value,
            'provider': self.provider,
            'cost_per_1k_tokens': self.cost_per_1k_tokens,
            'context_window': self.context_window,
            'capabilities': self.capabilities,
            'use_count': self.use_count,
            'total_tokens': self.total_tokens,
            'total_cost': self.estimate_cost(self.total_tokens),
        }


class ModelManager:
    """
    Model Management Layer — Слой 32.

    Функции:
        - реестр доступных моделей с профилями
        - выбор оптимальной модели под задачу и бюджет
        - переключение активной модели
        - управление версиями моделей
        - отслеживание использования и стоимости
        - fallback: переключение на более лёгкую модель при нехватке бюджета

    Используется:
        - Cognitive Core (Слой 3)         — выбирает модель для LLM-вызовов
        - Resource & Budget Control (Слой 26) — контроль стоимости
        - Monitoring (Слой 17)            — метрики использования
    """

    def __init__(self, budget_control=None, monitoring=None):
        self.budget_control = budget_control
        self.monitoring = monitoring

        self._models: dict[str, ModelProfile] = {}
        self._active_model_id: str | None = None
        self._task_routing: dict[str, str] = {}   # тип задачи → model_id

        self._register_defaults()

    # ── Регистрация моделей ───────────────────────────────────────────────────

    def register(self, model_id: str, name: str, tier: ModelTier,
                 provider: str, cost_per_1k_tokens: float = 0.0,
                 context_window: int = 8192, capabilities: list | None = None) -> ModelProfile:
        """Регистрирует модель в реестре."""
        profile = ModelProfile(model_id, name, tier, provider,
                               cost_per_1k_tokens, context_window, capabilities)
        self._models[model_id] = profile
        self._log(f"Модель зарегистрирована: '{name}' ({tier.value})")
        return profile

    def unregister(self, model_id: str):
        self._models.pop(model_id, None)
        if self._active_model_id == model_id:
            self._active_model_id = None

    # ── Активная модель ───────────────────────────────────────────────────────

    def set_active(self, model_id: str):
        """Устанавливает активную модель по умолчанию."""
        if model_id not in self._models:
            raise KeyError(f"Модель '{model_id}' не зарегистрирована")
        self._active_model_id = model_id
        self._log(f"Активная модель: '{self._models[model_id].name}'")

    def get_active(self) -> ModelProfile | None:
        if not self._active_model_id:
            return None
        return self._models.get(self._active_model_id)

    # ── Выбор модели под задачу ───────────────────────────────────────────────

    def select_for_task(self, task_type: str, expected_tokens: int = 1000,
                        required_capabilities: list | None = None) -> ModelProfile | None:
        """
        Выбирает оптимальную модель для задачи.

        Логика:
            1. Проверяет ручной маршрут (task_routing)
            2. Фильтрует по capabilities
            3. Проверяет бюджет
            4. Выбирает лучшую по tier (HEAVY → STANDARD → LIGHT)

        Args:
            task_type              — тип задачи ('coding', 'reasoning', 'analysis', ...)
            expected_tokens        — ожидаемое число токенов
            required_capabilities  — обязательные возможности модели

        Returns:
            ModelProfile или None если нет подходящей.
        """
        # Ручной маршрут
        if task_type in self._task_routing:
            model = self._models.get(self._task_routing[task_type])
            if model and self._matches_capabilities(model, required_capabilities):
                if self._is_affordable(model, expected_tokens):
                    return model
                self._log(
                    f"Маршрутная модель '{model.model_id}' не проходит бюджет — переключаюсь на fail-safe выбор"
                )

        candidates = list(self._models.values())

        # Фильтр по capabilities
        if required_capabilities:
            candidates = [
                m for m in candidates
                if all(cap in m.capabilities for cap in required_capabilities)
            ]

        # Фильтр по бюджету
        if self.budget_control:
            candidates = [
                m for m in candidates
                if self.budget_control.can_afford(
                    tokens=expected_tokens,
                    money=m.estimate_cost(expected_tokens)
                )
            ]

        if not candidates:
            self._log("Нет подходящей модели в рамках бюджета")
            return self.get_fallback(
                expected_tokens=expected_tokens,
                required_capabilities=required_capabilities,
            )

        # Экономичный и безопасный выбор: LOCAL/LIGHT предпочтительнее, затем STANDARD/HEAVY.
        candidates.sort(key=lambda m: (self._tier_rank(m.tier), m.estimate_cost(expected_tokens), -m.context_window))
        return candidates[0]

    def route(self, task_type: str, model_id: str):
        """Вручную назначает модель для типа задачи."""
        self._task_routing[task_type] = model_id
        self._log(f"Маршрут: задача '{task_type}' → модель '{model_id}'")

    def get_fallback(self, expected_tokens: int = 1000,
                     required_capabilities: list | None = None) -> ModelProfile | None:
        """Возвращает самую лёгкую доступную модель как fallback."""
        candidates = [
            m for m in self._models.values()
            if self._matches_capabilities(m, required_capabilities)
        ]
        affordable = [m for m in candidates if self._is_affordable(m, expected_tokens)]
        pool = affordable or candidates or list(self._models.values())
        if not pool:
            return None
        pool.sort(key=lambda m: (self._tier_rank(m.tier), m.estimate_cost(expected_tokens), -m.context_window))
        return pool[0]

    # ── Учёт использования ────────────────────────────────────────────────────

    def record_usage(self, model_id: str, tokens: int):
        """Записывает использование токенов для модели."""
        model = self._models.get(model_id)
        if not model:
            return
        model.record_use(tokens)

        if self.budget_control:
            self.budget_control.spend_tokens(
                prompt=int(tokens * 0.6),
                completion=int(tokens * 0.4),
                model=model.name,
            )
        if self.monitoring:
            self.monitoring.record_tokens(
                int(tokens * 0.6), int(tokens * 0.4), source=model.name
            )

    # ── Управление версиями ───────────────────────────────────────────────────

    def upgrade(self, old_model_id: str, new_model_id: str):
        """
        Переключает с устаревшей модели на новую.
        Переносит маршруты.
        """
        for task_type, mid in list(self._task_routing.items()):
            if mid == old_model_id:
                self._task_routing[task_type] = new_model_id
        if self._active_model_id == old_model_id:
            self._active_model_id = new_model_id
        self._log(f"Upgrade: '{old_model_id}' → '{new_model_id}'")

    # ── Отчёт ────────────────────────────────────────────────────────────────

    def list_models(self) -> list[dict]:
        return [m.to_dict() for m in self._models.values()]

    def get_model(self, model_id: str) -> ModelProfile | None:
        return self._models.get(model_id)

    def summary(self) -> dict:
        total_tokens = sum(m.total_tokens for m in self._models.values())
        total_cost = sum(m.estimate_cost(m.total_tokens) for m in self._models.values())
        return {
            'registered': len(self._models),
            'active': self._active_model_id,
            'total_tokens_used': total_tokens,
            'total_cost_usd': round(total_cost, 4),
            'routes': self._task_routing,
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _register_defaults(self):
        """Предзагрузка стандартных моделей из архитектуры."""
        self.register('claude-opus-4-6',    'Claude Opus 4.6',    ModelTier.HEAVY,
                      'anthropic', cost_per_1k_tokens=0.015, context_window=200000,
                      capabilities=['reasoning', 'coding', 'analysis', 'planning'])

        self.register('claude-sonnet-4-6',  'Claude Sonnet 4.6',  ModelTier.STANDARD,
                      'anthropic', cost_per_1k_tokens=0.003, context_window=200000,
                      capabilities=['reasoning', 'coding', 'analysis', 'planning'])

        self.register('claude-haiku-4-5',   'Claude Haiku 4.5',   ModelTier.LIGHT,
                      'anthropic', cost_per_1k_tokens=0.00025, context_window=200000,
                      capabilities=['reasoning', 'coding'])

        self.register('gpt-5.1',             'GPT-5.1',            ModelTier.STANDARD,
                      'openai', cost_per_1k_tokens=0.0025, context_window=1000000,
                      capabilities=['reasoning', 'coding', 'vision', 'analysis'])

        self.register('gpt-4o',             'GPT-4o',             ModelTier.STANDARD,
                      'openai', cost_per_1k_tokens=0.005, context_window=128000,
                      capabilities=['reasoning', 'coding', 'vision', 'analysis'])

        self.register('gpt-4o-mini',        'GPT-4o Mini',        ModelTier.LIGHT,
                      'openai', cost_per_1k_tokens=0.00015, context_window=128000,
                      capabilities=['reasoning', 'coding'])

        self._active_model_id = 'claude-sonnet-4-6'

    def _log(self, message: str):
        if self.monitoring:
            self.monitoring.info(message, source='model_manager')
        else:
            print(f"[ModelManager] {message}")

    @staticmethod
    def _tier_rank(tier: ModelTier) -> int:
        order = {
            ModelTier.LOCAL: 0,
            ModelTier.LIGHT: 1,
            ModelTier.STANDARD: 2,
            ModelTier.HEAVY: 3,
        }
        return order.get(tier, 99)

    def _is_affordable(self, model: ModelProfile, expected_tokens: int) -> bool:
        if not self.budget_control:
            return True
        return self.budget_control.can_afford(
            tokens=expected_tokens,
            money=model.estimate_cost(expected_tokens),
        )

    @staticmethod
    def _matches_capabilities(model: ModelProfile, required_capabilities: list | None) -> bool:
        if not required_capabilities:
            return True
        return all(cap in model.capabilities for cap in required_capabilities)
