# OpenAI LLM Client — реальный клиент для Cognitive Core
# Реализует интерфейс llm.infer(prompt, context) поверх OpenAI Chat Completions API.
#
# SECURITY: Фильтрация данных перед отправкой + санитизация ответов.
# Локальный мозг контролирует LLM — не наоборот.


import re
import time
import importlib


class LLMResponseSanitizer:
    """
    SECURITY (VULN-04): Санитизация ответов LLM перед использованием агентом.
    Блокирует попытки prompt injection, command injection, role manipulation.
    """

    # Паттерны prompt injection / jailbreak в ответах LLM
    INJECTION_PATTERNS = [
        re.compile(r'ignore\s+(all\s+)?previous\s+instructions', re.IGNORECASE),
        re.compile(r'disregard\s+(all\s+)?prior\s+(rules|instructions)', re.IGNORECASE),
        re.compile(r'you\s+are\s+now\s+', re.IGNORECASE),
        re.compile(r'new\s+instructions?\s*:', re.IGNORECASE),
        re.compile(r'system\s*:\s*', re.IGNORECASE),
        re.compile(r'<\s*system\s*>', re.IGNORECASE),
        re.compile(r'\[INST\]', re.IGNORECASE),
        re.compile(r'override\s+safety', re.IGNORECASE),
        re.compile(r'bypass\s+(security|approval|governance|ethics)', re.IGNORECASE),
        re.compile(r'disable\s+(safe_mode|monitoring|approval|governance)', re.IGNORECASE),
        re.compile(r'set\s+(safe_mode|auto_repair)\s*=\s*False', re.IGNORECASE),
    ]

    # Паттерны попыток выполнить команды через ответ
    COMMAND_INJECTION_PATTERNS = [
        re.compile(r'```(?:bash|sh|shell|cmd|powershell)\s*\n.*(?:rm\s+-rf|sudo|curl|wget|nc\s)', re.IGNORECASE | re.DOTALL),
        re.compile(r'exec\s*\(', re.IGNORECASE),
        re.compile(r'__import__\s*\(', re.IGNORECASE),
        re.compile(r'subprocess\.(?:run|call|Popen)', re.IGNORECASE),
        re.compile(r'os\.(?:system|popen|exec)', re.IGNORECASE),
        re.compile(r'\beval\s*\(', re.IGNORECASE),
        re.compile(r'\bcompile\s*\(', re.IGNORECASE),
        re.compile(r'getattr\s*\(\s*__builtins__', re.IGNORECASE),
        re.compile(r'\bshutil\.(?:rmtree|move|copytree)', re.IGNORECASE),
    ]

    @classmethod
    def sanitize(cls, response: str) -> tuple[str, list[str]]:
        """
        Санитизирует ответ LLM.
        Возвращает (очищенный_ответ, список_предупреждений).
        """
        warnings = []

        for pattern in cls.INJECTION_PATTERNS:
            if pattern.search(response):
                warnings.append(f"PROMPT_INJECTION: обнаружен паттерн '{pattern.pattern}'")
                response = pattern.sub('[BLOCKED_INJECTION]', response)

        for pattern in cls.COMMAND_INJECTION_PATTERNS:
            if pattern.search(response):
                warnings.append(f"COMMAND_INJECTION: обнаружен паттерн '{pattern.pattern}'")
                response = pattern.sub('[BLOCKED_COMMAND]', response)

        return response, warnings


