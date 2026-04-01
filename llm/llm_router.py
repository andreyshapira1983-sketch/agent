# LLM Router — умный роутер между OpenAI и Claude
# Логика: OpenAI (gpt-4o-mini) — по умолчанию для всех лёгких задач.
#          Claude (Sonnet/Haiku) — только для тяжёлых ситуаций.
#
# Тяжёлая ситуация — любое из условий:
#   1. Длинный промпт (> HEAVY_PROMPT_CHARS символов) — значит задача сложная
#   2. Промпт содержит ключевые слова тяжёлых задач (планирование, архитектура,
#      рефакторинг, самоисправление, стратегия, анализ большого контекста)
#   3. Параметр system содержит маркер "[HEAVY]"
#
# Дешёвая задача (→ локальный Qwen, бесплатно):
#   1. Промпт короткий (< CHEAP_PROMPT_CHARS символов)
#   2. НЕ содержит ключевых слов тяжёлых задач
#   3. System НЕ имеет маркера [HEAVY]
#   4. Используется в режиме 'balanced' — если local доступен
#
# Интерфейс идентичен OpenAIClient / ClaudeClient:
#   router.infer(prompt, context=None, system=None) → str


import os
import re
import importlib


# Ключевые слова, указывающие на тяжёлую задачу (регистронезависимо)
_HEAVY_KEYWORDS = re.compile(
    r'архитектур|стратег|долгосрочн|рефактор|self.repair|self_repair|'
    r'полный\s+анализ|детальный\s+план|глубокий\s+анализ|разработай\s+систем|'
    r'спроектируй|оптимизируй\s+всю|аудит\s+код|исправь\s+все\s+ошибк|'
    r'long.horizon|long_horizon|self.improv|self_improv|'
    r'перепиши\s+модул|перепиши\s+весь|переделай\s+весь',
    re.IGNORECASE,
)

# Ключевые слова, однозначно указывающие на дешёвую задачу (форматирование, запись, простой поиск)
_CHEAP_KEYWORDS = re.compile(
    r'^(WRITE|READ|SEARCH|ПЕРЕВОД|переведи|переведите|'
    r'запиши|сохрани|создай\s+файл|выведи\s+результат|'
    r'составь\s+список|оформи\s+как|суммаризируй\s+кратко)',
    re.IGNORECASE | re.MULTILINE,
)

# Промпт дешевле этого порога — кандидат на локальный LLM
CHEAP_PROMPT_CHARS = 1200
# Если промпт длиннее этого порога — задача считается тяжёлой
HEAVY_PROMPT_CHARS = 12000


