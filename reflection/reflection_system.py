# Reflection System (система самоанализа) — Слой 10
# Архитектура автономного AI-агента
# Проверка результатов, анализ ошибок, улучшение решений, оптимизация действий.


class ReflectionSystem:
    """
    Reflection System — Слой 10.

    Функции:
        - проверка результатов: достигнута ли цель?
        - анализ ошибок: что пошло не так и почему?
        - улучшение решений: как сделать лучше в следующий раз?
        - оптимизация действий: поиск более эффективных путей
        - накопление инсайтов для Self-Improvement (Слой 12)

    Используется:
        - Autonomous Loop (Слой 20) — фаза evaluate
        - Self-Improvement (Слой 12) — источник инсайтов
        - Knowledge System (Слой 2) — сохранение уроков
    """

    def __init__(self, cognitive_core=None, knowledge_system=None, monitoring=None):
        self.cognitive_core = cognitive_core
        self.knowledge = knowledge_system
        self.monitoring = monitoring

        self._reflections: list[dict] = []
        self._insights: list[str] = []

    # ── Основной интерфейс ────────────────────────────────────────────────────

    def reflect(
        self,
        goal: str,
        result,
        context: dict | None = None,
        allow_insights: bool = True,
    ) -> dict:
        """
        Анализирует результат выполнения задачи.

        Args:
            goal    — что планировалось достичь
            result  — что получилось
            context — дополнительный контекст (план, шаги, ошибки)

        Returns:
            {
                'goal_achieved': bool,
                'analysis': str,
                'lessons': list[str],
                'suggestions': str,
            }
        """
        self._log(f"Рефлексия по задаче: '{goal}'")

        analysis = self._analyse(goal, result, context)
        achieved = self._check_goal(goal, result, analysis)
        lessons = self._extract_lessons(analysis, store_insight=allow_insights)
        suggestions = self._suggest_improvements(goal, result, analysis)
        solver_memory_summary = self._solver_memory_context(user_visible=True)
        public_analysis = analysis
        if solver_memory_summary:
            public_analysis = f"{analysis}\n\nSolver-memory summary:\n{solver_memory_summary}"

        reflection = {
            'goal': goal,
            'result': result,
            'goal_achieved': achieved,
            'analysis': public_analysis,
            'lessons': lessons,
            'suggestions': suggestions,
            'solver_memory': solver_memory_summary,
            'context': context,
        }
        self._reflections.append(reflection)

        # Сохраняем уроки в Knowledge System только для сильных циклов
        if allow_insights:
            self._save_lessons(goal, lessons)

        return reflection

    def analyse_error(self, error: Exception, action: str, context: dict | None = None) -> dict:
        """
        Глубокий анализ ошибки: что произошло, почему, как предотвратить.

        Returns:
            {'root_cause': str, 'prevention': str, 'recovery': str}
        """
        self._log(f"Анализ ошибки: {type(error).__name__}: {error}")

        if self.cognitive_core:
            analysis = self.cognitive_core.reasoning(
                f"Произошла ошибка при действии: {action}\n"
                f"Ошибка: {type(error).__name__}: {error}\n"
                f"Контекст: {context}\n"
                f"1. Определи корневую причину.\n"
                f"2. Как это предотвратить в будущем?\n"
                f"3. Как восстановиться сейчас?"
            )
        else:
            analysis = str(error)

        result = {
            'error_type': type(error).__name__,
            'error_message': str(error),
            'action': action,
            'analysis': analysis,
        }
        self._reflections.append({'type': 'error_analysis', **result})
        return result

    def evaluate_quality(self, output: str, expected_criteria: list[str]) -> dict:
        """
        Оценивает качество вывода по заданным критериям.

        Шаг 1 (детерминированный): keyword-overlap каждого критерия с выводом.
        Шаг 2 (LLM, опционально): уточнение если confidence < 0.7.

        Returns:
            {'score': float (0–1), 'passed': list, 'failed': list, 'feedback': str}
        """
        import re

        if not expected_criteria:
            return {'score': 1.0, 'passed': [], 'failed': [], 'feedback': 'Критерии не заданы'}

        output_l = output.lower()
        output_words = set(re.findall(r'\w+', output_l))

        passed  = []
        failed  = []

        for criterion in expected_criteria:
            crit_words = set(w for w in re.findall(r'\w+', criterion.lower()) if len(w) > 3)
            if not crit_words:
                passed.append(criterion)
                continue
            # Jaccard overlap: если ≥20% слов критерия встречается в выводе → выполнен
            overlap = len(crit_words & output_words) / len(crit_words)
            if overlap >= 0.2:
                passed.append(criterion)
            else:
                failed.append(criterion)

        det_score = len(passed) / len(expected_criteria) if expected_criteria else 1.0
        det_confidence = 0.6 if len(expected_criteria) > 2 else 0.4

        # LLM уточняет если неуверены (много критериев или плохой coverage)
        if self.cognitive_core and det_confidence < 0.7:
            criteria_str = '\n'.join(f"- {c}" for c in expected_criteria)
            llm_feedback = self.cognitive_core.reasoning(
                f"Оцени результат по критериям:\n{criteria_str}\n\n"
                f"Результат:\n{output[:1000]}\n\n"
                f"Для каждого критерия: выполнен или нет. В конце SCORE: <0-1>"
            )
            # Пробуем извлечь SCORE из LLM-ответа
            score_m = re.search(r'SCORE[:\s]+([0-9.]+)', str(llm_feedback), re.IGNORECASE)
            llm_score = float(score_m.group(1)) if score_m else None
            final_score = round(
                (llm_score * 0.6 + det_score * 0.4) if llm_score is not None else det_score,
                3
            )
            return {
                'score': final_score,
                'passed': passed,
                'failed': failed,
                'feedback': str(llm_feedback),
                'method': 'llm_enhanced',
            }

        return {
            'score': round(det_score, 3),
            'passed': passed,
            'failed': failed,
            'feedback': (
                f"Пройдено {len(passed)}/{len(expected_criteria)} критериев. "
                + (f"Не пройдены: {', '.join(failed[:3])}" if failed else "Все критерии выполнены.")
            ),
            'method': 'deterministic',
        }

    # ── Инсайты ───────────────────────────────────────────────────────────────

    def add_insight(self, insight: str):
        """Вручную добавляет инсайт (из внешних источников)."""
        self._insights.append(insight)

    def get_insights(self) -> list[str]:
        """Возвращает все накопленные инсайты."""
        return list(self._insights)

    def get_reflections(self, last_n: int | None = None) -> list[dict]:
        if last_n:
            return self._reflections[-last_n:]
        return list(self._reflections)

    def summary(self) -> dict:
        total = len(self._reflections)
        achieved = sum(1 for r in self._reflections if r.get('goal_achieved'))
        return {
            'total_reflections': total,
            'goals_achieved': achieved,
            'goals_failed': total - achieved,
            'success_rate': round(achieved / total, 2) if total else 0,
            'total_insights': len(self._insights),
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _analyse(self, goal, result, context) -> str:
        if not self.cognitive_core:
            return f"Цель: {goal}. Результат: {result}."
        solver_memory = self._solver_memory_context()
        goal_brief = str(goal)[:300]
        result_brief = str(result)[:4000]
        return self.cognitive_core.reasoning(
            f"Проанализируй выполнение задачи:\n"
            f"Цель: {goal_brief}\n"
            f"Результат: {result_brief}\n"
            f"Контекст: {context}\n"
            f"{solver_memory}"
            f"Оцени: что получилось, что нет, почему, что можно улучшить."
        )

    def _solver_memory_context(self, limit: int = 5, user_visible: bool = False) -> str:
        brain = getattr(self.cognitive_core, 'persistent_brain', None)
        if not brain:
            return ''

        try:
            recent_cases = brain.get_recent_solver_cases(limit)
            recent_journal = brain.get_recent_exact_solver_journal(limit)
            brain_summary = brain.summary()
        except Exception:
            return ''

        if not recent_cases and not recent_journal:
            return ''

        lines = [] if user_visible else ['Недавняя solver-memory:']
        aggregate_lines = self._solver_memory_aggregate_lines(brain_summary)
        if aggregate_lines:
            if user_visible:
                lines.append('Aggregate:')
            else:
                lines.append('Агрегаты:')
            lines.extend(f'- {line}' for line in aggregate_lines)
            if recent_cases or recent_journal:
                lines.append('Recent entries:' if user_visible else 'Последние записи:')
        for case in recent_cases:
            error = str(case.get('error', '')).strip()
            line = (
                'solver={solver}; optimality={optimality}; verification={verification}; '
                'confidence={confidence}'.format(
                    solver=case.get('solver', '?'),
                    optimality=case.get('optimality', '?'),
                    verification=case.get('verification', False),
                    confidence=case.get('confidence', 0.0),
                )
            )
            if error:
                line += f'; error={error}'
            lines.append(f'- {line}')
        for entry in recent_journal:
            lines.append(
                '- champion={solver}; challenger={challenger}; decision={decision}; '
                'verification={verification}; confidence={confidence}'.format(
                    solver=entry.get('solver', '?'),
                    challenger=entry.get('challenger', '?'),
                    decision=entry.get('decision', '?'),
                    verification=entry.get('verification', False),
                    confidence=entry.get('confidence', 0.0),
                )
            )
        return '\n'.join(lines) + '\n'

    @staticmethod
    def _solver_memory_aggregate_lines(brain_summary: dict | None) -> list[str]:
        if not isinstance(brain_summary, dict):
            return []

        solver_stats = brain_summary.get('solver_case_by_type') or {}
        journal_stats = brain_summary.get('champion_challenger_by_solver') or {}
        if not solver_stats and not journal_stats:
            return []

        lines: list[str] = []
        top_solvers = sorted(
            solver_stats.items(),
            key=lambda item: int(item[1].get('total', 0)),
            reverse=True,
        )[:3]
        for solver, stats in top_solvers:
            lines.append(
                'solver={solver}: total={total}, verified={verified}, failed={failed}, avg_confidence={confidence}'.format(
                    solver=solver,
                    total=stats.get('total', 0),
                    verified=stats.get('verified', 0),
                    failed=stats.get('failed_verification', 0),
                    confidence=stats.get('avg_confidence', 0.0),
                )
            )

        top_journal = sorted(
            journal_stats.items(),
            key=lambda item: int(item[1].get('total', 0)),
            reverse=True,
        )[:3]
        for solver, stats in top_journal:
            challengers = stats.get('challengers', {})
            best_challenger = ''
            if challengers:
                challenger_name, challenger_stats = max(
                    challengers.items(),
                    key=lambda item: int(item[1].get('total', 0)),
                )
                best_challenger = (
                    f", top_challenger={challenger_name}"
                    f" ({challenger_stats.get('promote', 0)}/{challenger_stats.get('total', 0)} promote)"
                )
            lines.append(
                'journal={solver}: promote={promote}, reject={reject}, verified={verified}, avg_confidence={confidence}{challenger}'.format(
                    solver=solver,
                    promote=stats.get('promote', 0),
                    reject=stats.get('reject', 0),
                    verified=stats.get('verified', 0),
                    confidence=stats.get('avg_confidence', 0.0),
                    challenger=best_challenger,
                )
            )
        return lines

    def _check_goal(self, _goal, result, analysis: str) -> bool:
        """
        Эвристически определяет достигнута ли цель.
        Без LLM: keyword-сигналы в analysis + проверка что result не None.
        """
        if result is None:
            return False

        analysis_l = str(analysis).lower()
        result_l   = str(result).lower()

        # Сигналы провала (вес -1 каждый)
        _FAIL = [
            'ошибка', 'не удалось', 'провал', 'failed', 'error', 'exception',
            'не достигнута', 'цель не', 'не выполнен', 'unable', 'cannot',
        ]
        # Сигналы успеха (вес +1 каждый)
        _SUCCESS = [
            'успех', 'выполнено', 'достигнут', 'завершен', 'completed', 'success',
            'done', 'achieved', 'готово', 'результат получен',
        ]

        fail_count    = sum(1 for s in _FAIL    if s in analysis_l or s in result_l)
        success_count = sum(1 for s in _SUCCESS if s in analysis_l or s in result_l)

        # Keyword-эвристика
        # Нет сигналов вообще = неизвестно (раньше считалось "достигнута" → ложный успех)
        if success_count == 0 and fail_count == 0:
            det_achieved = False  # лучше перепроверить, чем врать
        else:
            det_achieved = success_count > fail_count

        if not self.cognitive_core:
            return det_achieved

        # LLM для уточнения (если нет сигналов или ничья)
        if success_count == fail_count or (success_count == 0 and fail_count == 0):
            verdict = self.cognitive_core.decision_making(
                options=['цель достигнута', 'цель не достигнута'],
                context_note=f"Анализ:\n{analysis}"
            )
            return ('достигнута' in str(verdict).lower()
                    and 'не достигнута' not in str(verdict).lower())

        return det_achieved

    def _extract_lessons(self, analysis: str, store_insight: bool = True) -> list[str]:
        """
        Извлекает уроки из текста анализа.
        Без LLM: находит структурированные строки (нумерованные, буллеты) + lesson-keywords.
        """
        import re

        text = str(analysis)
        lessons = []

        # Паттерн 0: инлайн-нумерация "1. X. 2. Y" — разбиваем по ". N." границам
        inline_parts = re.split(r'(?<=[.!?])\s+\d+[.)]\s+', text)
        if len(inline_parts) > 1:
            for part in inline_parts:
                clean = re.sub(r'^\d+[.)]\s*', '', part).strip()
                if len(clean) > 10:
                    lessons.append(clean)

        # Паттерн 1: нумерованные строки (1. ..., 2. ...)
        if len(lessons) < 2:
            for m in re.finditer(r'^\s*\d+[.)]\s*(.+)', text, re.MULTILINE):
                line = m.group(1).strip()
                if len(line) > 15:
                    lessons.append(line)

        # Паттерн 2: строки с буллетами (- ..., • ...)
        if len(lessons) < 2:
            for m in re.finditer(r'^\s*[-•*]\s*(.+)', text, re.MULTILINE):
                line = m.group(1).strip()
                if len(line) > 15:
                    lessons.append(line)

        # Паттерн 3: предложения с ключевыми словами урока
        if len(lessons) < 2:
            _LESSON_KW = ['нужно', 'следует', 'важно', 'стоит', 'необходимо',
                          'should', 'must', 'better to', 'recommend', 'lesson']
            for sentence in re.split(r'[.!?]\s+', text):
                s_l = sentence.lower()
                if any(kw in s_l for kw in _LESSON_KW) and len(sentence) > 20:
                    lessons.append(sentence.strip())

        lessons = lessons[:5]  # не более 5 уроков

        # LLM для уточнения если нашли мало
        if len(lessons) < 2 and self.cognitive_core:
            raw = self.cognitive_core.reasoning(
                f"Из этого анализа извлеки 2-5 конкретных уроков (по одному на строку):\n{analysis}"
            )
            lessons = [line.strip('- •').strip()
                       for line in str(raw).splitlines()
                       if line.strip() and len(line.strip()) > 10][:5]

        if lessons and store_insight:
            self._insights.append(f"Урок: {lessons[0][:100]}")
        return lessons

    def _suggest_improvements(self, goal, result, analysis: str) -> str:
        """
        Предлагает конкретные улучшения на основе результата.
        Без LLM: rule-based шаблоны по паттернам ошибок.
        """
        analysis_l = str(analysis).lower()
        result_l   = str(result).lower()
        suggestions = []

        # Паттерн: ошибки → предлагаем добавить проверки
        if any(w in analysis_l for w in ['ошибка', 'error', 'exception', 'failed']):
            suggestions.append('Добавить обработку ошибок и валидацию входных данных.')

        # Паттерн: таймаут → предлагаем оптимизацию
        if any(w in analysis_l for w in ['timeout', 'slow', 'медленно', 'долго']):
            suggestions.append('Оптимизировать производительность: кэширование или параллелизм.')

        # Паттерн: нет данных → предлагаем расширить источники
        if any(w in result_l for w in ['none', 'null', 'пусто', 'not found', 'не найден']):
            suggestions.append('Расширить источники данных или уточнить запрос.')

        # Паттерн: часть цели не выполнена → разбить на шаги
        if any(w in analysis_l for w in ['частично', 'не полностью', 'partial']):
            suggestions.append('Разбить задачу на более мелкие подзадачи и выполнять последовательно.')

        # Универсальное предложение если ничего не сработало
        if not suggestions:
            suggestions.append('Сохранить текущий подход — результат достигнут.')

        base_suggestion = '\n'.join(f'- {s}' for s in suggestions)

        # LLM дополняет если есть
        if self.cognitive_core:
            llm_suggestions = self.cognitive_core.strategy_generator(
                f"На основе анализа предложи конкретные улучшения.\n"
                f"Цель: {goal}\nАнализ: {analysis[:500]}"
            )
            return f"{base_suggestion}\n\nLLM: {llm_suggestions}"

        return base_suggestion

    def _save_lessons(self, goal, lessons: list[str]):
        if self.knowledge and lessons:
            self.knowledge.store_long_term(
                f"lesson:{str(goal)[:50]}",
                '\n'.join(lessons),
                source='reflection', trust=0.6,
            )

    def _log(self, message: str):
        if self.monitoring:
            self.monitoring.info(message, source='reflection')
        else:
            print(f"[Reflection] {message}")

    # ── Ретроспективные мета-отчёты ───────────────────────────────────────────

    def generate_retrospective(self, cycles: list[dict], window: int = 50) -> dict:
        """Мета-отчёт по последним `window` циклам автономного цикла.

        Args:
            cycles  — список cycle.to_dict() из LoopCycle._cycle_history
            window  — сколько последних циклов анализировать

        Returns:
            {
              'total':        int,
              'success_rate': float,
              'avg_confidence': float,
              'top_errors':   list[str],        # топ-5 паттернов ошибок
              'goal_patterns': dict,            # тип цели → success_rate
              'trend':        'improving'|'degrading'|'stable',
              'recommendations': list[str],
            }
        """
        recent = cycles[-window:] if cycles else []
        if not recent:
            return {'total': 0, 'success_rate': 0.0, 'recommendations': ['Нет данных для анализа.']}

        total = len(recent)
        successes = sum(1 for c in recent if not c.get('errors'))
        success_rate = round(successes / total, 2)

        # Среднее confidence
        confs = [c.get('overall_confidence', 1.0) for c in recent if 'overall_confidence' in c]
        avg_confidence = round(sum(confs) / len(confs), 2) if confs else 1.0

        # Топ-5 паттернов ошибок
        from collections import Counter
        import re
        error_counter: Counter = Counter()
        for c in recent:
            for err in c.get('errors', []):
                # Нормализуем: берём первые 4 слова как ключ
                key = ' '.join(re.findall(r'\w+', str(err).lower())[:4])
                if key:
                    error_counter[key] += 1
        top_errors = [f"{k} (×{v})" for k, v in error_counter.most_common(5)]

        # Тренд: сравниваем первую и вторую половины
        half = max(1, total // 2)
        first_sr  = sum(1 for c in recent[:half]  if not c.get('errors')) / half
        second_sr = sum(1 for c in recent[half:]  if not c.get('errors')) / max(1, total - half)
        delta = second_sr - first_sr
        trend = 'improving' if delta > 0.1 else ('degrading' if delta < -0.1 else 'stable')

        # Рекомендации
        recommendations: list[str] = []
        if success_rate < 0.5:
            recommendations.append('Высокая частота ошибок — пересмотрите стратегию планирования.')
        if avg_confidence < 0.5:
            recommendations.append('Низкая средняя уверенность — проверьте качество данных наблюдения.')
        if top_errors:
            recommendations.append(f'Чаще всего: «{top_errors[0]}» — устраните корневую причину.')
        if trend == 'degrading':
            recommendations.append('Производительность снижается — рекомендуется диагностика.')
        if not recommendations:
            recommendations.append('Система работает стабильно.')

        self._log(
            f"[retrospective] total={total}, success_rate={success_rate:.0%}, "
            f"trend={trend}, top_error='{top_errors[0] if top_errors else '–'}'"
        )

        return {
            'total':           total,
            'success_rate':    success_rate,
            'avg_confidence':  avg_confidence,
            'top_errors':      top_errors,
            'trend':           trend,
            'recommendations': recommendations,
        }

    # ── Разбор опыта по слоям ─────────────────────────────────────────────────

    def layer_experience_digest(self) -> dict:
        """Анализирует накопленные рефлексии на предмет вклада каждого слоя.

        Сканирует все сохранённые рефлексии и ищет упоминания ключевых слоёв
        в полях 'errors', 'lessons', 'analysis'. Возвращает рейтинг слоёв:
        сколько раз слой встречался в ошибках и сколько в успехах.

        Returns:
            {
              'layer_scores': {
                  'execution':  {'errors': int, 'successes': int, 'score': float},
                  'cognitive_core': {...},
                  ...
              },
              'most_problematic': str,   # слой с наибольшим числом ошибок
              'most_reliable':    str,   # слой с наибольшим числом успехов
              'total_reflections': int,
            }
        """
        # Ключевые слои: ключ-паттерн → имя слоя
        LAYER_PATTERNS = {
            'execution':      r'execut|action_dispatch|исполнен',
            'cognitive_core': r'cognit|reasoning|анализ|llm\b|inference',
            'planning':       r'plan(?:n|ir)|планир',
            'simulation':     r'simul|sandbox|безопасност',
            'knowledge':      r'knowledge|knowledg|knowledge_system|знани',
            'learning':       r'learn(?:ing)?|обучен',
            'reflection':     r'reflect(?:ion)?|рефлекс',
            'monitoring':     r'monitor(?:ing)?|мониторинг',
            'perception':     r'percept|восприят|наблюден',
            'self_repair':    r'self.?repair|repair|само.{0,5}восстан',
            'hardware':       r'hardware|cpu|memory|ram|ресурс',
            'communication':  r'telegram|bot|уведомлен|сообщен',
        }
        import re
        layer_errors:    dict[str, int] = {k: 0 for k in LAYER_PATTERNS}
        layer_successes: dict[str, int] = {k: 0 for k in LAYER_PATTERNS}

        for ref in self._reflections:
            # Собираем весь текст рефлексии в одну строку
            parts = [
                str(ref.get('analysis', '')),
                ' '.join(str(l) for l in ref.get('lessons', [])),
                str(ref.get('suggestions', '')),
                str(ref.get('error_message', '')),
            ]
            text = ' '.join(parts).lower()
            goal_achieved = ref.get('goal_achieved', True)

            for layer, pat in LAYER_PATTERNS.items():
                if re.search(pat, text):
                    if goal_achieved:
                        layer_successes[layer] += 1
                    else:
                        layer_errors[layer] += 1

        # Строим scores: score = successes / max(1, errors + successes)
        layer_scores = {}
        for layer in LAYER_PATTERNS:
            e = layer_errors[layer]
            s = layer_successes[layer]
            layer_scores[layer] = {
                'errors':    e,
                'successes': s,
                'score':     round(s / max(1, e + s), 2),
            }

        # Слои с хоть каким-то вкладом
        active = {k: v for k, v in layer_scores.items() if v['errors'] + v['successes'] > 0}

        most_problematic = (
            min(active, key=lambda k: active[k]['score']) if active else '–'
        )
        most_reliable = (
            max(active, key=lambda k: active[k]['score']) if active else '–'
        )

        self._log(
            f"[layer_digest] reflections={len(self._reflections)}, "
            f"problematic={most_problematic}, reliable={most_reliable}"
        )

        return {
            'layer_scores':       layer_scores,
            'most_problematic':   most_problematic,
            'most_reliable':      most_reliable,
            'total_reflections':  len(self._reflections),
        }

    def export_state(self) -> dict:
        """Возвращает полное состояние для персистентности."""
        return {
            "reflections": list(self._reflections),
            "insights": list(self._insights),
        }

    def import_state(self, data: dict):
        """Восстанавливает состояние из персистентного хранилища."""
        if data.get("reflections"):
            self._reflections.extend(data["reflections"])
        if data.get("insights"):
            self._insights.extend(data["insights"])