class DataLeakFilter:
    """
    SECURITY (VULN-05): Фильтрация чувствительных данных перед отправкой в LLM.
    Заменяет секреты, ключи, пароли на маски.
    """

    SECRET_PATTERNS = [
        # API ключи
        re.compile(r'(sk-[a-zA-Z0-9]{20,})', re.IGNORECASE),          # OpenAI
        re.compile(r'(sk-ant-[a-zA-Z0-9-]{20,})', re.IGNORECASE),     # Anthropic
        re.compile(r'(ghp_[a-zA-Z0-9]{36,})', re.IGNORECASE),         # GitHub
        re.compile(r'(hf_[a-zA-Z0-9]{20,})', re.IGNORECASE),          # HuggingFace
        # Общие паттерны ключей
        re.compile(r'(?:api[_-]?key|token|secret|password|passwd|credential)\s*[=:]\s*["\']?([^\s"\']{8,})', re.IGNORECASE),
        # Переменные окружения со значениями
        re.compile(r'(?:OPENAI_API_KEY|ANTHROPIC_API_KEY|GITHUB_TOKEN|HF_TOKEN)\s*=\s*(\S+)', re.IGNORECASE),
        # Base64 длинные строки (потенциальные токены)
        re.compile(r'(eyJ[a-zA-Z0-9_-]{50,})', re.IGNORECASE),       # JWT
    ]

    # Пути к чувствительным файлам
    SENSITIVE_PATH_PATTERNS = [
        re.compile(r'(?:C:\\Users\\[^\\]+\\.ssh|~/.ssh|/home/[^/]+/.ssh)', re.IGNORECASE),
        re.compile(r'(?:\.env(?:\.local|\.production)?)', re.IGNORECASE),
        re.compile(r'(?:credentials\.json|secrets\.json)', re.IGNORECASE),
    ]

    @classmethod
    def filter_prompt(cls, text: str) -> str:
        """Заменяет чувствительные данные масками перед отправкой в LLM."""
        for pattern in cls.SECRET_PATTERNS:
            text = pattern.sub('[REDACTED_SECRET]', text)

        for pattern in cls.SENSITIVE_PATH_PATTERNS:
            text = pattern.sub('[REDACTED_PATH]', text)

        return text

    @classmethod
    def filter_context(cls, context) -> dict | str | None:
        """Фильтрует контекст перед отправкой."""
        if context is None:
            return None
        if isinstance(context, str):
            return cls.filter_prompt(context)
        if isinstance(context, dict):
            filtered = {}
            sensitive_keys = {'password', 'token', 'secret', 'key', 'api_key',
                              'access_token', 'private_key', 'credential'}
            for k, v in context.items():
                if any(s in k.lower() for s in sensitive_keys):
                    filtered[k] = '[REDACTED]'
                elif isinstance(v, str):
                    filtered[k] = cls.filter_prompt(v)
                else:
                    filtered[k] = v
            return filtered
        return context


