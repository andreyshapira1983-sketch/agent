import unittest

from core.cognitive_core import CognitiveCore, SolverMode, SolverTaskType


class CognitiveCoreRegressionTests(unittest.TestCase):
    def _make_core(self):
        core = object.__new__(CognitiveCore)
        core._duplicate_window_sec = 1800
        core._semantic_duplicate_threshold = 0.72
        core._recent_user_requests = []
        core.persistent_brain = None
        core._max_recent_requests = 20
        return core

    def test_duplicate_detection_respects_numeric_parameters(self):
        core = self._make_core()
        core._remember_request('Дай агенту 20 источников по теме', 'ok')

        self.assertIsNotNone(core._find_duplicate('Дай агенту 20 источников по теме'))
        self.assertIsNone(core._find_duplicate('Дай агенту 30 источников по теме'))

    def test_portfolio_index_requires_explicit_marker(self):
        self.assertEqual(
            CognitiveCore._extract_portfolio_project_index('открой проект 3 в портфолио', 10),
            2,
        )
        self.assertIsNone(
            CognitiveCore._extract_portfolio_project_index('в портфолио у меня 3 кейса по python', 10)
        )

    def test_structured_resource_allocation_routes_to_solver(self):
        core = self._make_core()
        task = (
            'У тебя есть 10 ресурсов. Выбери оптимальную стратегию и максимизируй ожидаемую награду.\n\n'
            'A: reward 100, cost 6, p=0.9\n'
            'B: reward 70, cost 4, p=0.9\n'
            'C: reward 40, cost 3, p=0.95\n'
        )

        route = core._classify_solver_task(task)
        self.assertEqual(route['mode'], SolverMode.SOLVER.value)
        self.assertEqual(route['task_type'], SolverTaskType.RESOURCE_ALLOCATION.value)

    def test_reward_cost_discussion_without_solver_intent_stays_reasoning(self):
        core = self._make_core()
        task = (
            'Нужно придумать для агента много задач. '
            'Опиши, как reward, cost и вероятность могут использоваться в таких заданиях, '
            'но ничего не решай и не выбирай оптимальную стратегию.'
        )

        route = core._classify_solver_task(task)
        self.assertEqual(route['mode'], SolverMode.REASONING.value)
        self.assertEqual(route['task_type'], SolverTaskType.HEURISTIC.value)

    def test_generic_constraints_text_does_not_become_constraint_solver(self):
        core = self._make_core()
        task = (
            'Составь план работы с учетом ограничений по времени и бюджету, '
            'но без формального перебора, CSP или SAT.'
        )

        route = core._classify_solver_task(task)
        self.assertEqual(route['mode'], SolverMode.REASONING.value)
        self.assertEqual(route['task_type'], SolverTaskType.HEURISTIC.value)

    def test_structured_graph_routes_to_solver(self):
        core = self._make_core()
        task = (
            'Найди кратчайший путь из A в D.\n'
            'A -> B: 3\n'
            'B -> D: 4\n'
            'A -> C: 10\n'
            'C -> D: 1\n'
        )

        route = core._classify_solver_task(task)
        self.assertEqual(route['mode'], SolverMode.SOLVER.value)
        self.assertEqual(route['task_type'], SolverTaskType.GRAPH_SEARCH.value)

    def test_generic_route_word_does_not_become_graph_solver(self):
        core = self._make_core()
        task = 'Составь маршрут работы на неделю и маршрут обсуждения с клиентом с учетом приоритетов.'

        route = core._classify_solver_task(task)
        self.assertEqual(route['mode'], SolverMode.REASONING.value)
        self.assertEqual(route['task_type'], SolverTaskType.HEURISTIC.value)


if __name__ == '__main__':
    unittest.main()