"""
CognitiveModel: многоуровневая когнитивная модель агента.

Интегрирует:
  - Planner        — построение плана из цели/контекста
  - ShortTermMemory — история диалога
  - SentimentAnalyzer — определение настроения входящего текста
  - Inner monologue  — внутренняя рефлексия перед ответом
"""
from __future__ import annotations

from typing import Any

from src.planning.planner import make_plan
from src.planning.schemas import Plan
from src.memory import short_term as stm
from src.sentiment_analysis import SentimentAnalyzer

_analyzer = SentimentAnalyzer()


class CognitiveModel:
    """
    Центральная когнитивная модель агента.

    Цикл обработки одного входящего сообщения:
      1. Запомнить сообщение пользователя.
      2. Оценить настроение.
      3. Построить план действий (Planner).
      4. Сформировать внутренний монолог (рефлексия).
      5. Вернуть структурированный результат.
    """

    def __init__(self, user_id: str = "default") -> None:
        self.user_id = user_id

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------

    def process(self, text: str) -> dict[str, Any]:
        """
        Обработать входящее сообщение.

        Returns:
            {
                "sentiment":        "Positive" | "Negative" | "Neutral",
                "inner_monologue":  str,   # цепочка рассуждений
                "plan":             Plan,
                "memory_snapshot":  list[dict],  # последние сообщения
            }
        """
        # 1. Сохранить в краткосрочную память
        stm.add_message(self.user_id, "user", text)
        history = stm.get_messages(self.user_id)

        # 2. Определить настроение
        sentiment = _analyzer.analyze_sentiment(text)

        # 3. Построить план
        plan: Plan = make_plan(text)

        # 4. Внутренний монолог
        monologue = self._inner_monologue(text, sentiment, plan, history)

        return {
            "sentiment": sentiment,
            "inner_monologue": monologue,
            "plan": plan,
            "memory_snapshot": history,
        }

    def record_response(self, response: str) -> None:
        """Сохранить собственный ответ агента в памяти."""
        stm.add_message(self.user_id, "assistant", response)

    def self_check(self) -> dict[str, Any]:
        """
        Быстрая самодиагностика: текущий размер памяти, словарный объём.
        """
        history = stm.get_messages(self.user_id)
        return {
            "memory_length": len(history),
            "positive_vocab": len(_analyzer._positive),
            "negative_vocab": len(_analyzer._negative),
        }

    # ------------------------------------------------------------------
    # Вспомогательный внутренний монолог
    # ------------------------------------------------------------------

    @staticmethod
    def _inner_monologue(
        text: str,
        sentiment: str,
        plan: Plan,
        history: list[dict[str, str]],
    ) -> str:
        """Сформировать цепочку рассуждений (chain-of-thought) агента."""
        lines: list[str] = [
            f"Получено сообщение: «{text[:80]}{'…' if len(text) > 80 else ''}»",
            f"Настроение: {sentiment}.",
        ]

        if sentiment == "Negative":
            lines.append("Собеседник, возможно, расстроен — стоит проявить эмпатию.")
        elif sentiment == "Positive":
            lines.append("Собеседник настроен позитивно — можно поддержать этот тон.")
        else:
            lines.append("Нейтральный тон — придерживаться информативного стиля.")

        tool_names = [s.tool for s in plan.steps]
        lines.append(f"План: {tool_names}.")

        ctx_len = len(history)
        if ctx_len > 3:
            lines.append(f"Контекст беседы: {ctx_len} сообщений — учтём историю.")
        else:
            lines.append("Беседа только начинается — помним это.")

        return " ".join(lines)

