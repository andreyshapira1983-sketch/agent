# Покрытие: skills/task_decomposition.py
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from skills.task_decomposition import (
    SubtaskStatus, Subtask, TaskGraph, TaskDecompositionEngine,
)


class TestSubtaskStatus(unittest.TestCase):

    def test_values(self):
        self.assertEqual(SubtaskStatus.PENDING.value, 'pending')
        self.assertEqual(SubtaskStatus.READY.value, 'ready')
        self.assertEqual(SubtaskStatus.DONE.value, 'done')
        self.assertEqual(SubtaskStatus.FAILED.value, 'failed')
        self.assertEqual(SubtaskStatus.SKIPPED.value, 'skipped')


class TestSubtask(unittest.TestCase):

    def test_init_defaults(self):
        s = Subtask('t1', 'do something')
        self.assertEqual(s.task_id, 't1')
        self.assertEqual(s.goal, 'do something')
        self.assertIsNone(s.role)
        self.assertEqual(s.depends_on, [])
        self.assertEqual(s.priority, 2)
        self.assertEqual(s.status, SubtaskStatus.PENDING)
        self.assertIsNone(s.result)

    def test_init_with_args(self):
        s = Subtask('t1', 'goal', role='coding', depends_on=['t0'],
                     priority=3, metadata={'key': 'val'})
        self.assertEqual(s.role, 'coding')
        self.assertEqual(s.depends_on, ['t0'])
        self.assertEqual(s.metadata, {'key': 'val'})

    def test_to_dict(self):
        s = Subtask('t1', 'goal', role='analysis')
        s.result = 'ok'
        d = s.to_dict()
        self.assertEqual(d['task_id'], 't1')
        self.assertEqual(d['status'], 'pending')
        self.assertEqual(d['result'], 'ok')


class TestTaskGraph(unittest.TestCase):

    def _graph_with_chain(self):
        """a -> b -> c"""
        g = TaskGraph()
        a = Subtask('a', 'first')
        b = Subtask('b', 'second', depends_on=['a'])
        c = Subtask('c', 'third', depends_on=['b'])
        g.add(a); g.add(b); g.add(c)
        return g, a, b, c

    def test_add_and_get(self):
        g = TaskGraph()
        t = Subtask('x', 'test')
        g.add(t)
        self.assertIs(g.get('x'), t)
        self.assertIsNone(g.get('missing'))

    def test_get_ready_no_deps(self):
        g = TaskGraph()
        g.add(Subtask('a', 'no deps'))
        ready = g.get_ready()
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0].task_id, 'a')
        self.assertEqual(ready[0].status, SubtaskStatus.READY)

    def test_get_ready_with_deps(self):
        g, a, b, c = self._graph_with_chain()
        ready = g.get_ready()
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0].task_id, 'a')

    def test_get_ready_after_done(self):
        g, a, b, c = self._graph_with_chain()
        g.get_ready()  # marks a as READY
        g.mark_done('a', 'result_a')
        ready = g.get_ready()
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0].task_id, 'b')

    def test_get_ready_sorted_by_priority(self):
        g = TaskGraph()
        g.add(Subtask('lo', 'low', priority=1))
        g.add(Subtask('hi', 'high', priority=3))
        ready = g.get_ready()
        self.assertEqual(ready[0].task_id, 'hi')

    def test_mark_done(self):
        g = TaskGraph()
        g.add(Subtask('t', 'test'))
        g.mark_done('t', 'result')
        self.assertEqual(g.get('t').status, SubtaskStatus.DONE)
        self.assertEqual(g.get('t').result, 'result')

    def test_mark_done_missing(self):
        g = TaskGraph()
        g.mark_done('nonexistent')  # should not raise

    def test_mark_failed(self):
        g = TaskGraph()
        g.add(Subtask('t', 'test'))
        g.mark_failed('t', 'error msg')
        self.assertEqual(g.get('t').status, SubtaskStatus.FAILED)
        self.assertEqual(g.get('t').result, 'error msg')

    def test_mark_failed_missing(self):
        g = TaskGraph()
        g.mark_failed('nonexistent')

    def test_all_done_empty(self):
        g = TaskGraph()
        self.assertTrue(g.all_done())

    def test_all_done_false(self):
        g = TaskGraph()
        g.add(Subtask('t', 'test'))
        self.assertFalse(g.all_done())

    def test_all_done_true(self):
        g = TaskGraph()
        g.add(Subtask('t', 'test'))
        g.mark_done('t')
        self.assertTrue(g.all_done())

    def test_all_done_mixed_terminal(self):
        g = TaskGraph()
        g.add(Subtask('a', 'x'))
        g.add(Subtask('b', 'y'))
        g.mark_done('a')
        g.mark_failed('b')
        self.assertTrue(g.all_done())

    def test_to_list(self):
        g = TaskGraph()
        g.add(Subtask('a', 'x'))
        g.add(Subtask('b', 'y'))
        lst = g.to_list()
        self.assertEqual(len(lst), 2)
        self.assertIsInstance(lst[0], dict)

    def test_expand_node(self):
        g = TaskGraph()
        parent = Subtask('p', 'parent')
        parent.depth = 1
        g.add(parent)
        sub1 = Subtask('s1', 'child 1')
        sub2 = Subtask('s2', 'child 2')
        g.expand_node('p', [sub1, sub2])
        self.assertEqual(parent.status, SubtaskStatus.SKIPPED)
        self.assertEqual(sub1.depth, 2)
        self.assertIn('s1', g._nodes)

    def test_expand_node_missing_parent(self):
        g = TaskGraph()
        sub = Subtask('s', 'child')
        g.expand_node('nonexistent', [sub])
        self.assertEqual(sub.depth, 0)
        self.assertIn('s', g._nodes)

    def test_topological_order(self):
        g, a, b, c = self._graph_with_chain()
        order = g.topological_order()
        ids = [t.task_id for t in order]
        self.assertEqual(ids, ['a', 'b', 'c'])

    def test_topological_order_independent(self):
        g = TaskGraph()
        g.add(Subtask('x', 'independent 1'))
        g.add(Subtask('y', 'independent 2'))
        order = g.topological_order()
        self.assertEqual(len(order), 2)


