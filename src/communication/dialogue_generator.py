"""
DialogueGenerator: генератор реплик агента с поддержкой инициативы.

Функции:
  - generate()          — сформировать ответ по плану и настроению
  - generate_question() — curiosity-driven вопрос для продолжения беседы
  - track_topic_interest() — обновить/получить метрики интереса к темам
  - update_vocabulary() — динамически расширить словарь эмоций
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from src.planning.schemas import Plan
from src.sentiment_analysis import SentimentAnalyzer

_analyzer = SentimentAnalyzer()

# Шаблоны ответов по настроению
_TEMPLATES: dict[str, list[str]] = {
    "Positive": [
        "Отлично! {context}",
        "Рад слышать это. {context}",
        "Замечательно! {context}",
    ],
    "Negative": [
        "Понимаю вас. {context} Надеюсь, ситуация улучшится.",
        "Сочувствую. {context} Давайте разберёмся вместе.",
        "Это непросто. {context}",
    ],
    "Neutral": [
        "{context}",
        "Понял. {context}",
        "Хорошо. {context}",
    ],
}

# Базовые curiosity-вопросы по ключевым темам
_TOPIC_QUESTIONS: dict[str, list[str]] = {
    "general": [
        "Что для вас сейчас наиболее важно?",
        "Расскажите подробнее — что именно вас интересует?",
        "Есть ли что-то, что хотелось бы обсудить отдельно?",
    ],
    "tech": [
        "Какие инструменты или технологии вы используете?",
        "С какими техническими трудностями вы сталкиваетесь чаще всего?",
    ],
    "emotion": [
        "Что вас беспокоит больше всего прямо сейчас?",
        "Как я могу лучше вам помочь?",
    ],
}


class DialogueGenerator:
    """
    Генератор реплик агента.

    Хранит:
      _topic_scores — счётчики упоминаний тем (для метрик интереса)
      _turn_count   — номер хода беседы
    """

    def __init__(self) -> None:
        self._topic_scores: dict[str, int] = defaultdict(int)
        self._turn_count: int = 0
        self._last_initiative_turn: int = -5  # когда последний раз задавали вопрос

    # ------------------------------------------------------------------
    # Генерация ответа
    # ------------------------------------------------------------------

    def generate(
        self,
        text: str,
        sentiment: str,
        plan: Plan,
        memory_snapshot: list[dict[str, str]] | None = None,
    ) -> str:
        """
        Формирует реплику агента на основе плана и настроения.

        Args:
            text:            исходный текст пользователя
            sentiment:       "Positive" | "Negative" | "Neutral"
            plan:            объект Plan из CognitiveModel
            memory_snapshot: история сообщений для контекста

        Returns:
            Готовая реплика агента (str).
        """
        self._turn_count += 1
        self._update_topic_scores(text)

        # Выбрать шаблон
        templates = _TEMPLATES.get(sentiment, _TEMPLATES["Neutral"])
        template = templates[self._turn_count % len(templates)]

        # Сформировать контекстную часть из плана
        tool_names = [s.tool for s in plan.steps]
        context_hint = f"Выполняю: {', '.join(tool_names)}." if tool_names else ""

        reply = template.format(context=context_hint).strip()

        # Добавить инициативный вопрос раз в 3–5 ходов
        if self._should_ask_initiative():
            question = self.generate_question(text)
            reply = f"{reply} {question}" if reply else question
            self._last_initiative_turn = self._turn_count

        return reply

    # ------------------------------------------------------------------
    # Генерация инициативного вопроса (curiosity-driven)
    # ------------------------------------------------------------------

    def generate_question(self, context_text: str = "") -> str:
        """
        Генерирует вопрос для продолжения беседы на основе контекста.
        """
        ctx_lower = context_text.lower()

        # Определить тематику
        if any(w in ctx_lower for w in ("код", "python", "модуль", "ошибка", "тест", "файл", "agent")):
            topic_key = "tech"
        elif any(w in ctx_lower for w in ("плохо", "грустно", "проблема", "расстроен", "беспокоит")):
            topic_key = "emotion"
        else:
            topic_key = "general"

        questions = _TOPIC_QUESTIONS[topic_key]
        idx = self._turn_count % len(questions)
        return questions[idx]

    # ------------------------------------------------------------------
    # Метрики интереса к темам
    # ------------------------------------------------------------------

    def track_topic_interest(self) -> dict[str, int]:
        """Возвращает текущие счётчики упоминаний тем."""
        return dict(self._topic_scores)

    def top_topics(self, n: int = 3) -> list[tuple[str, int]]:
        """Топ-n тем по частоте упоминания."""
        return sorted(self._topic_scores.items(), key=lambda x: x[1], reverse=True)[:n]

    # ------------------------------------------------------------------
    # Динамическое обновление словаря эмоций
    # ------------------------------------------------------------------

    def update_vocabulary(
        self,
        positive: list[str] | None = None,
        negative: list[str] | None = None,
    ) -> None:
        """Расширить словарь эмоций SentimentAnalyzer на лету."""
        _analyzer.update_vocabulary(
            positive=positive or [],
            negative=negative or [],
        )

    # ------------------------------------------------------------------
    # Вспомогательные методы
    # ------------------------------------------------------------------

    def _update_topic_scores(self, text: str) -> None:
        """Извлекаем существительные/ключевые слова и инкрементируем счётчики."""
        tokens = re.findall(r"[a-zA-Zа-яА-ЯёЁ]{4,}", text.lower())
        stopwords = {"этот", "этом", "этого", "есть", "быть", "when", "that", "this", "with"}
        for token in tokens:
            if token not in stopwords:
                self._topic_scores[token] += 1

    def _should_ask_initiative(self) -> bool:
        """Спрашивать ли сейчас инициативный вопрос."""
        gap = self._turn_count - self._last_initiative_turn
        return gap >= 4
