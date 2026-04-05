# Decision Explainer — объяснимость действий агента-партнёра
# Архитектура: Partner Core → объяснимость (почему агент сделал X)
# Меморандум (Часть 4.4): "Агент имеет право и обязан говорить: не знаю,
#   не уверен, вижу риск, это плохая идея"
# Меморандум (Часть 5.1): "запоминание не тайное — я должен видеть,
#   что именно он понял обо мне"
#
# Функции:
#   - объяснение принятых решений в human-readable формате
#   - decision trace: "рассматривал A, B, C → выбрал B потому что..."
#   - ретроспективный анализ: "почему X произошло"
#   - прозрачность: пользователь видит reasoning

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from enum import Enum


class DecisionType(Enum):
    """Тип решения."""
    ACTION      = 'action'       # выбор действия
    PLANNING    = 'planning'     # планирование задачи
    INITIATIVE  = 'initiative'   # инициативное предложение
    REJECTION   = 'rejection'    # отказ от действия
    ESCALATION  = 'escalation'   # эскалация к человеку
    PRIORITY    = 'priority'     # выбор приоритета


class Confidence(Enum):
    """Уровень уверенности в решении."""
    HIGH   = 'high'       # > 0.8
    MEDIUM = 'medium'     # 0.5 - 0.8
    LOW    = 'low'        # 0.3 - 0.5
    UNSURE = 'unsure'     # < 0.3


@dataclass
class ConsideredOption:
    """Один вариант, который рассматривался."""
    description: str
    pros: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)
    estimated_risk: str = 'low'    # low / medium / high
    estimated_value: str = 'medium'
    rejected_reason: str = ''      # если отклонён

    def to_dict(self) -> dict:
        return {
            'description': self.description,
            'pros': self.pros,
            'cons': self.cons,
            'estimated_risk': self.estimated_risk,
            'estimated_value': self.estimated_value,
            'rejected_reason': self.rejected_reason,
        }