class LLMRouter:
    """
    Роутер между лёгким LLM (OpenAI) и тяжёлым LLM (Claude).

    Алгоритм выбора при каждом вызове infer():
        - Если Claude недоступен (кредиты / не задан) → всегда OpenAI
        - Если промпт короткий и простой               → OpenAI
        - Если промпт длинный > HEAVY_PROMPT_CHARS     → Claude
        - Если промпт содержит тяжёлые ключевые слова  → Claude
        - Если system содержит маркер "[HEAVY]"         → Claude
        - Иначе                                        → OpenAI

    Атрибуты set_model / total_tokens / total_cost_usd совместимы
    с OpenAIClient / ClaudeClient (использует основной клиент).
    """

    def __init__(self, light_client=None, heavy_client=None,
                 local_client=None, monitoring=None):
        """
        Args:
            light_client  — OpenAIClient (обязателен)
            heavy_client  — ClaudeClient (опционален; если None — всё идёт через light)
            monitoring    — объект Monitoring (Слой 17)
        """
        self._light = light_client          # обычно OpenAI
        self._heavy = heavy_client          # Claude или None
        self._local = local_client          # локальный LLM (in-process backend) или None
        self.monitoring = monitoring

        mode = os.environ.get('LLM_ROUTER_MODE', 'balanced').strip().lower()
        self._mode = mode if mode in {'balanced', 'local-first', 'llm-first'} else 'balanced'

        # Статистика маршрутизации
        self._routed_light = 0
        self._routed_heavy = 0
        self._routed_local = 0

    # ── Основной метод ────────────────────────────────────────────────────────

    def _heavy_available(self) -> bool:
        """Доступен ли heavy-клиент для маршрутизации прямо сейчас."""
        if self._heavy is None:
            return False
        # ClaudeClient выставляет этот флаг после ошибки кредитов.
        if getattr(self._heavy, '_credit_exhausted', False):
            return False
        return True

    def _light_available(self) -> bool:
        return self._light is not None

    def _local_available(self) -> bool:
        if self._local is None:
            return False
        # Не используем локальный Qwen если RAM > 85% — выгружаем и отдаём задачу облаку
        try:
            _psutil = importlib.import_module('psutil')
            if _psutil.virtual_memory().percent > 85.0:
                try:
                    getattr(self._local, 'unload')()
                except (AttributeError, TypeError):
                    pass
                return False
        except (ImportError, AttributeError, TypeError, ValueError, RuntimeError, OSError):
            pass
        try:
            status = getattr(self._local, 'health')()
            if isinstance(status, dict):
                return bool(status.get('ok', False))
            return False
        except (AttributeError, TypeError, ValueError, RuntimeError, OSError):
            return False
        return True

    def infer(self, prompt: str, context=None, system: str | None = None,
              history: list | None = None,
              max_tokens: int | None = None) -> str:
        """Выбирает клиент и вызывает infer()."""
        ordered_clients = self._build_priority(prompt, system)
        last_error = None
        for client_name, client in ordered_clients:
            if client is None:
                continue
            try:
                if client_name == 'heavy':
                    self._routed_heavy += 1
                    if self.monitoring:
                        self.monitoring.info(
                            f"[router] HEAVY → Claude (промпт {len(prompt)} симв.)",
                            source="llm_router",
                        )
                elif client_name == 'local':
                    self._routed_local += 1
                else:
                    self._routed_light += 1
                return client.infer(prompt, context=context, system=system, history=history,
                                    max_tokens=max_tokens)
            except (AttributeError, TypeError, ValueError, RuntimeError, OSError, ImportError) as exc:
                last_error = exc
                if self.monitoring:
                    self.monitoring.warning(
                        f"[router] backend {client_name} failed: {type(exc).__name__}: {exc}",
                        source="llm_router",
                    )

        raise RuntimeError(f"Все LLM backends недоступны: {last_error}")

    def _build_priority(self, prompt: str, system: str | None):
        heavy = self._heavy if self._heavy_available() and self._is_heavy(prompt, system) else None
        local = self._local if self._local_available() else None
        light = self._light if self._light_available() else None

        if self._mode == 'local-first':
            return [('local', local), ('heavy', heavy), ('light', light)]
        if self._mode == 'llm-first':
            return [('heavy', heavy), ('light', light), ('local', local)]

        # balanced:
        # 1. Дешёвые задачи → local (Qwen, бесплатно) → light (fallback)
        # 2. Короткие простые реплики → local → остальные
        # 3. Остальное → обычный порядок heavy → light → local
        if local is not None and self._is_cheap(prompt, system):
            if self.monitoring:
                self.monitoring.info(
                    f"[router] CHEAP → Local Qwen (промпт {len(prompt or '')} симв.)",
                    source="llm_router",
                )
            return [('local', local), ('light', light), ('heavy', heavy)]

        short_prompt = len((prompt or '').split()) <= 12 and len(prompt or '') <= 220
        if short_prompt and local is not None:
            return [('local', local), ('heavy', heavy), ('light', light)]
        return [('heavy', heavy), ('light', light), ('local', local)]

    # ── Критерий тяжёлой задачи ───────────────────────────────────────────────

    def _is_heavy(self, prompt: str, system: str | None) -> bool:
        """Возвращает True если задача считается тяжёлой."""
        # Маркер в system-сообщении
        if system and '[HEAVY]' in system:
            return True
        # Длинный промпт
        if len(prompt) > HEAVY_PROMPT_CHARS:
            return True
        # Ключевые слова
        if _HEAVY_KEYWORDS.search(prompt):
            return True
        return False

    def _is_cheap(self, prompt: str, system: str | None) -> bool:
        """
        Возвращает True если задача дешёвая — можно отправить в локальный Qwen.

        Дешёвая задача:
          - Промпт короткий (< CHEAP_PROMPT_CHARS)
          - НЕ тяжёлая (нет heavy-ключевых слов, нет маркера [HEAVY])
          - Явно содержит дешёвые ключевые слова (запись, перевод, поиск, список)
        """
        if self._is_heavy(prompt, system):
            return False
        if len(prompt or '') > CHEAP_PROMPT_CHARS:
            return False
        if _CHEAP_KEYWORDS.search(prompt or ''):
            return True
        # Короткий промпт без тяжёлых ключевых слов — тоже дёшево
        if len(prompt or '') <= 600:
            return True
        return False

    # ── Совместимость с клиентами ─────────────────────────────────────────────

    def set_model(self, model: str):
        """Меняет модель активного light-клиента."""
        if self._light is not None:
            self._light.set_model(model)

    @property
    def model(self) -> str:
        """Описание конфигурации роутера (для логирования)."""
        light_model = getattr(self._light, 'model', 'none') if self._light else 'none'
        heavy_info = ""
        if self._heavy:
            heavy_info = f", heavy={getattr(self._heavy, 'model', 'claude')}"
        local_info = ""
        if self._local:
            local_info = f", local={getattr(self._local, 'model', 'local')}"
        return f"LLMRouter(mode={self._mode}, light={light_model}{heavy_info}{local_info})"

    @property
    def budget_control(self):
        return getattr(self._light, 'budget_control', None)

    @budget_control.setter
    def budget_control(self, value):
        """Пробрасывает budget_control в оба клиента."""
        if self._light is not None and hasattr(self._light, 'budget_control'):
            self._light.budget_control = value
        if self._heavy is not None and hasattr(self._heavy, 'budget_control'):
            self._heavy.budget_control = value

    @property
    def total_tokens(self) -> int:
        light = getattr(self._light, 'total_tokens', 0) if self._light else 0
        heavy = getattr(self._heavy, 'total_tokens', 0) if self._heavy else 0
        local = getattr(self._local, 'total_tokens', 0) if self._local else 0
        return (light or 0) + (heavy or 0) + (local or 0)

    @property
    def total_cost_usd(self) -> float:
        light = getattr(self._light, 'total_cost_usd', 0.0) if self._light else 0.0
        heavy = getattr(self._heavy, 'total_cost_usd', 0.0) if self._heavy else 0.0
        local = getattr(self._local, 'total_cost_usd', 0.0) if self._local else 0.0
        return round((light or 0.0) + (heavy or 0.0) + (local or 0.0), 6)

    def routing_stats(self) -> dict:
        """Статистика: сколько вызовов ушло на каждый клиент."""
        total = self._routed_light + self._routed_heavy
        return {
            'total_calls': total,
            'light_calls': self._routed_light,
            'heavy_calls': self._routed_heavy,
            'local_calls': self._routed_local,
            'heavy_pct': round(self._routed_heavy / total * 100, 1) if total else 0.0,
            'local_pct': round(self._routed_local / total * 100, 1) if total else 0.0,
            'mode': self._mode,
        }
