"""
SentimentAnalyzer — минимальный агент для анализа настроения текста.
Поддерживает: Positive / Negative / Neutral.
"""

from __future__ import annotations

import re
from typing import Optional


POSITIVE_WORDS: set[str] = {
    "love", "like", "great", "excellent", "amazing", "wonderful", "fantastic",
    "good", "best", "happy", "joy", "nice", "superb", "awesome", "brilliant",
    "perfect", "glad", "pleased", "praise", "enjoy", "adore", "brilliant",
    "радость", "хорошо", "отлично", "замечательно", "прекрасно", "нравится",
}

NEGATIVE_WORDS: set[str] = {
    "hate", "bad", "terrible", "awful", "horrible", "worst", "ugly", "poor",
    "disgusting", "dreadful", "nasty", "evil", "sad", "angry", "disappoint",
    "fail", "failure", "broken", "useless", "ridiculous", "stupid",
    "ненавижу", "плохо", "ужасно", "кошмар", "отвратительно", "грустно",
}


class SentimentAnalyzer:
    """Словарный анализатор настроения текста."""

    def __init__(
        self,
        positive_words: Optional[set[str]] = None,
        negative_words: Optional[set[str]] = None,
    ) -> None:
        self._positive: set[str] = positive_words if positive_words is not None else set(POSITIVE_WORDS)
        self._negative: set[str] = negative_words if negative_words is not None else set(NEGATIVE_WORDS)
        # Последние результаты анализа (контекстная память)
        self._history: list[dict] = []

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------

    def analyze_sentiment(self, text: str) -> str:
        """
        Определяет настроение текста.

        Returns:
            "Positive" | "Negative" | "Neutral"
        """
        tokens = self._tokenize(text)
        pos_score = sum(1 for t in tokens if t in self._positive)
        neg_score = sum(1 for t in tokens if t in self._negative)

        if pos_score > neg_score:
            result = "Positive"
        elif neg_score > pos_score:
            result = "Negative"
        else:
            result = "Neutral"

        self._history.append({"text": text, "sentiment": result})
        return result

    def get_response(self, text: str) -> str:
        """Возвращает ответ агента с учётом настроения сообщения."""
        sentiment = self.analyze_sentiment(text)
        if sentiment == "Positive":
            return "Рад слышать это! 😊"
        if sentiment == "Negative":
            return "Понимаю вас. Надеюсь, ситуация улучшится. 🤝"
        return "Понял, спасибо за сообщение."

    def get_history(self) -> list[dict]:
        """Возвращает сохранённую историю анализов."""
        return list(self._history)

    def update_vocabulary(self, positive: list[str] = (), negative: list[str] = ()) -> None:
        """Добавляет новые слова в словарь эмоций на лету."""
        self._positive.update(word.lower() for word in positive)
        self._negative.update(word.lower() for word in negative)

    # ------------------------------------------------------------------
    # Вспомогательные методы
    # ------------------------------------------------------------------

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """Приводит текст к нижнему регистру и разбивает на слова."""
        return re.findall(r"[a-zA-Zа-яА-ЯёЁ]+", text.lower())
