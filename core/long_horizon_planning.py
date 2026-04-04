# Long-Horizon Planning (долгосрочное планирование) — Слой 38
# Архитектура автономного AI-агента
# Планирование на неделях/месяцах: дорожные карты, зависимости, контрольные точки.


import re
import time
from enum import Enum


class PlanMetrics:
    """Количественные метрики долгосрочного плана."""

    def __init__(self):
        self.resource_efficiency: float = 0.0   # 0..1 — эффективность использования ресурсов
        self.survival_probability: float = 0.0  # 0..1 — вероятность успеха при рисках
        self.influence_score: float = 0.0        # 0..1 — влияние результата
        self.risk_score: float = 0.0             # 0..1 — суммарный риск (выше = хуже)
        self.adaptation_rate: float = 0.0        # 0..1 — насколько план адаптируем
        self.discount_factor: float = 0.9        # γ для дисконтированной полезности
        self.raw: dict = {}                      # сырые значения из LLM

    def expected_utility(self) -> float:
        reward = (
            self.resource_efficiency * 0.25
            + self.survival_probability * 0.30
            + self.influence_score * 0.25
            - self.risk_score * 0.20
        )
        return round(max(0.0, min(1.0, reward)), 3)

    def to_dict(self) -> dict:
        return {
            'resource_efficiency':  round(self.resource_efficiency, 3),
            'survival_probability': round(self.survival_probability, 3),
            'influence_score':      round(self.influence_score, 3),
            'risk_score':           round(self.risk_score, 3),
            'adaptation_rate':      round(self.adaptation_rate, 3),
            'expected_utility':     self.expected_utility(),
        }


class HorizonScale(Enum):
    DAY    = 'day'      # до 1 дня
    WEEK   = 'week'     # 1–7 дней
    MONTH  = 'month'    # 1–4 недели
    QUARTER = 'quarter' # 1–3 месяца
    YEAR   = 'year'     # > 3 месяца

    @property
    def days(self) -> int:
        return _HORIZON_DAYS[self]


_HORIZON_DAYS: dict['HorizonScale', int] = {
    HorizonScale.DAY: 1,
    HorizonScale.WEEK: 7,
    HorizonScale.MONTH: 30,
    HorizonScale.QUARTER: 90,
    HorizonScale.YEAR: 365,
}


class Milestone:
    """Контрольная точка в долгосрочном плане."""

    def __init__(self, milestone_id: str, description: str,
                 target_date: float, deliverables: list | None = None):
        self.milestone_id = milestone_id
        self.description = description
        self.target_date = target_date      # unix timestamp
        self.deliverables = deliverables or []
        self.completed = False
        self.completed_at: float | None = None
        self.progress: float = 0.0

    @property
    def days_remaining(self) -> float:
        return (self.target_date - time.time()) / 86400

    def to_dict(self):
        return {
            'milestone_id': self.milestone_id,
            'description': self.description,
            'target_date': self.target_date,
            'days_remaining': round(self.days_remaining, 1),
            'completed': self.completed,
            'progress': self.progress,
            'deliverables': self.deliverables,
        }


class Roadmap:
    """Дорожная карта — долгосрочный план достижения цели."""

    # TTL по умолчанию зависит от горизонта (horizon_days * 2, но не менее 1 дня)
    _HORIZON_TTL_MULTIPLIER = 2

    def __init__(self, roadmap_id: str, title: str, goal: str,
                 horizon: HorizonScale, start_date: float | None = None):
        self.roadmap_id = roadmap_id
        self.title = title
        self.goal = goal
        self.horizon = horizon
        self.start_date = start_date or time.time()
        self.milestones: list[Milestone] = []
        self.phases: list[dict] = []        # фазы работы
        self.risks: list[str] = []
        self.assumptions: list[str] = []
        self.status = 'active'
        self.created_at = time.time()
        self.metrics = PlanMetrics()

        self.ttl_sec: float = max(86400, horizon.days * 86400 * self._HORIZON_TTL_MULTIPLIER)

    def is_stale(self) -> bool:
        return time.time() - self.created_at > self.ttl_sec

    @property
    def overall_progress(self) -> float:
        if not self.milestones:
            return 0.0
        completed = sum(1 for m in self.milestones if m.completed)
        return completed / len(self.milestones)

    def next_milestone(self) -> Milestone | None:
        pending = [m for m in self.milestones if not m.completed]
        if not pending:
            return None
        return min(pending, key=lambda m: m.target_date)

    def to_dict(self):
        return {
            'roadmap_id': self.roadmap_id,
            'title': self.title,
            'goal': self.goal,
            'horizon': self.horizon.value,
            'status': self.status,
            'overall_progress': round(self.overall_progress, 2),
            'milestones': [m.to_dict() for m in self.milestones],
            'phases': self.phases,
            'risks': self.risks,
            'metrics': self.metrics.to_dict() if hasattr(self, 'metrics') else {},
        }


