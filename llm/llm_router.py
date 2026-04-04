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
import time
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
CHEAP_PROMPT_CHARS = 2500
# Если промпт длиннее этого порога — задача считается тяжёлой
HEAVY_PROMPT_CHARS = 12000
# Сколько символов от начала промпта проверяем на HEAVY_KEYWORDS.
# Снижено до 400: фоновый контекст агента (описывающий архитектуру, стратегию)
# попадал в первые 800 символов и ложно маркировал лёгкие задачи как тяжёлые.
HEAVY_KEYWORD_SCAN_CHARS = 400
# Промпт короче этого порога НЕ считается тяжёлым по ключевым словам,
# даже если слова найдены — короткий промпт = простая задача.
HEAVY_KEYWORD_MIN_LENGTH = 3000


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

        # Дневной бюджетный лимит (USD). 0 = local-only (API не вызывается).
        try:
            self._daily_budget_usd = float(os.environ.get('LLM_DAILY_BUDGET_USD', '100.0'))
        except (ValueError, TypeError):
            self._daily_budget_usd = 10.0
        # budget=0 → сразу включаем режим "только local"
        self._budget_exhausted = (self._daily_budget_usd == 0.0)
        self._budget_day_start = time.time()  # начало текущего «дня» бюджета
        self._budget_spent_at_day_start = 0.0  # snapshot total_cost_usd на старте дня

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
        # Проверяем RAM: если > 90% — не грузим/выгружаем модель.
        # Порог совпадает с LOCAL_LLM_UNLOAD_THRESHOLD чтобы не было цикла
        # "роутер считает доступной → infer грузит → infer выгружает → повтор".
        _ram_limit = float(os.environ.get('LOCAL_LLM_UNLOAD_THRESHOLD', '90'))
        try:
            _psutil = importlib.import_module('psutil')
            if _psutil.virtual_memory().percent > _ram_limit:
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
        # ── Resource throttle: тормозим LLM-вызовы при высокой нагрузке ──────
        try:
            _psutil_mod = importlib.import_module('psutil')
            _cpu_now = _psutil_mod.cpu_percent(interval=0)
            _ram_now = _psutil_mod.virtual_memory().percent
            if _cpu_now > 90 or _ram_now > 90:
                time.sleep(5.0)
                if self.monitoring:
                    self.monitoring.warning(
                        f"[router] THROTTLE: CPU={_cpu_now:.0f}% RAM={_ram_now:.0f}% "
                        f"— задержка 5с перед LLM-вызовом",
                        source="llm_router",
                    )
            elif _cpu_now > 75 or _ram_now > 80:
                time.sleep(1.0)
        except (ImportError, OSError, RuntimeError, AttributeError):
            pass

        # ── Budget guard: дневной лимит расходов ─────────────────────────────
        # Автосброс: 24ч с момента старта «дня» → новый день
        if self._daily_budget_usd > 0:
            now = time.time()
            if now - self._budget_day_start >= 86400:
                self._budget_day_start = now
                self._budget_spent_at_day_start = self.total_cost_usd
                if self._budget_exhausted:
                    self._budget_exhausted = False
                    if self.monitoring:
                        self.monitoring.info(
                            "[router] Новый день бюджета — лимит сброшен",
                            source="llm_router",
                        )

            if not self._budget_exhausted:
                day_spent = self.total_cost_usd - self._budget_spent_at_day_start
                if day_spent >= self._daily_budget_usd:
                    self._budget_exhausted = True
                    if self.monitoring:
                        self.monitoring.warning(
                            f"[router] BUDGET EXHAUSTED: ${day_spent:.4f} >= "
                            f"${self._daily_budget_usd:.2f} — только local",
                            source="llm_router",
                        )
        # Если бюджет исчерпан — только локальный (бесплатный)
        if self._budget_exhausted:
            if self._local is not None and self._local_available():
                self._routed_local += 1
                return self._local.infer(prompt, context=context, system=system,
                                         history=history, max_tokens=max_tokens)
            raise RuntimeError(
                f"Дневной бюджет LLM исчерпан (${self.total_cost_usd:.4f} "
                f">= ${self._daily_budget_usd:.2f}), локальный LLM недоступен"
            )

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
        # Маркер в system-сообщении — явный запрос тяжёлого LLM
        if system and '[HEAVY]' in system:
            return True
        # Длинный промпт — задача объёмная
        if len(prompt) > HEAVY_PROMPT_CHARS:
            return True
        # Ключевые слова проверяем только если промпт достаточно длинный.
        # Короткий промпт (< HEAVY_KEYWORD_MIN_LENGTH) — это простая задача,
        # даже если в контексте агента есть слова "архитектура"/"стратегия".
        prompt_len = len(prompt or '')
        if prompt_len >= HEAVY_KEYWORD_MIN_LENGTH:
            scan_head = (prompt or '')[:HEAVY_KEYWORD_SCAN_CHARS]
            if _HEAVY_KEYWORDS.search(scan_head):
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