class TestTaskDecompositionEngine(unittest.TestCase):

    def _make(self, **kw):
        defaults = dict(
            cognitive_core=None,
            agent_system=None,
            monitoring=MagicMock(),
        )
        defaults.update(kw)
        return TaskDecompositionEngine(**defaults)

    # ── decompose ─────────────────────────────────────────────────────────
    def test_decompose_no_core_uses_deterministic(self):
        eng = self._make()
        g = eng.decompose('исследуй рынок AI')
        tasks = g.to_list()
        self.assertGreater(len(tasks), 0)

    def test_decompose_with_core(self):
        cc = MagicMock()
        cc.plan.return_value = (
            "1. Проанализировать требования\n"
            "   Роль: planning\n"
            "   Приоритет: 3\n"
            "2. Реализовать решение\n"
            "   Роль: coding\n"
            "   Зависит от: 1\n"
            "   Приоритет: 2\n"
        )
        eng = self._make(cognitive_core=cc)
        g = eng.decompose('build a module')
        tasks = g.to_list()
        self.assertGreaterEqual(len(tasks), 1)

    def test_decompose_empty_response(self):
        cc = MagicMock()
        cc.plan.return_value = ""
        eng = self._make(cognitive_core=cc)
        g = eng.decompose('empty plan')
        self.assertEqual(len(g.to_list()), 0)

    # ── _decompose_deterministic templates ────────────────────────────────
    def test_template_research(self):
        eng = self._make()
        g = eng._decompose_deterministic('исследуй и найди информацию')
        self.assertEqual(len(g.to_list()), 4)

    def test_template_code(self):
        eng = self._make()
        g = eng._decompose_deterministic('создай модуль для парсинга')
        self.assertEqual(len(g.to_list()), 4)

    def test_template_debug(self):
        eng = self._make()
        g = eng._decompose_deterministic('исправь ошибку в модуле')
        self.assertEqual(len(g.to_list()), 4)

    def test_template_analyze(self):
        eng = self._make()
        g = eng._decompose_deterministic('проанализируй данные за квартал')
        self.assertEqual(len(g.to_list()), 4)

    def test_template_write(self):
        eng = self._make()
        g = eng._decompose_deterministic('напиши текст документации')
        # 'write' keyword matches 'напиши текст' via write_article default
        tasks = g.to_list()
        self.assertGreater(len(tasks), 0)

    def test_template_deploy(self):
        eng = self._make()
        g = eng._decompose_deterministic('задеплой сервис в production')
        self.assertEqual(len(g.to_list()), 4)

    def test_template_default(self):
        eng = self._make()
        g = eng._decompose_deterministic('сделай что-нибудь непонятное странное')
        self.assertEqual(len(g.to_list()), 4)

    def test_deterministic_max_subtasks(self):
        eng = self._make()
        g = eng._decompose_deterministic('найди данные', max_subtasks=2)
        self.assertLessEqual(len(g.to_list()), 2)

    # ── decompose_recursive ───────────────────────────────────────────────
    def test_decompose_recursive_depth_1(self):
        eng = self._make()
        g = eng.decompose_recursive('найди данные', depth=1)
        self.assertGreater(len(g.to_list()), 0)

    def test_decompose_recursive_depth_2(self):
        eng = self._make()
        g = eng.decompose_recursive('найди данные и проанализируй', depth=2)
        self.assertGreater(len(g.to_list()), 4)

    # ── execute_graph ─────────────────────────────────────────────────────
    def test_execute_graph_no_agent(self):
        eng = self._make()
        g = TaskGraph()
        g.add(Subtask('t1', 'do thing'))
        results = eng.execute_graph(g)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['status'], 'done')
        self.assertIn('warning', results[0]['result'])

    def test_execute_graph_with_agent(self):
        agent = MagicMock()
        agent.handle.return_value = 'done!'
        eng = self._make(agent_system=agent)
        g = TaskGraph()
        g.add(Subtask('t1', 'task goal'))
        results = eng.execute_graph(g)
        self.assertEqual(results[0]['result'], 'done!')

    def test_execute_graph_agent_exception(self):
        agent = MagicMock()
        agent.handle.side_effect = RuntimeError('fail')
        eng = self._make(agent_system=agent)
        g = TaskGraph()
        g.add(Subtask('t1', 'will fail'))
        results = eng.execute_graph(g)
        self.assertEqual(results[0]['status'], 'failed')

    def test_execute_graph_skips_failed(self):
        eng = self._make()
        g = TaskGraph()
        t = Subtask('t1', 'pre-failed')
        t.status = SubtaskStatus.FAILED
        g.add(t)
        results = eng.execute_graph(g)
        self.assertEqual(len(results), 0)  # skipped, not executed

    def test_execute_graph_chain(self):
        agent = MagicMock()
        agent.handle.return_value = 'ok'
        eng = self._make(agent_system=agent)
        g = TaskGraph()
        g.add(Subtask('a', 'first'))
        g.add(Subtask('b', 'second', depends_on=['a']))
        results = eng.execute_graph(g)
        self.assertEqual(len(results), 2)

    # ── _parse_subtasks ───────────────────────────────────────────────────
    def test_parse_subtasks_basic(self):
        eng = self._make()
        raw = "1. Do first thing\n2. Do second thing"
        result = eng._parse_subtasks(raw)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['goal'], 'Do first thing')

    def test_parse_subtasks_with_role(self):
        eng = self._make()
        raw = "1. Analyze data\n   Роль: analysis\n2. Write code\n   Роль: coding"
        result = eng._parse_subtasks(raw)
        self.assertEqual(result[0]['role'], 'analysis')
        self.assertEqual(result[1]['role'], 'coding')

    def test_parse_subtasks_with_deps(self):
        eng = self._make()
        raw = "1. Step one\n2. Step two\n   Зависит от: 1"
        result = eng._parse_subtasks(raw)
        self.assertIn(1, result[1]['depends_on'])

    def test_parse_subtasks_with_priority(self):
        eng = self._make()
        raw = "1. Important task\n   Приоритет: 3"
        result = eng._parse_subtasks(raw)
        self.assertEqual(result[0]['priority'], 3)

    def test_parse_subtasks_empty(self):
        eng = self._make()
        self.assertEqual(eng._parse_subtasks(''), [])

    # ── needs_decomposition ───────────────────────────────────────────────
    def test_needs_decomposition_empty(self):
        eng = self._make()
        self.assertFalse(eng.needs_decomposition(''))

    def test_needs_decomposition_code_block(self):
        eng = self._make()
        self.assertFalse(eng.needs_decomposition('```python\nprint("hi")\n```'))

    def test_needs_decomposition_tool_call(self):
        eng = self._make()
        self.assertFalse(eng.needs_decomposition('tool_layer.use("terminal", cmd="ls")'))

    def test_needs_decomposition_short_task(self):
        eng = self._make()
        self.assertFalse(eng.needs_decomposition('прочитай файл'))

    def test_needs_decomposition_search_prefix(self):
        eng = self._make()
        self.assertFalse(eng.needs_decomposition('SEARCH: python machine learning'))

    def test_needs_decomposition_atomic_verb(self):
        eng = self._make()
        self.assertFalse(eng.needs_decomposition(
            'создай файл output.txt с результатами анализа'))

    def test_needs_decomposition_complex(self):
        eng = self._make()
        result = eng.needs_decomposition(
            'Сначала найди все данные о конкурентах, затем проанализируй их '
            'и создай подробный отчёт с графиками и рекомендациями'
        )
        self.assertTrue(result)

    def test_needs_decomposition_numbered_steps(self):
        eng = self._make()
        result = eng.needs_decomposition(
            'Выполни задание:\n1. Собери данные\n2. Обработай\n3. Визуализируй'
        )
        self.assertTrue(result)


if __name__ == '__main__':
    unittest.main()