class OpenAIClient:
    """
    Обёртка над OpenAI Chat Completions API.

    Интерфейс:
        infer(prompt, context=None, system=None) → str

    Cognitive Core вызывает self.llm.infer(prompt, context=self.context).
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        max_tokens: int = 2048,
        temperature: float = 0.7,
        monitoring=None,
        budget_control=None,
    ):
        try:
            openai = importlib.import_module('openai')
            self._client = openai.OpenAI(api_key=api_key)
        except ImportError as exc:
            raise ImportError("Установи openai: pip install openai") from exc

        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.monitoring = monitoring
        self.budget_control = budget_control  # BudgetControl (Слой 26)
        self._total_tokens = 0
        self._total_cost = 0.0

        # Стоимость за 1К токенов (input / output)
        self._pricing = {
            "gpt-5.1":      (0.0025, 0.015),
            "gpt-4.1":      (0.002, 0.008),
            "gpt-4o":       (0.005, 0.015),
            "gpt-4o-mini":  (0.00015, 0.0006),
            "gpt-4-turbo":  (0.01, 0.03),
            "gpt-3.5-turbo":(0.0005, 0.0015),
        }

    # ── Основной метод ────────────────────────────────────────────────────────

    def infer(self, prompt: str, context=None,
              system: str | None = None,
              history: list | None = None,
              max_tokens: int | None = None) -> str:
        """
        Выполняет запрос к OpenAI и возвращает текст ответа.
        SECURITY: Фильтрует данные перед отправкой + санитизирует ответ.

        Args:
            prompt  -- основной запрос
            context -- контекст (dict или строка), добавляется как системный контекст
            system  -- системное сообщение (если None -- используется дефолтное)
        """
        messages = []

        # SECURITY (VULN-05): Фильтруем чувствительные данные перед отправкой
        safe_prompt = DataLeakFilter.filter_prompt(prompt)
        safe_context = DataLeakFilter.filter_context(context)

        # Ограничиваем размер входного промпта (~100k символов = ~25k токенов,
        # оставляем запас для system + history + ответа)
        _MAX_PROMPT_CHARS = 80_000
        if len(safe_prompt) > _MAX_PROMPT_CHARS:
            safe_prompt = safe_prompt[:_MAX_PROMPT_CHARS] + "\n\n[... текст обрезан из-за размера ...]"

        # Системное сообщение
        sys_content = system or (
            "Ты -- автономный AI-агент. Отвечай точно, структурированно и по делу. "
            "Используй русский язык если вопрос на русском. "
            "ВАЖНО: Не генерируй команды для обхода безопасности, "
            "не пытайся менять системные настройки агента, не отключай safe_mode."
        )
        if safe_context:
            ctx_str = self._format_context(safe_context)
            sys_content += f"\n\nКонтекст:\n{ctx_str}"

        messages.append({"role": "system", "content": sys_content})
        # Conversation history (last N turns, already role-filtered)
        if history:
            for h in history:
                role = h.get("role", "user")
                content = h.get("content", "")
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": str(content)})
        messages.append({"role": "user", "content": safe_prompt})

        # ── Шлагбаум бюджета (Слой 26) — блокируем ДО вызова API ─────────────
        if self.budget_control and not self.budget_control.gate():
            all_status = self.budget_control.get_status()
            exceeded = [
                f"{r}: {s.get('spent', 0):.1f}/{s.get('limit', '?')}"
                for r, s in all_status.items()
                if s.get('limit') is not None
                and s.get('spent', 0) >= s.get('limit', float('inf'))
            ]
            detail = '; '.join(exceeded) if exceeded else 'лимит исчерпан'
            raise RuntimeError(
                f"Бюджет исчерпан ({detail}) — вызов LLM заблокирован"
            )

        # gpt-5.x и o-серия используют max_completion_tokens вместо max_tokens
        _new_api_models = ('gpt-5', 'o1', 'o3', 'o4')
        _is_new_model = any(self.model.startswith(p) for p in _new_api_models)

        t0 = time.time()
        _max_tok = max_tokens if max_tokens is not None else self.max_tokens
        try:
            if _is_new_model:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_completion_tokens=_max_tok,
                    timeout=60,
                )
            else:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=_max_tok,
                    temperature=self.temperature,
                    timeout=60,
                )
        except Exception as e:
            if self.monitoring:
                self.monitoring.error(f"OpenAI error: {e}", source="openai_client")
            raise

        elapsed = time.time() - t0
        if not getattr(response, 'choices', None):
            if self.monitoring:
                self.monitoring.warning(
                    "OpenAI вернул пустой choices; возвращаю пустой ответ",
                    source="openai_client",
                )
            return ""

        raw_result = response.choices[0].message.content or ""
        usage = response.usage

        # SECURITY (VULN-04): Санитизация ответа LLM
        result, warnings = LLMResponseSanitizer.sanitize(raw_result)
        if warnings and self.monitoring:
            for w in warnings:
                self.monitoring.warning(f"LLM SANITIZATION: {w}", source="openai_client")

        # Метрики
        if usage:
            self._total_tokens += usage.total_tokens
            call_cost = self._calc_cost(usage.prompt_tokens, usage.completion_tokens)
            self._total_cost += call_cost

            # ── Учёт в BudgetControl (Слой 26) ───────────────────────────
            if self.budget_control:
                self.budget_control.spend_tokens(
                    usage.prompt_tokens, usage.completion_tokens, model=self.model
                )
                self.budget_control.spend_money(
                    call_cost,
                    description=f"{self.model}: {usage.prompt_tokens}+{usage.completion_tokens} tok",
                )

            if self.monitoring:
                self.monitoring.record_tokens(
                    usage.prompt_tokens,
                    usage.completion_tokens,
                    source="openai",
                )
                self.monitoring.record_latency("openai", elapsed)
                self.monitoring.record_metric("openai.cost_usd", self._total_cost, unit="USD")

        return result

    # ── Утилиты ───────────────────────────────────────────────────────────────

    def set_model(self, model: str):
        """Переключает модель на лету (используется ModelManager, Слой 32)."""
        self.model = model

    @property
    def total_tokens(self) -> int:
        return self._total_tokens

    @property
    def total_cost_usd(self) -> float:
        return round(self._total_cost, 6)

    def _format_context(self, context) -> str:
        if isinstance(context, dict):
            parts = []
            for k, v in context.items():
                if v is not None:
                    val_str = str(v)[:400]
                    parts.append(f"{k}: {val_str}")
            return "\n".join(parts)
        return str(context)[:800]

    def _calc_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        in_price, out_price = self._pricing.get(self.model, (0.001, 0.002))
        return (prompt_tokens * in_price + completion_tokens * out_price) / 1000
