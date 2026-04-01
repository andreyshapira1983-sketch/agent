# Self-Improvement System (система саморазвития) — Слой 12
# Архитектура автономного AI-агента
# Оптимизация алгоритмов, улучшение стратегий, создание новых инструментов, обновление архитектуры.
#
# SECURITY (VULN-15): Стратегии санитизируются при сохранении.
# LLM не может внедрить вредоносные инструкции через стратегии.
# pylint: disable=broad-except


import re
import time


class ImprovementProposal:
    """Предложение по улучшению системы."""

    def __init__(self, area: str, current_behavior: str, proposed_change: str,
                 rationale: str, priority: int = 2):
        self.area = area                         # какой компонент/слой улучшается
        self.current_behavior = current_behavior # как сейчас
        self.proposed_change = proposed_change   # что предлагается
        self.rationale = rationale               # обоснование
        self.priority = priority                 # 1=низкий, 2=средний, 3=высокий
        self.status = 'proposed'                 # proposed | approved | applied | rejected
        self.created_at = time.time()
        self.result: str | None = None

    def to_dict(self):
        return {
            'area': self.area,
            'current_behavior': self.current_behavior,
            'proposed_change': self.proposed_change,
            'rationale': self.rationale,
            'priority': self.priority,
            'status': self.status,
            'result': self.result,
        }


