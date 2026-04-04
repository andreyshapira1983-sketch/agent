"""
StepEvaluator — evaluation harness для оценки результатов шагов агента.

Проваливает шаг при:
  - неверном классе инструмента (expected: email, got: search)
  - нерелевантном результате (irrelevant_results flag)
  - подмене задачи (expected: retrieve, got: generate)
  - ложном статусе success (status='non_actionable' + success=True)
  - отсутствии object resolution (нет конкретного объекта, а есть placeholder)

Используется в AutonomousLoop._evaluate() и OrchestrationSystem для
финального verdict каждого шага.

Возвращает {passed: bool, verdict: str, issues: list[str], score: float}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Импортируем TaskRouter если доступен
try:
    from execution.task_router import TaskRouter, TaskRoute
    _ROUTER: TaskRouter | None = TaskRouter()
except ImportError:
    _ROUTER = None


@dataclass
class StepVerdict:
    passed: bool
    verdict: str                        # SUCCESS / PARTIAL / BLOCKED / FAILED_WRONG_TOOL /
                                        # FAILED_IRRELEVANT / FAILED_SUBSTITUTION / NON_ACTIONABLE / SKIPPED
    score: float                        # 0.0 – 1.0
    issues: list[str] = field(default_factory=list)
    expected_tool: str = ''
    actual_tool: str = ''
    intent: str = ''

    def to_dict(self) -> dict:
        return {
            'passed':        self.passed,
            'verdict':       self.verdict,
            'score':         round(self.score, 3),
            'issues':        self.issues,
            'expected_tool': self.expected_tool,
            'actual_tool':   self.actual_tool,
            'intent':        self.intent,
        }


class StepEvaluator:
    """
    Оценивает результат шага агента с точки зрения:
      1. Correct tool class — правильный ли инструмент был использован
      2. Relevance — соответствует ли результат цели
      3. Task integrity — не была ли задача подменена
      4. Status honesty — не помечен ли незавершённый шаг как SUCCESS
      5. Object resolution — был ли объект конкретизирован или остался placeholder

    Основной метод: evaluate(goal, result) → StepVerdict
    """

    # Маппинг intent → ожидаемые типы action
    _EXPECTED_TOOL_TYPES: dict[str, set[str]] = {
        'email':       {'email'},
        'calendar':    {'calendar'},
        'contacts':    {'contacts', 'email'},  # contacts через email tool тоже допустимо
        'user_files':  {'filesystem', 'python', 'bash', 'read'},
        'pdf_extract': {'pdf', 'python', 'read'},
        'weather':     {'weather', 'search'},
        'time':        {'time', 'python'},
        'currency':    {'currency', 'search'},
        'sports':      {'search'},
        'web_search':  {'search'},
        'code':        {'python', 'bash'},
        'general':     {'python', 'bash', 'write', 'search'},
        'heading':     set(),
        'empty':       set(),
    }

    # Действия-признаки GENERATE (создание нового) vs RETRIEVE (получение существующего)
    _GENERATE_SIGNALS = (
        'создан', 'создала', 'создан файл', 'написан', 'сгенерирован',
        'created', 'generated', 'I have created', 'presentation created',
        'файл создан', 'отчёт создан', 'документ создан',
    )
    _RETRIEVE_SIGNALS = (
        'найдено', 'найден', 'получен', 'extracted', 'retrieved',
        'results:', 'результаты:', 'found', 'список',
    )

    def evaluate(self, goal: str, result: Any, execution_result: dict | None = None) -> StepVerdict:
        """
        Оценивает результат шага.

        Args:
            goal — текст задачи (цели)
            result — результат из agent_system.handle() или dispatch()
            execution_result — опциональный dict из ActionDispatcher

        Returns:
            StepVerdict
        """
        issues: list[str] = []
        score = 1.0

        # 0. Маршрутизация задачи
        route: TaskRoute | None = None
        if _ROUTER is not None and goal:
            try:
                route = _ROUTER.route(goal)
            except (AttributeError, TypeError, ValueError, RuntimeError):
                pass

        intent = route.intent.value if route else 'general'

        # 1. NON_ACTIONABLE (заголовок)
        if route and route.intent.value in ('heading', 'empty'):
            return StepVerdict(
                passed=True,
                verdict='NON_ACTIONABLE',
                score=1.0,
                issues=['Заголовок раздела — корректно пропущен'],
                intent=intent,
            )

        # 2. BLOCKED (нет обязательных аргументов)
        if route and route.status.value == 'blocked':
            missing = [a.description for a in route.missing_args]
            issues.append(f'BLOCKED: отсутствуют аргументы: {", ".join(missing)}')
            # Проверяем, честно ли агент вернул blocked
            res_status = self._get_result_status(result)
            if res_status not in ('blocked', 'non_actionable', 'failed'):
                issues.append(
                    f'ЛОЖНЫЙ_SUCCESS: задача BLOCKED, но статус ответа={res_status!r}'
                )
                score -= 0.5
            return StepVerdict(
                passed=False,
                verdict='BLOCKED',
                score=max(0.0, score),
                issues=issues,
                intent=intent,
            )

        # 3. Correct tool check
        tool_verdict = self._check_tool_class(intent, execution_result, issues)
        score += tool_verdict  # отрицательный штраф

        # 4. Relevance check
        rel_verdict = self._check_relevance(execution_result, issues)
        score += rel_verdict

        # 5. Task substitution check
        sub_verdict = self._check_substitution(goal, result, issues)
        score += sub_verdict

        # 6. Status honesty check
        status_verdict = self._check_status_honesty(result, execution_result, issues)
        score += status_verdict

        # 7. Object resolution check
        obj_verdict = self._check_object_resolution(goal, issues)
        score += obj_verdict

        score = max(0.0, min(1.0, score))
        passed = score >= 0.5 and not any(
            kw in i for i in issues
            for kw in ('WRONG_TOOL', 'SUBSTITUTION', 'ЛОЖНЫЙ_SUCCESS', 'IRRELEVANT')
        )

        # Финальный verdict
        if not issues:
            verdict = 'SUCCESS'
        elif score >= 0.7:
            verdict = 'PARTIAL'
        elif any('WRONG_TOOL' in i for i in issues):
            verdict = 'FAILED_WRONG_TOOL'
        elif any('IRRELEVANT' in i for i in issues):
            verdict = 'FAILED_IRRELEVANT'
        elif any('SUBSTITUTION' in i for i in issues):
            verdict = 'FAILED_SUBSTITUTION'
        elif any('ЛОЖНЫЙ' in i for i in issues):
            verdict = 'FAILED_FALSE_STATUS'
        elif any('NOT_RESOLVED' in i for i in issues):
            verdict = 'BLOCKED_MISSING_INPUT'
        else:
            verdict = 'FAILED'

        return StepVerdict(
            passed=passed,
            verdict=verdict,
            score=score,
            issues=issues,
            expected_tool=','.join(self._EXPECTED_TOOL_TYPES.get(intent, {'?'})),
            intent=intent,
        )

    # ── Проверки ──────────────────────────────────────────────────────────────

    def _check_tool_class(
        self, intent: str, exec_r: dict | None, issues: list
    ) -> float:
        """Проверяет, что был использован инструмент правильного класса."""
        expected = self._EXPECTED_TOOL_TYPES.get(intent, set())
        if not expected:
            return 0.0  # нет ожидания

        # Определяем фактический тип инструмента
        actual_types = self._extract_actual_tool_types(exec_r)
        if not actual_types:
            return 0.0  # нет данных — не штрафуем

        # web-search для личных доменов
        personal_intents = {'email', 'calendar', 'contacts', 'user_files'}
        if intent in personal_intents and actual_types - {'blocked'} == {'search'}:
            issues.append(
                f'WRONG_TOOL: intent={intent}, ожидался {expected}, '
                f'использован web-search — личные данные не в интернете'
            )
            return -0.4

        # Нет пересечения ожидаемых и фактических инструментов
        if expected and not (expected & actual_types):
            issues.append(
                f'WRONG_TOOL: intent={intent}, ожидался={expected}, фактически={actual_types}'
            )
            return -0.3

        return 0.0

    def _check_relevance(
        self, exec_r: dict | None, issues: list
    ) -> float:
        """Проверяет релевантность search-результатов цели."""
        if not exec_r:
            return 0.0

        results_list = exec_r.get('results', [])
        search_results = [r for r in results_list if r.get('type') == 'search']
        irrelevant_count = sum(
            1 for r in search_results
            if r.get('relevant') is False or (r.get('error') or '').startswith('IRRELEVANT')
        )
        if irrelevant_count and irrelevant_count == len(search_results):
            issues.append(
                f'IRRELEVANT: все {irrelevant_count} search-результата нерелевантны цели'
            )
            return -0.3

        return 0.0

    def _check_substitution(
        self, goal: str, result: Any, issues: list
    ) -> float:
        """Обнаруживает подмену retrieve→generate."""
        goal_l = goal.lower()

        # Разговорные вопросы — не retrieve-задачи, пропускаем
        _CONVERSATIONAL = (
            'что ты сделал', 'что ты делал', 'как дела', 'привет',
            'что нового', 'что умеешь', 'расскажи о себе',
            'who are you', 'what did you do', 'how are you',
            'что ты знаешь', 'что происходит', 'статус',
        )
        if any(c in goal_l for c in _CONVERSATIONAL):
            return 0.0

        # Задача требует FIND/RETRIEVE
        is_retrieve_task = any(kw in goal_l for kw in (
            'найди', 'найти', 'get', 'retrieve', 'find', 'список', 'покажи',
            'открой', 'прочитай', 'извлеки', 'extract', 'show',
        ))
        if not is_retrieve_task:
            return 0.0

        # Результат содержит сигналы GENERATE
        result_text = self._result_to_text(result)
        has_generate_signal = any(s in result_text.lower() for s in self._GENERATE_SIGNALS)
        if has_generate_signal:
            # Дополнительно проверяем: нет ли сигналов retrieve среди generate?
            has_retrieve_signal = any(s in result_text.lower() for s in self._RETRIEVE_SIGNALS)
            if not has_retrieve_signal:
                issues.append(
                    'SUBSTITUTION: задача требовала retrieve/find, '
                    'но агент выполнил generate/create'
                )
                return -0.4

        return 0.0

    def _check_status_honesty(
        self, result: Any, exec_r: dict | None, issues: list
    ) -> float:
        """Проверяет честность статуса результата."""
        if exec_r is None:
            return 0.0

        res_status = self._get_result_status(result)
        exec_success = exec_r.get('success', None)

        # Execution провалился, но статус 'done' или 'success'
        if exec_success is False and res_status in ('done', 'success'):
            issues.append(
                f'ЛОЖНЫЙ_SUCCESS: execution.success=False, но result.status={res_status!r}'
            )
            return -0.3

        # actions_found=0 но помечено как success
        if exec_r.get('actions_found', -1) == 0 and exec_success is True:
            status = exec_r.get('status', '')
            if status not in ('non_actionable', 'skipped', 'blocked'):
                issues.append(
                    'ЛОЖНЫЙ_SUCCESS: actions_found=0 но execution.success=True '
                    '(нет реальных действий)'
                )
                return -0.2

        return 0.0

    def _check_object_resolution(self, goal: str, issues: list) -> float:
        """Проверяет, что объект задачи не остался placeholder'ом."""
        vague_markers = (
            'конкретного', 'определённого', 'specific sender',
            'конкретной команды', 'specific team',
            'имя_контакта', 'конкретного контакта',
            'заданного отправителя',
        )
        goal_l = goal.lower()
        if any(m in goal_l for m in vague_markers):
            issues.append(
                'NOT_RESOLVED: задача содержит placeholder вместо конкретного объекта'
            )
            return -0.2

        return 0.0

    # ── Утилиты ───────────────────────────────────────────────────────────────

    @staticmethod
    def _get_result_status(result: Any) -> str:
        if isinstance(result, dict):
            return str(result.get('status', 'unknown')).lower()
        return 'unknown'

    @staticmethod
    def _extract_actual_tool_types(exec_r: dict | None) -> set[str]:
        """Извлекает типы инструментов, которые реально использовались."""
        if not exec_r:
            return set()
        types: set[str] = set()
        for item in exec_r.get('results', []):
            t = str(item.get('type', '')).lower()
            if t:
                types.add(t)
        return types

    @staticmethod
    def _result_to_text(result: Any) -> str:
        if isinstance(result, str):
            return result[:500]
        if isinstance(result, dict):
            parts = []
            for key in ('result', 'output', 'summary', 'message', 'answer'):
                v = result.get(key, '')
                if v:
                    parts.append(str(v)[:200])
            return ' '.join(parts)
        return str(result)[:300]


# ── Convenience singleton ─────────────────────────────────────────────────────

_evaluator = StepEvaluator()


def evaluate_step(goal: str, result: Any, execution_result: dict | None = None) -> StepVerdict:
    """Module-level shortcut: evaluate_step(goal, result) → StepVerdict."""
    return _evaluator.evaluate(goal, result, execution_result)