@dataclass
class DecisionTrace:
    """Полная трассировка одного решения."""
    decision_id: str
    decision_type: DecisionType
    timestamp: float = field(default_factory=time.time)
    context: str = ''                           # в какой ситуации
    question: str = ''                          # что решали
    options: list[ConsideredOption] = field(default_factory=list)
    chosen: str = ''                            # что выбрали
    reasoning: str = ''                         # почему
    confidence: float = 0.5                     # 0.0 - 1.0
    factors: list[str] = field(default_factory=list)   # факторы, повлиявшие
    risks_considered: list[str] = field(default_factory=list)
    user_id: str = ''                           # для кого решение
    outcome: str = ''                           # результат (заполняется позже)
    trace_id: str = ''                          # связь с TraceContext

    def to_dict(self) -> dict:
        return {
            'decision_id': self.decision_id,
            'type': self.decision_type.value,
            'timestamp': self.timestamp,
            'context': self.context,
            'question': self.question,
            'options': [o.to_dict() for o in self.options],
            'chosen': self.chosen,
            'reasoning': self.reasoning,
            'confidence': round(self.confidence, 3),
            'factors': self.factors,
            'risks_considered': self.risks_considered,
            'user_id': self.user_id,
            'outcome': self.outcome,
            'trace_id': self.trace_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> DecisionTrace:
        options = [
            ConsideredOption(**opt) if isinstance(opt, dict) else opt
            for opt in d.get('options', [])
        ]
        return cls(
            decision_id=d.get('decision_id', ''),
            decision_type=DecisionType(d.get('type', 'action')),
            timestamp=d.get('timestamp', time.time()),
            context=d.get('context', ''),
            question=d.get('question', ''),
            options=options,
            chosen=d.get('chosen', ''),
            reasoning=d.get('reasoning', ''),
            confidence=d.get('confidence', 0.5),
            factors=d.get('factors', []),
            risks_considered=d.get('risks_considered', []),
            user_id=d.get('user_id', ''),
            outcome=d.get('outcome', ''),
            trace_id=d.get('trace_id', ''),
        )


# ── Decision Explainer ────────────────────────────────────────────────────────

class DecisionExplainer:
    """
    Система объяснимости решений агента-партнёра.

    Каждое решение записывается с полной трассировкой:
        - что рассматривалось
        - что выбрано и почему
        - какие риски учтены
        - уровень уверенности

    Пользователь может спросить "почему ты сделал X?" и получить
    человекочитаемый ответ.

    KPI партнёра:
        - объяснимость действий → каждое решение = trace
        - trust building → прозрачность повышает доверие
        - предсказуемость → можно понять логику агента
    """

    MAX_TRACES = 500

    def __init__(self):
        self._traces: list[DecisionTrace] = []
        self._lock = threading.Lock()

    # ── Recording ─────────────────────────────────────────────────────────

    def record_decision(
        self,
        decision_type: DecisionType,
        question: str,
        chosen: str,
        reasoning: str,
        context: str = '',
        options: list[ConsideredOption] | None = None,
        confidence: float = 0.5,
        factors: list[str] | None = None,
        risks: list[str] | None = None,
        user_id: str = '',
        trace_id: str = '',
    ) -> DecisionTrace:
        """Записывает решение с полной трассировкой."""
        import uuid
        trace = DecisionTrace(
            decision_id=f"d_{uuid.uuid4().hex[:8]}",
            decision_type=decision_type,
            context=context,
            question=question,
            options=options or [],
            chosen=chosen,
            reasoning=reasoning,
            confidence=confidence,
            factors=factors or [],
            risks_considered=risks or [],
            user_id=user_id,
            trace_id=trace_id,
        )
        with self._lock:
            self._traces.append(trace)
            if len(self._traces) > self.MAX_TRACES:
                self._traces = self._traces[-self.MAX_TRACES:]
        return trace

    def record_outcome(self, decision_id: str, outcome: str):
        """Дополняет решение результатом (ретроспективно)."""
        with self._lock:
            for trace in reversed(self._traces):
                if trace.decision_id == decision_id:
                    trace.outcome = outcome
                    return True
        return False

    # ── Explanation API ───────────────────────────────────────────────────

    def explain_last(self, n: int = 1) -> list[str]:
        """Объясняет последние N решений в человекочитаемом формате."""
        with self._lock:
            recent = self._traces[-n:]
        return [self._render_explanation(t) for t in reversed(recent)]

    def explain_decision(self, decision_id: str) -> str | None:
        """Объясняет конкретное решение."""
        with self._lock:
            for trace in reversed(self._traces):
                if trace.decision_id == decision_id:
                    return self._render_explanation(trace)
        return None

    def explain_why(self, query: str) -> str:
        """Ищет решение по контексту/вопросу и объясняет."""
        query_lower = query.lower()
        best_match = None
        best_score = 0.0

        with self._lock:
            for trace in reversed(self._traces[-100:]):
                score = self._relevance_score(query_lower, trace)
                if score > best_score:
                    best_score = score
                    best_match = trace

        if best_match and best_score > 0.2:
            return self._render_explanation(best_match)
        return "Не нашёл решения, подходящего под этот вопрос."

    def get_recent_decisions(self, user_id: str = '', n: int = 10) -> list[dict]:
        """Возвращает последние решения (опционально для конкретного пользователя)."""
        with self._lock:
            traces = self._traces
            if user_id:
                traces = [t for t in traces if t.user_id == user_id]
            return [t.to_dict() for t in traces[-n:]]

    # ── Rendering ─────────────────────────────────────────────────────────

    def _render_explanation(self, trace: DecisionTrace) -> str:
        """Рендерит человекочитаемое объяснение решения."""
        parts = []

        # Заголовок
        conf = self._confidence_label(trace.confidence)
        parts.append(f"Решение ({trace.decision_type.value}, {conf}):")

        # Контекст
        if trace.context:
            parts.append(f"  Ситуация: {trace.context}")

        # Вопрос
        if trace.question:
            parts.append(f"  Вопрос: {trace.question}")

        # Варианты
        if trace.options:
            parts.append("  Рассмотренные варианты:")
            for i, opt in enumerate(trace.options, 1):
                marker = "→" if opt.description == trace.chosen else " "
                parts.append(f"    {marker} {i}. {opt.description}")
                if opt.pros:
                    parts.append(f"        +: {', '.join(opt.pros)}")
                if opt.cons:
                    parts.append(f"        -: {', '.join(opt.cons)}")
                if opt.rejected_reason:
                    parts.append(f"        Отклонено: {opt.rejected_reason}")

        # Выбор
        parts.append(f"  Выбрано: {trace.chosen}")
        parts.append(f"  Причина: {trace.reasoning}")

        # Факторы
        if trace.factors:
            parts.append(f"  Факторы: {', '.join(trace.factors)}")

        # Риски
        if trace.risks_considered:
            parts.append(f"  Учтённые риски: {', '.join(trace.risks_considered)}")

        # Результат
        if trace.outcome:
            parts.append(f"  Результат: {trace.outcome}")

        return '\n'.join(parts)

    @staticmethod
    def _confidence_label(confidence: float) -> str:
        if confidence >= 0.8:
            return "уверен"
        if confidence >= 0.5:
            return "скорее уверен"
        if confidence >= 0.3:
            return "не уверен"
        return "сомневаюсь"

    @staticmethod
    def _relevance_score(query: str, trace: DecisionTrace) -> float:
        """Простая оценка релевантности запроса к trace."""
        words = set(query.split())
        if not words:
            return 0.0
        target = f"{trace.context} {trace.question} {trace.chosen} {trace.reasoning}".lower()
        target_words = set(target.split())
        if not target_words:
            return 0.0
        overlap = len(words & target_words)
        return overlap / max(len(words), 1)

    # ── Serialization ─────────────────────────────────────────────────────

    def to_dict(self) -> list[dict]:
        with self._lock:
            return [t.to_dict() for t in self._traces]

    def load_from_list(self, data: list[dict]):
        with self._lock:
            self._traces = []
            for d in data:
                try:
                    self._traces.append(DecisionTrace.from_dict(d))
                except (KeyError, ValueError):
                    continue