class LongHorizonPlanning:
    """
    Long-Horizon Planning System — Слой 38.

    Функции:
        - создание дорожных карт (roadmaps) для долгосрочных целей
        - разбивка на фазы и контрольные точки (milestones)
        - оценка зависимостей между задачами и рисков
        - адаптация плана при изменении обстоятельств
        - мониторинг прогресса относительно дедлайнов
        - интеграция с Goal Manager для учёта текущих целей

    Используется:
        - Goal Manager (Слой 37)         — берёт долгосрочные цели
        - Autonomous Loop (Слой 20)      — проверяет дедлайны в каждом цикле
        - Reflection System (Слой 10)    — обновляет прогресс
        - Knowledge System (Слой 2)      — сохраняет планы

    OWNERSHIP CONTRACT:
        Владеет: _roadmaps (дорожные карты, milestones, metrics).
        НЕ владеет: Goal-объектами (GoalManager), TaskGraph-ами (TaskDecomposition).
        Может предложить roadmap через offer_promotion() → GoalManager.promote_advisory().
        НЕ может напрямую создавать Goal или менять _active_goal_id.
    """

    def __init__(self, cognitive_core=None, goal_manager=None,
                 knowledge_system=None, monitoring=None):
        self.cognitive_core = cognitive_core
        self.goal_manager = goal_manager
        self.knowledge = knowledge_system
        self.monitoring = monitoring

        self._roadmaps: dict[str, Roadmap] = {}
        self._counter = 0
    # ── Сталость и очистка ─────────────────────────────────────────

    def invalidate_stale_roadmaps(self) -> int:
        """Архивирует дорожные карты, у которых истёк TTL. Возвращает количество архивированных."""
        stale_ids = [
            rm_id for rm_id, rm in self._roadmaps.items()
            if rm.status == 'active' and rm.is_stale()
        ]
        for rm_id in stale_ids:
            rm = self._roadmaps[rm_id]
            rm.status = 'stale'
            self._trace('invalidate_stale', roadmap_id=rm_id,
                        goal=rm.goal[:80], age_sec=round(time.time() - rm.created_at),
                        ttl_sec=round(rm.ttl_sec))
            self._log(f"Roadmap [{rm_id}] помечен как stale (TTL истёк)")
        return len(stale_ids)

    def invalidate_for_goal(self, old_goal: str) -> int:
        """Архивирует все активные roadmaps, привязанные к сменившейся цели."""
        invalidated = 0
        for rm in self._roadmaps.values():
            if rm.status == 'active' and rm.goal == old_goal:
                rm.status = 'invalidated'
                invalidated += 1
                self._trace('invalidate_goal_switch', roadmap_id=rm.roadmap_id,
                            old_goal=old_goal[:80])
                self._log(f"Roadmap [{rm.roadmap_id}] инвалидирован (цель изменилась)")
        return invalidated
    # ── Создание дорожных карт ────────────────────────────────────────────────

    def create_roadmap(self, title: str, goal: str,
                       horizon: HorizonScale = HorizonScale.MONTH) -> Roadmap:
        """Создаёт пустую дорожную карту. Перед созданием чистит stale-карты."""
        self.invalidate_stale_roadmaps()
        self._counter += 1
        rm_id = f"rm{self._counter:04d}"
        rm = Roadmap(roadmap_id=rm_id, title=title, goal=goal, horizon=horizon)
        self._roadmaps[rm_id] = rm
        self._log(f"Дорожная карта создана: [{rm_id}] '{title}'")
        return rm

    def plan(self, goal: str,
             horizon: HorizonScale = HorizonScale.MONTH,
             n_milestones: int = 5) -> Roadmap:
        """
        Автоматически строит дорожную карту для цели через Cognitive Core.

        Для стратегических целей использует формальный MDP-фрейминг.
        Для тактических — обычный пошаговый план.
        """
        rm = self.create_roadmap(
            title=f"Roadmap: {goal[:50]}",
            goal=goal,
            horizon=horizon,
        )

        if not self.cognitive_core:
            rm.phases = self._plan_deterministic(goal, horizon.days)
            for phase in rm.phases[:n_milestones]:
                self.add_milestone(
                    rm.roadmap_id,
                    description=phase.get('goal', 'Этап'),
                    days_from_now=max(1, float(phase.get('phase', 1))),
                    deliverables=[phase.get('duration', '')],
                )
            return rm

        horizon_days = horizon.days

        is_strategic = self._is_strategic_goal(goal)

        if is_strategic:
            prompt = self._build_strategic_prompt(goal, horizon_days, n_milestones)
        else:
            prompt = self._build_tactical_prompt(goal, horizon_days, n_milestones)

        raw = str(self.cognitive_core.reasoning(prompt))
        self._parse_roadmap(rm, raw, horizon_days)
        self._parse_metrics(rm, raw)

        quality = self._score_reasoning_quality(raw, is_strategic)
        rm.phases.append({'type': 'meta', 'reasoning_quality': quality})

        if self.knowledge:
            self.knowledge.store_long_term(
                f'roadmap_{rm.roadmap_id}', str(rm.to_dict()),
                source='planning', trust=0.7,
            )

        self._log(
            f"Roadmap [{rm.roadmap_id}]: {len(rm.milestones)} вех, "
            f"{len(rm.risks)} рисков, "
            f"EU={rm.metrics.expected_utility():.2f}, "
            f"reasoning_quality={quality:.0%}"
        )
        return rm

    def strategic_plan(self, goal: str,
                       horizon: HorizonScale = HorizonScale.QUARTER) -> Roadmap:
        """
        Формальное MDP-планирование для сложных стратегических задач.

        Требует от LLM явно определить:
          S — пространство состояний
          A — пространство действий
          T — модель переходов (описание)
          R — функцию награды с метриками
          π — алгоритм принятия решений
          Δ — механизм адаптации при изменении среды
        """
        rm = self.create_roadmap(
            title=f"[STRATEGY] {goal[:50]}",
            goal=goal,
            horizon=horizon,
        )

        if not self.cognitive_core:
            rm.phases = self._plan_deterministic(goal, horizon.days)
            return rm

        horizon_days = horizon.days

        prompt = (
            f"Ты — автономный агент. Тебе нужна формальная стратегия:\n\n"
            f"СТРАТЕГИЧЕСКАЯ ЦЕЛЬ: {goal}\n"
            f"ГОРИЗОНТ: {horizon_days} дней\n\n"
            f"Ответ ОБЯЗАН содержать следующие разделы:\n\n"
            f"## МОДЕЛЬ СРЕДЫ (MDP)\n"
            f"СОСТОЯНИЯ (S): <перечисли 3–5 ключевых состояний среды>\n"
            f"ДЕЙСТВИЯ (A): <перечисли 3–5 доступных действий агента>\n"
            f"ПЕРЕХОДЫ (T): <как каждое действие меняет состояние>\n"
            f"НАГРАДА (R): <формула или словесное описание функции полезности>\n\n"
            f"## АЛГОРИТМ ПРИНЯТИЯ РЕШЕНИЙ\n"
            f"АЛГОРИТМ: <MDP/Bandit/MCTS/Greedy/другой — выбери и обоснуй>\n"
            f"ПСЕВДОКОД:\n<5–10 строк псевдокода цикла принятия решений>\n\n"
            f"## МЕТРИКИ\n"
            f"RESOURCE_EFFICIENCY: <число 0..1 — оценка эффективности>\n"
            f"SURVIVAL_PROBABILITY: <число 0..1 — вероятность успеха>\n"
            f"INFLUENCE_SCORE: <число 0..1 — влияние>\n"
            f"RISK_SCORE: <число 0..1 — суммарный риск>\n"
            f"ADAPTATION_RATE: <число 0..1 — гибкость стратегии>\n\n"
            f"## ПЛАН ДЕЙСТВИЙ\n"
            f"ЭТАП: <название> | СРОК: <дней от начала> | РЕЗУЛЬТАТ: <deliverable>\n"
            f"(повторить для каждого этапа, минимум 4)\n\n"
            f"## РИСКИ И АДАПТАЦИЯ\n"
            f"РИСК: <формулировка> — ВЕРОЯТНОСТЬ: <0..1> — МИТИГАЦИЯ: <действие>\n"
            f"ТРИГГЕР_АДАПТАЦИИ: <условие смены стратегии>\n"
            f"ДОПУЩЕНИЕ: <принятое допущение>\n"
        )

        raw = str(self.cognitive_core.reasoning(prompt))
        self._parse_roadmap(rm, raw, horizon_days)
        self._parse_metrics(rm, raw)
        self._parse_risks_extended(rm, raw)

        quality = self._score_reasoning_quality(raw, is_strategic=True)
        rm.phases.append({
            'type': 'meta',
            'reasoning_quality': quality,
            'mode': 'strategic_mdp',
        })

        if self.knowledge:
            self.knowledge.store_long_term(
                f'roadmap_{rm.roadmap_id}', str(rm.to_dict()),
                source='planning', trust=0.7,
            )

        self._log(
            f"[STRATEGY] Roadmap [{rm.roadmap_id}]: "
            f"EU={rm.metrics.expected_utility():.2f}, quality={quality:.0%}"
        )
        return rm

    def evaluate_plan(self, roadmap_id: str) -> dict:
        """
        Оценивает качество существующего плана по формальным критериям:
          - наличие метрик
          - наличие алгоритма принятия решений
          - полнота рисков
          - адаптируемость
        Возвращает dict с оценками и рекомендациями.
        """
        rm = self._roadmaps.get(roadmap_id)
        if not rm:
            return {'error': f'Roadmap {roadmap_id} не найден'}

        issues = []
        score = 1.0

        if rm.metrics.expected_utility() == 0.0:
            issues.append('Отсутствуют количественные метрики (EU=0)')
            score -= 0.3

        if not rm.risks:
            issues.append('Нет идентифицированных рисков')
            score -= 0.2

        if not rm.assumptions:
            issues.append('Нет явных допущений')
            score -= 0.1

        has_algorithm = any(
            'алгоритм' in str(p).lower() or 'pseudocode' in str(p).lower()
            for p in rm.phases
        )
        if not has_algorithm:
            issues.append('Нет формального алгоритма принятия решений')
            score -= 0.2

        if len(rm.milestones) < 3:
            issues.append('Недостаточно контрольных точек (< 3)')
            score -= 0.1

        meta = next((p for p in rm.phases if isinstance(p, dict) and p.get('type') == 'meta'), {})
        rq = meta.get('reasoning_quality', 0.0)
        if rq < 0.5:
            issues.append(f'Низкое качество рассуждения: {rq:.0%}')
            score -= 0.1

        return {
            'roadmap_id': roadmap_id,
            'plan_quality': round(max(0.0, score), 2),
            'expected_utility': rm.metrics.expected_utility(),
            'reasoning_quality': rq,
            'issues': issues,
            'recommendation': (
                'Используйте strategic_plan() для формального MDP-планирования'
                if score < 0.6 else 'План удовлетворительный'
            ),
        }

    def adapt(self, roadmap_id: str, change_description: str) -> Roadmap | None:
        """
        Адаптирует существующий план под изменившиеся обстоятельства.
        """
        rm = self._roadmaps.get(roadmap_id)
        if not rm or not self.cognitive_core:
            return rm

        current_plan = str(rm.to_dict())
        raw = str(self.cognitive_core.reasoning(
            f"Существующий план:\n{current_plan}\n\n"
            f"Произошло изменение: {change_description}\n\n"
            f"Что нужно скорректировать в плане? "
            f"Укажи в формате КОРРЕКТИРОВКА: <описание изменения>."
        ))

        corrections = re.findall(r'КОРРЕКТИРОВКА[:\s]+(.+)', raw, re.IGNORECASE)
        for c in corrections:
            rm.phases.append({'type': 'adaptation', 'note': c.strip(),
                               'at': time.time()})
        self._log(f"Roadmap [{roadmap_id}] адаптирован: {len(corrections)} корректировок")
        return rm

    # ── Контрольные точки ─────────────────────────────────────────────────────

    def add_milestone(self, roadmap_id: str, description: str,
                      days_from_now: float, deliverables: list | None = None) -> Milestone | None:
        """Добавляет контрольную точку к дорожной карте."""
        rm = self._roadmaps.get(roadmap_id)
        if not rm:
            return None
        ms_id = f"{roadmap_id}_m{len(rm.milestones)+1:02d}"
        ms = Milestone(
            milestone_id=ms_id,
            description=description,
            target_date=time.time() + days_from_now * 86400,
            deliverables=deliverables or [],
        )
        rm.milestones.append(ms)
        return ms

    def complete_milestone(self, roadmap_id: str, milestone_id: str):
        """Отмечает контрольную точку как выполненную."""
        rm = self._roadmaps.get(roadmap_id)
        if not rm:
            return
        for ms in rm.milestones:
            if ms.milestone_id == milestone_id:
                ms.completed = True
                ms.completed_at = time.time()
                self._log(f"Веха выполнена: [{milestone_id}]")
                break

    # ── Мониторинг ────────────────────────────────────────────────────────────

    def check_deadlines(self, warn_days: float = 7.0) -> list[dict]:
        """
        Проверяет приближающиеся дедлайны.
        Возвращает список вех со сроком менее warn_days дней.
        """
        warnings = []
        for rm in self._roadmaps.values():
            if rm.status != 'active':
                continue
            for ms in rm.milestones:
                if not ms.completed and ms.days_remaining <= warn_days:
                    warnings.append({
                        'roadmap_id': rm.roadmap_id,
                        'roadmap_title': rm.title,
                        'milestone_id': ms.milestone_id,
                        'description': ms.description,
                        'days_remaining': round(ms.days_remaining, 1),
                        'overdue': ms.days_remaining < 0,
                    })
        if warnings:
            self._log(f"Предупреждений о дедлайнах: {len(warnings)}")
        return warnings

    def review_progress(self) -> list[dict]:
        """Обзор прогресса по всем активным дорожным картам."""
        return [
            {
                'roadmap_id': rm.roadmap_id,
                'title': rm.title,
                'progress': rm.overall_progress,
                'next_milestone': (nm.to_dict() if (nm := rm.next_milestone()) else None),
                'horizon': rm.horizon.value,
            }
            for rm in self._roadmaps.values()
            if rm.status == 'active'
        ]

    # ── Реестр ────────────────────────────────────────────────────────────────

    def get_roadmap(self, roadmap_id: str) -> Roadmap | None:
        return self._roadmaps.get(roadmap_id)

    def list_roadmaps(self) -> list[dict]:
        return [rm.to_dict() for rm in self._roadmaps.values()]

    def summary(self) -> dict:
        active = [rm for rm in self._roadmaps.values() if rm.status == 'active']
        return {
            'total_roadmaps': len(self._roadmaps),
            'active': len(active),
            'avg_progress': round(
                sum(rm.overall_progress for rm in active) / max(1, len(active)), 2
            ),
            'deadline_warnings': len(self.check_deadlines()),
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _add_default_milestones(self, rm: Roadmap, n: int):
        total_days = rm.horizon.days
        for i in range(1, n + 1):
            days = total_days * i / n
            self.add_milestone(
                rm.roadmap_id,
                description=f"Этап {i}: {rm.goal[:40]}",
                days_from_now=days,
            )

    def _parse_roadmap(self, rm: Roadmap, raw: str, horizon_days: int):
        stages = re.findall(r'ЭТАП[:\s]+(.+)', raw, re.IGNORECASE)
        deadlines = re.findall(r'СРОК[:\s]+(\d+)', raw, re.IGNORECASE)
        results = re.findall(r'РЕЗУЛЬТАТ[:\s]+(.+)', raw, re.IGNORECASE)
        risks = re.findall(r'РИСК[:\s]+(.+)', raw, re.IGNORECASE)
        assumptions = re.findall(r'ДОПУЩЕНИЕ[:\s]+(.+)', raw, re.IGNORECASE)

        for i, stage in enumerate(stages):
            days = int(deadlines[i]) if i < len(deadlines) else horizon_days * (i + 1) / max(1, len(stages))
            deliverables = [results[i].strip()] if i < len(results) else []
            self.add_milestone(rm.roadmap_id, stage.strip(), days, deliverables)

        rm.risks = [r.strip() for r in risks]
        rm.assumptions = [a.strip() for a in assumptions]

    def _plan_deterministic(self, goal: str, horizon: int) -> list[dict]:
        """
        Deterministic planning algorithm without LLM.

        Args:
            goal    — goal description string
            horizon — number of days for the planning horizon

        Returns:
            List of phase dicts with keys: phase, goal, duration, dependencies, priority
        """
        if horizon <= 7:
            # Daily plan
            steps = ['research', 'plan', 'execute', 'verify']
            duration_label = '1 day'
        elif horizon <= 30:
            # Weekly milestones
            steps = ['understand', 'design', 'implement', 'test', 'deploy']
            duration_label = '1 week'
        else:
            # Monthly phases
            steps = ['research', 'prototype', 'develop', 'refine', 'release']
            duration_label = '1 month'

        phases = []
        for i, step in enumerate(steps):
            deps = [i] if i > 0 else []  # each phase depends on the previous
            phases.append({
                'phase': i + 1,
                'goal': f"{step}: {goal}",
                'duration': duration_label,
                'dependencies': deps,
                'priority': len(steps) - i,  # first phases have higher priority
            })
        return phases

    # ── Planning helpers ──────────────────────────────────────────────────────

    def _is_strategic_goal(self, goal: str) -> bool:
        """True если цель стратегическая: вовлекает рынок, конкурентов, платформы,
        долгосрочное позиционирование или длинная (> 120 символов)."""
        keywords = (
            'стратег', 'долгосроч', 'платформ', 'масштаб', 'конкурент',
            'рынок', 'архитектур', 'экосистем', 'позиционир',
            'strategy', 'long-term', 'competitive', 'market', 'platform',
            'scale', 'ecosystem',
        )
        return len(goal) > 120 or any(k in goal.lower() for k in keywords)

    def _build_tactical_prompt(self, goal: str, horizon_days: int,
                                n_milestones: int) -> str:
        return (
            f"Составь конкретный план достижения цели за {horizon_days} дней.\n\n"
            f"Цель: {goal}\n\n"
            f"Для каждого из {n_milestones} этапов укажи:\n"
            f"ЭТАП: <название>\n"
            f"СРОК: <дней от начала — целое число>\n"
            f"РЕЗУЛЬТАТ: <что готово>\n\n"
            f"Добавь:\n"
            f"РИСК: <возможный риск>\n"
            f"ДОПУЩЕНИЕ: <принятое допущение>\n"
            f"RESOURCE_EFFICIENCY: <0..1>\n"
            f"SURVIVAL_PROBABILITY: <0..1>\n"
            f"INFLUENCE_SCORE: <0..1>\n"
            f"RISK_SCORE: <0..1>\n"
        )

    def _build_strategic_prompt(self, goal: str, horizon_days: int,
                                 n_milestones: int) -> str:
        return (
            f"Ты — автономный агент. Составь стратегический план.\n\n"
            f"ЦЕЛЬ: {goal}\n"
            f"ГОРИЗОНТ: {horizon_days} дней\n\n"
            f"## СРЕДА (опиши кратко)\n"
            f"СОСТОЯНИЯ (S): <3–5 состояний>\n"
            f"ДЕЙСТВИЯ (A): <3–5 действий>\n"
            f"НАГРАДА (R): <как измеряем успех>\n\n"
            f"## АЛГОРИТМ\n"
            f"АЛГОРИТМ: <MDP/Bandit/Greedy — выбери>\n"
            f"ПСЕВДОКОД: <5–8 строк цикла принятия решений>\n\n"
            f"## МЕТРИКИ\n"
            f"RESOURCE_EFFICIENCY: <0..1>\n"
            f"SURVIVAL_PROBABILITY: <0..1>\n"
            f"INFLUENCE_SCORE: <0..1>\n"
            f"RISK_SCORE: <0..1>\n"
            f"ADAPTATION_RATE: <0..1>\n\n"
            f"## ПЛАН\n"
            f"(минимум {n_milestones} этапов)\n"
            f"ЭТАП: <название> | СРОК: <дней> | РЕЗУЛЬТАТ: <deliverable>\n\n"
            f"## РИСКИ\n"
            f"РИСК: <риск>\n"
            f"ВЕРОЯТНОСТЬ: <0..1>\n"
            f"МИТИГАЦИЯ: <действие>\n"
            f"ТРИГГЕР_АДАПТАЦИИ: <условие>\n"
            f"ДОПУЩЕНИЕ: <допущение>\n"
        )

    def _parse_metrics(self, rm: Roadmap, raw: str):
        """Извлекает количественные метрики из сырого текста LLM."""
        mapping = {
            'RESOURCE_EFFICIENCY': 'resource_efficiency',
            'SURVIVAL_PROBABILITY': 'survival_probability',
            'INFLUENCE_SCORE': 'influence_score',
            'RISK_SCORE': 'risk_score',
            'ADAPTATION_RATE': 'adaptation_rate',
        }
        for label, attr in mapping.items():
            m = re.search(rf'{label}[:\s]+([\d.]+)', raw, re.IGNORECASE)
            if m:
                try:
                    setattr(rm.metrics, attr, min(1.0, max(0.0, float(m.group(1)))))
                except ValueError:
                    pass

    def _parse_risks_extended(self, rm: Roadmap, raw: str):
        """Парсит расширенный формат рисков: РИСК + ВЕРОЯТНОСТЬ + МИТИГАЦИЯ."""
        blocks = re.findall(
            r'РИСК[:\s]+(.+?)(?=РИСК:|$)',
            raw, re.IGNORECASE | re.DOTALL
        )
        for block in blocks:
            desc_m = re.match(r'(.+)', block.strip())
            prob_m = re.search(r'ВЕРОЯТНОСТЬ[:\s]+([\d.]+)', block, re.IGNORECASE)
            mit_m = re.search(r'МИТИГАЦИЯ[:\s]+(.+)', block, re.IGNORECASE)
            if desc_m:
                entry = desc_m.group(1).strip()
                if prob_m:
                    entry += f' [p={prob_m.group(1)}]'
                if mit_m:
                    entry += f' → {mit_m.group(1).strip()}'
                if entry not in rm.risks:
                    rm.risks.append(entry)

        triggers = re.findall(r'ТРИГГЕР_АДАПТАЦИИ[:\s]+(.+)', raw, re.IGNORECASE)
        for t in triggers:
            rm.phases.append({'type': 'adaptation_trigger', 'condition': t.strip()})

    def _score_reasoning_quality(self, raw: str, is_strategic: bool) -> float:
        """
        Оценивает качество рассуждения LLM по наличию формальных компонентов.
        Возвращает 0..1.
        """
        raw_lower = raw.lower()
        score = 0.0

        # Базовые компоненты: этапы и риски всегда ожидаются
        if re.search(r'этап[:\s]', raw_lower):
            score += 0.2
        if re.search(r'риск[:\s]', raw_lower):
            score += 0.15
        if re.search(r'результат[:\s]', raw_lower):
            score += 0.15

        # Метрики
        has_metrics = bool(re.search(
            r'(resource_efficiency|survival_probability|influence_score|risk_score)',
            raw_lower
        ))
        if has_metrics:
            score += 0.2

        if is_strategic:
            # Формальная модель среды
            if re.search(r'состояни[яе][:\s(]|action[s\s]|s\s*=\s*[{<(]', raw_lower):
                score += 0.15
            # Алгоритм
            if re.search(r'(mdp|markov|bandit|mcts|greedy|алгоритм)[:\s]', raw_lower):
                score += 0.1
            # Адаптация
            if re.search(r'(триггер|if\s+|когда\s+.{0,20}:)', raw_lower):
                score += 0.05

        return round(min(1.0, score), 2)

    def _log(self, message: str):
        if self.monitoring:
            self.monitoring.info(message, source='long_horizon_planning')
        else:
            print(f"[LongHorizonPlanning] {message}")

    def _trace(self, action: str, **ctx):
        """Structured traceability: почему было принято именно это решение."""
        entry = {'ts': time.time(), 'layer': 38, 'component': 'LongHorizonPlanning',
                 'action': action, **ctx}
        self._log(f"[TRACE] {entry}")

    def offer_promotion(self, roadmap_id: str) -> dict | None:
        """
        PROMOTION RULE: предлагает roadmap для промоушена в Goal.

        НЕ создаёт Goal самостоятельно — возвращает dict для
        GoalManager.promote_advisory().

        Returns:
            dict с полями roadmap если подходит, None если нет.
        """
        rm = self._roadmaps.get(roadmap_id)
        if not rm or rm.status != 'active':
            return None
        if rm.is_stale():
            rm.status = 'stale'
            self._trace('offer_reject', roadmap_id=roadmap_id, reason='stale')
            return None
        eu = rm.metrics.expected_utility()
        self._trace('offer_promotion', roadmap_id=roadmap_id, eu=eu,
                    goal=rm.goal[:80])
        return rm.to_dict()


# Alias for compatibility
LongHorizonPlanner = LongHorizonPlanning