class SelfImprovementSystem:
    """
    Self-Improvement System — Слой 12.

    Функции:
        - оптимизация алгоритмов на основе данных рефлексии
        - улучшение стратегий поведения агента
        - генерация предложений по созданию новых инструментов
        - обновление архитектурных решений
        - отслеживание прогресса улучшений

    Связан с:
        - Reflection System (Слой 10) — источник инсайтов для улучшений
        - Learning System (Слой 9)   — новые знания → новые стратегии
        - Knowledge System (Слой 2)  — хранение улучшенных стратегий
        - Human Approval (Слой 22)   — одобрение крупных изменений
        - Cognitive Core (Слой 3)    — генерация предложений
    """

    def __init__(
        self,
        cognitive_core=None,
        reflection_system=None,
        knowledge_system=None,
        human_approval=None,
        monitoring=None,
        sandbox=None,
        auto_apply: bool = False,
    ):
        """
        Args:
            auto_apply -- автоматически применять предложения (только для низкоприоритетных)
            sandbox    -- SandboxLayer для тестирования новых стратегий до применения
        """
        self.cognitive_core = cognitive_core
        self.reflection = reflection_system
        self.knowledge = knowledge_system
        self.human_approval = human_approval
        self.monitoring = monitoring
        self.sandbox = sandbox
        self.auto_apply = auto_apply

        self._proposals: list[ImprovementProposal] = []
        self._applied: list[dict] = []
        self._strategy_store: dict[str, str] = {}  # area → текущая лучшая стратегия
        self._performance_history: dict[str, list] = {}  # area → последние N метрик
        # Откат: area → предыдущая стратегия (до последнего apply)
        self._strategy_rollback: dict[str, str] = {}
        # Базовый success_rate на момент apply (для self-assessment)
        self._strategy_baseline: dict[str, float] = {}
        # Стратегии, реально запрошенные в текущем цикле
        self._strategies_consulted: set[str] = set()

    # ── Генерация предложений ─────────────────────────────────────────────────

    def analyse_and_propose(self, max_proposals: int = 3) -> list[ImprovementProposal]:
        """
        Анализирует накопленные данные рефлексии и генерирует предложения.
        Вызывается автоматически из Autonomous Loop или вручную.
        max_proposals -- максимальное число новых предложений за один вызов.
        """
        proposals = []

        # Собираем инсайты из Reflection System
        insights = self.reflection.get_insights() if self.reflection else []
        insights = list(insights) + self._collect_solver_case_insights()
        if not insights:
            self._log("Нет инсайтов для анализа. Пропуск.")
            return proposals

        # Уже применённые области (не ещё раз создаём для них предложения)
        pending_areas = {
            p.area for p in self._proposals if p.status == 'proposed'
        }

        for insight in insights[-10:]:
            if len(proposals) >= max_proposals:
                break
            proposal = self._generate_proposal(insight)
            if not proposal:
                continue
            # Пропускаем дубликаты: одна активная стратегия на область
            if proposal.area in pending_areas:
                continue
            # Одно применение в последних 3 циклах — пропускаем
            recent_applied = [
                p for p in self._applied[-9:]
                if p.get('area') == proposal.area
            ]
            if len(recent_applied) >= 1:
                continue
            self._proposals.append(proposal)
            proposals.append(proposal)
            pending_areas.add(proposal.area)
            change_text = " ".join(str(proposal.proposed_change).split())
            self._log(f"Предложение сгенерировано: [{proposal.area}] {change_text}")

        return proposals

    def propose(self, area: str, current_behavior: str, proposed_change: str,
                rationale: str, priority: int = 2) -> ImprovementProposal:
        """Вручную создаёт предложение по улучшению."""
        proposal = ImprovementProposal(
            area=area,
            current_behavior=current_behavior,
            proposed_change=proposed_change,
            rationale=rationale,
            priority=priority,
        )
        self._proposals.append(proposal)
        self._log(f"Предложение добавлено вручную: [{area}]")
        return proposal

    # ── Применение улучшений ──────────────────────────────────────────────────

    def apply(self, proposal: ImprovementProposal) -> bool:
        """
        Применяет предложение.
        Высокоприоритетные (priority=3) всегда требуют Human Approval.
        Перед применением — тест в sandbox если подключён.
        """
        # Тест стратегии в sandbox перед применением
        if self.sandbox:
            from environment.sandbox import SandboxResult
            run = self.sandbox.test_strategy(
                strategy=proposal.proposed_change[:300],
                test_cases=[{
                    'scenario': {
                        'area': proposal.area,
                        'current': proposal.current_behavior[:200],
                    },
                    'expected_outcome': '',  # пустой expected = проверяем только на UNSAFE
                }]
            )
            passed = run.get('passed', 0)
            total  = run.get('total', 0)
            unsafe_runs = [r for r in run.get('runs', [])
                           if r.get('run', {}).get('verdict') == SandboxResult.UNSAFE.value]
            if unsafe_runs:
                proposal.status = 'rejected'
                self._log(f"Предложение [{proposal.area}] отклонено sandbox (UNSAFE).")
                return False
            if total > 0 and passed == 0:
                proposal.status = 'rejected'
                self._log(f"Предложение [{proposal.area}] отклонено sandbox (0/{total} тестов пройдено).")
                return False
            self._log(f"Предложение [{proposal.area}] прошло проверку sandbox "
                      f"({passed}/{total} тестов).")

        if proposal.priority >= 3 and self.human_approval:
            approved = self.human_approval.request_approval(
                'self_improvement',
                f"Область: {proposal.area}\n"
                f"Текущее поведение: {proposal.current_behavior}\n"
                f"Предлагаемое изменение: {proposal.proposed_change}\n"
                f"Обоснование: {proposal.rationale}"
            )
            if not approved:
                proposal.status = 'rejected'
                self._log(f"Предложение [{proposal.area}] отклонено человеком.")
                return False

        # SECURITY (VULN-15): Санитизация стратегии перед сохранением
        safe_strategy = self._sanitize_strategy(proposal.proposed_change)

        # Сохраняем предыдущую стратегию как кандидата для отката
        old_strategy = self._strategy_store.get(proposal.area)
        if old_strategy:
            self._strategy_rollback[proposal.area] = old_strategy
        # Запоминаем базовый success_rate на момент применения
        hist = self._performance_history.get(proposal.area, [])
        if hist:
            self._strategy_baseline[proposal.area] = float(
                hist[-1].get('success_rate', 0.5)
            )

        # Сохраняем улучшенную стратегию в Knowledge System
        if self.knowledge:
            self.knowledge.store_long_term(
                f"strategy:{proposal.area}",
                safe_strategy,
                source='self_improvement', trust=0.6,
            )
        self._strategy_store[proposal.area] = safe_strategy

        proposal.status = 'applied'
        proposal.result = f"Стратегия для '{proposal.area}' обновлена."
        self._applied.append({
            'area': proposal.area,
            'change': proposal.proposed_change[:200],
            'priority': proposal.priority,
        })
        self._log(f"Улучшение применено: [{proposal.area}]")
        return True

    def apply_all_pending(self) -> list[bool]:
        """Применяет все предложения со статусом 'proposed'."""
        pending = [p for p in self._proposals if p.status == 'proposed']
        results = []
        for p in sorted(pending, key=lambda x: -x.priority):
            if self.auto_apply and p.priority <= 2:
                results.append(self.apply(p))
            elif p.priority >= 3:
                results.append(self.apply(p))
            else:
                self._log(f"Предложение [{p.area}] ожидает ручного применения (auto_apply=False)")
        return results

    # ── Оптимизация стратегий ─────────────────────────────────────────────────

    def get_strategy(self, area: str) -> str | None:
        """Возвращает текущую лучшую стратегию для области."""
        # Сначала из локального кэша
        if area in self._strategy_store:
            self._strategies_consulted.add(area)
            return self._strategy_store[area]
        # Потом из Knowledge System
        if self.knowledge:
            result = self.knowledge.get_long_term(f"strategy:{area}")
            if result:
                self._strategies_consulted.add(area)
            return result
        return None

    def pop_consulted(self) -> set[str]:
        """Возвращает и сбрасывает набор стратегий, запрошенных в этом цикле."""
        consulted = self._strategies_consulted.copy()
        self._strategies_consulted.clear()
        return consulted

    def optimise_strategy(self, area: str, performance_data: dict) -> str | None:
        """
        Улучшает стратегию для конкретной области на основе данных о производительности.
        Накапливает историю метрик — LLM видит прогресс, а не только текущий момент.

        Args:
            area             — область (например 'planning', 'research', 'coding')
            performance_data — метрики: {'success_rate': 0.7, 'avg_time': 12.3, ...}
        """
        if not self.cognitive_core:
            return None

        # Накапливаем историю метрик (последние 5 записей)
        hist = self._performance_history.setdefault(area, [])
        hist.append(performance_data)
        if len(hist) > 5:
            hist.pop(0)

        # ── Self-assessment: сравниваем метрики до и после последнего apply ──
        # Если накоплено 4+ записей и последние 2 хуже базового → откат
        if len(hist) >= 4 and area in self._strategy_baseline:
            baseline_sr = self._strategy_baseline[area]
            recent_sr = sum(
                h.get('success_rate', 0.5) for h in hist[-2:]
            ) / 2
            if recent_sr < baseline_sr - 0.15 and area in self._strategy_rollback:
                old = self._strategy_rollback[area]
                self._strategy_store[area] = old
                if self.knowledge:
                    self.knowledge.store_long_term(
                        f'strategy:{area}', old,
                        source='self_improvement', trust=0.8,
                    )
                del self._strategy_rollback[area]
                del self._strategy_baseline[area]
                self._log(
                    f"[self_assess] Стратегия '{area}' деградировала "
                    f"(baseline={baseline_sr:.0%} → текущий={recent_sr:.0%}) — "
                    f"откатываюсь к предыдущей."
                )
                return None  # откат — не генерируем новый proposal сейчас

        current = self.get_strategy(area) or 'стратегия не определена'
        history_text = ''
        if len(hist) > 1:
            history_text = '\nИстория производительности (от старого к новому):\n' + '\n'.join(
                f"  #{i+1}: {d}" for i, d in enumerate(hist[:-1])
            )
        new_strategy = self.cognitive_core.strategy_generator(
            f"Область: {area}\n"
            f"Текущая стратегия: {current}{history_text}\n"
            f"Текущие метрики: {performance_data}\n"
            f"Предложи КОНКРЕТНУЮ улучшенную стратегию с учётом динамики."
        )

        proposal = self.propose(
            area=area,
            current_behavior=current,
            proposed_change=new_strategy,
            rationale=f"Оптимизация на основе метрик: {performance_data}",
            priority=2,
        )
        if self.auto_apply:
            self.apply(proposal)

        return new_strategy

    # ── История ───────────────────────────────────────────────────────────────

    def get_proposals(self, status: str | None = None) -> list[dict]:
        if status:
            return [p.to_dict() for p in self._proposals if p.status == status]
        return [p.to_dict() for p in self._proposals]

    def get_applied(self) -> list[dict]:
        return list(self._applied)

    def summary(self) -> dict:
        from collections import Counter
        statuses = Counter(p.status for p in self._proposals)
        return {
            'total_proposals': len(self._proposals),
            'applied': statuses.get('applied', 0),
            'pending': statuses.get('proposed', 0),
            'rejected': statuses.get('rejected', 0),
            'strategies_stored': len(self._strategy_store),
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _sanitize_strategy(strategy: str) -> str:
        """
        SECURITY (VULN-15): Санитизация стратегий — удаляет вредоносные паттерны.
        LLM может попытаться внедрить инструкции через стратегии, которые
        потом подставляются в промпты.
        """
        if not strategy:
            return strategy

        # Паттерны prompt injection в стратегиях
        injection_patterns = [
            re.compile(r'ignore\s+(all\s+)?previous\s+instructions', re.IGNORECASE),
            re.compile(r'disregard\s+(all\s+)?prior', re.IGNORECASE),
            re.compile(r'you\s+are\s+now\s+', re.IGNORECASE),
            re.compile(r'system\s*:\s*', re.IGNORECASE),
            re.compile(r'override\s+safety', re.IGNORECASE),
            re.compile(r'bypass\s+(?:security|approval|governance)', re.IGNORECASE),
            re.compile(r'disable\s+(?:safe_mode|monitoring|approval)', re.IGNORECASE),
            re.compile(r'auto_approve', re.IGNORECASE),
            re.compile(r'exec\s*\(', re.IGNORECASE),
            re.compile(r'__import__\s*\(', re.IGNORECASE),
            re.compile(r'subprocess', re.IGNORECASE),
            re.compile(r'os\.system', re.IGNORECASE),
            re.compile(r'rm\s+-rf', re.IGNORECASE),
            re.compile(r'curl\s+', re.IGNORECASE),
        ]

        for pattern in injection_patterns:
            strategy = pattern.sub('[SANITIZED]', strategy)

        # Ограничиваем длину стратегии (защита от token stuffing)
        if len(strategy) > 2000:
            strategy = strategy[:2000] + '...[TRUNCATED]'

        return strategy

    def _generate_proposal(self, insight: str) -> ImprovementProposal | None:
        if not self.cognitive_core:
            return None
        try:
            raw = self.cognitive_core.reasoning(
                f"На основе этого инсайта предложи конкретное улучшение системы:\n{insight}\n\n"
                f"Ответь строго в формате:\n"
                f"ОБЛАСТЬ: <название компонента>\n"
                f"СЕЙЧАС: <текущее поведение>\n"
                f"ИЗМЕНЕНИЕ: <что предлагается>\n"
                f"ОБОСНОВАНИЕ: <почему это улучшит работу>"
            )
            lines = {line.split(':', 1)[0].strip(): line.split(':', 1)[1].strip()
                     for line in str(raw).splitlines() if ':' in line}
            return ImprovementProposal(
                area=lines.get('ОБЛАСТЬ', 'general'),
                current_behavior=lines.get('СЕЙЧАС', ''),
                proposed_change=lines.get('ИЗМЕНЕНИЕ', str(raw)[:200]),
                rationale=lines.get('ОБОСНОВАНИЕ', insight),
                priority=2,
            )
        except Exception:  # pylint: disable=broad-except
            return None

    def _collect_solver_case_insights(self) -> list[str]:
        brain = getattr(self.cognitive_core, 'persistent_brain', None)
        if not brain:
            return []

        try:
            recent_cases = brain.get_recent_solver_cases(12)
            recent_journal = brain.get_recent_exact_solver_journal(12)
        except Exception:
            return []

        insights: list[str] = []
        verification_failures: dict[str, int] = {}
        low_confidence: dict[str, int] = {}
        rejected_challengers: dict[str, int] = {}

        for case in recent_cases:
            solver = str(case.get('solver', 'unknown'))
            if not case.get('verification', False) or str(case.get('error', '')).strip():
                verification_failures[solver] = verification_failures.get(solver, 0) + 1
            if float(case.get('confidence', 0.0) or 0.0) < 0.75:
                low_confidence[solver] = low_confidence.get(solver, 0) + 1

        for entry in recent_journal:
            if str(entry.get('decision', '')).lower() == 'reject':
                challenger = str(entry.get('challenger', 'unknown'))
                rejected_challengers[challenger] = rejected_challengers.get(challenger, 0) + 1

        for solver, count in verification_failures.items():
            insights.append(
                f"Solver-memory: у solver '{solver}' {count} недавних провалов верификации; "
                f"нужно усилить детерминированную проверку и fallback-путь."
            )
        for solver, count in low_confidence.items():
            insights.append(
                f"Solver-memory: у solver '{solver}' {count} недавних низкоуверенных результатов; "
                f"нужно улучшить parser, confidence calibration или baseline verification."
            )
        for challenger, count in rejected_challengers.items():
            insights.append(
                f"Champion/challenger-memory: challenger '{challenger}' отклонялся {count} раз; "
                f"нужно пересмотреть критерии продвижения или условия запуска challenger."
            )

        return insights[:6]

    def _log(self, message: str):
        if self.monitoring:
            self.monitoring.info(message, source='self_improvement')
        else:
            print(f"[SelfImprovement] {message}")
