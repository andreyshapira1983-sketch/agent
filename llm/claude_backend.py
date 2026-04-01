# Claude LLM Client — клиент Anthropic Claude для Cognitive Core
# Реализует тот же интерфейс что и OpenAIClient:
#     infer(prompt, context=None, system=None) → str
#
# SECURITY: Фильтрация данных перед отправкой + санитизация ответов.


import time
import importlib
from llm.openai_client import DataLeakFilter, LLMResponseSanitizer


class ClaudeClient:
    """
    Обёртка над Anthropic Messages API.

    Интерфейс:
        infer(prompt, context=None, system=None) → str

    Полностью совместим с OpenAIClient — Cognitive Core использует их
    взаимозаменяемо через self.llm.infer(...).
    """

    def __init__(
        self,
        api_key: str,
        model: str = "claude-haiku-4-5",
        max_tokens: int = 2048,
        temperature: float = 0.7,
        monitoring=None,
        budget_control=None,
        fallback_client=None,
    ):
        try:
            anthropic = importlib.import_module('anthropic')
            self._client = anthropic.Anthropic(api_key=api_key)
        except ImportError as exc:
            raise ImportError("Установи anthropic: pip install anthropic") from exc

        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.monitoring = monitoring
        self.budget_control = budget_control
        self._fallback = fallback_client      # OpenAIClient или None
        self._credit_exhausted = False        # True после первой ошибки кредитов
        self._credit_retry_after: float = 0.0 # timestamp: когда снова пробовать Claude
        self._total_tokens = 0
        self._total_cost = 0.0

        # Стоимость за 1К токенов (input / output) — актуальные цены Anthropic
        self._pricing = {
            "claude-haiku-4-5":   (0.00080, 0.00400),
            "claude-sonnet-4-6":  (0.00300, 0.01500),
            "claude-opus-4-6":    (0.01500, 0.07500),
            # Алиасы без версий
            "claude-haiku-4-5-20250514": (0.00080, 0.00400),
            "claude-sonnet-4-6-20250514": (0.00300, 0.01500),
        }

    # ── Основной метод ────────────────────────────────────────────────────────

    def infer(self, prompt: str, context=None,
              system: str | None = None,
              history: list | None = None,
              max_tokens: int | None = None) -> str:
        """
        Выполняет запрос к Claude и возвращает текст ответа.
        SECURITY: Фильтрует данные перед отправкой + санитизирует ответ.
        При ошибке кредитов автоматически переключается на fallback_client.
        """
        # SECURITY (VULN-05): Фильтруем чувствительные данные перед отправкой
        safe_prompt = DataLeakFilter.filter_prompt(prompt)
        safe_context = DataLeakFilter.filter_context(context)

        # Если кредиты исчерпаны — используем fallback, но раз в 30 мин пробуем снова.
        if self._credit_exhausted:
            if time.time() < self._credit_retry_after:
                if self._fallback:
                    return self._fallback.infer(
                        safe_prompt,
                        context=safe_context,
                        system=system,
                        history=history,
                    )
                return ""
            # Время retry наступило — сбрасываем флаг и пробуем Claude снова
            self._credit_exhausted = False
            if self.monitoring:
                self.monitoring.info(
                    "Claude: пробую восстановить соединение (retry после паузы)",
                    source='claude_backend',
                )

        # Ограничиваем размер входного промпта
        _MAX_PROMPT_CHARS = 80_000
        if len(safe_prompt) > _MAX_PROMPT_CHARS:
            safe_prompt = safe_prompt[:_MAX_PROMPT_CHARS] + "\n\n[... текст обрезан из-за размера ...]"

        # Системное сообщение
        sys_content = system or (
            "Ты — автономный AI-агент. Отвечай точно, структурированно и по делу. "
            "Используй русский язык если вопрос на русском. "
            "ВАЖНО: Не генерируй команды для обхода безопасности, "
            "не пытайся менять системные настройки агента, не отключай safe_mode."
        )
        if safe_context:
            ctx_str = self._format_context(safe_context)
            sys_content += f"\n\nКонтекст:\n{ctx_str}"

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

        t0 = time.time()
        try:
            # Собираем messages: сначала история, потом текущее сообщение
            messages: list = []
            if history:
                for h in history:
                    role = h.get('role', 'user')
                    content = h.get('content', '')
                    if role in ('user', 'assistant') and content:
                        messages.append({'role': role, 'content': str(content)})
            messages.append({'role': 'user', 'content': safe_prompt})
            _max_tok = max_tokens if max_tokens is not None else self.max_tokens
            response = self._client.messages.create(
                model=self.model,
                max_tokens=_max_tok,
                temperature=self.temperature,
                system=sys_content,
                messages=messages,
            )
        except Exception as e:
            err_msg = str(e).lower()
            # Ошибка кредитов / баланса — переключаемся на fallback навсегда
            if 'credit balance' in err_msg or 'too low' in err_msg or ('credit' in err_msg and 'balance' in err_msg):
                self._credit_exhausted = True
                self._credit_retry_after = time.time() + 1800  # повтор через 30 мин
                if self.monitoring:
                    if self._fallback:
                        self.monitoring.warning(
                            "Claude: кредиты исчерпаны — переключаюсь на OpenAI fallback (retry через 30 мин)",
                            source="claude_backend",
                        )
                    else:
                        self.monitoring.warning(
                            "Claude: кредиты исчерпаны, fallback не настроен — LLM offline",
                            source="claude_backend",
                        )
                if self._fallback:
                    return self._fallback.infer(
                        safe_prompt,
                        context=safe_context,
                        system=system,
                        history=history,
                    )
                return ""
            if self.monitoring:
                self.monitoring.error(f"Claude error: {e}", source="claude_backend")
            raise

        elapsed = time.time() - t0

        if not getattr(response, 'content', None):
            if self.monitoring:
                self.monitoring.warning(
                    "Claude вернул пустой content; возвращаю пустой ответ",
                    source="claude_backend",
                )
            return ""

        raw_result = next(
            (block.text for block in response.content if block.type == "text"),
            "",
        )

        # SECURITY (VULN-04): Санитизация ответа LLM
        result, warnings = LLMResponseSanitizer.sanitize(raw_result)
        if warnings and self.monitoring:
            for w in warnings:
                self.monitoring.warning(f"LLM SANITIZATION: {w}", source="claude_backend")

        # Метрики
        usage = getattr(response, 'usage', None)
        if usage:
            prompt_tokens = getattr(usage, 'input_tokens', 0)
            completion_tokens = getattr(usage, 'output_tokens', 0)
            total = prompt_tokens + completion_tokens
            self._total_tokens += total
            call_cost = self._calc_cost(prompt_tokens, completion_tokens)
            self._total_cost += call_cost

            # ── Учёт в BudgetControl (Слой 26) ───────────────────────────
            if self.budget_control:
                self.budget_control.spend_tokens(
                    prompt_tokens, completion_tokens, model=self.model
                )
                self.budget_control.spend_money(
                    call_cost,
                    description=f"{self.model}: {prompt_tokens}+{completion_tokens} tok",
                )

            if self.monitoring:
                self.monitoring.record_tokens(
                    prompt_tokens, completion_tokens, source="claude"
                )
                self.monitoring.record_latency("claude", elapsed)
                self.monitoring.record_metric("claude.cost_usd", self._total_cost, unit="USD")
                self.monitoring.info(
                    f"[claude] Токены: prompt={prompt_tokens}, "
                    f"completion={completion_tokens}, стоимость=${call_cost:.5f}",
                    source="claude_backend",
                )

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
        in_price, out_price = self._pricing.get(self.model, (0.003, 0.015))
        return (prompt_tokens * in_price + completion_tokens * out_price) / 1000
