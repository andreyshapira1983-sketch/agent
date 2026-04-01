"""
Priority-4 coverage: tools/tool_layer.py (ToolLayer class) and
loop/autonomous_loop.py (LoopCycle, LearningQualityTracker, AutonomousLoop basic API).
"""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ═══════════════════════════════════════════════════════════════════════════
# 1. ToolLayer
# ═══════════════════════════════════════════════════════════════════════════
from tools.tool_layer import ToolLayer, BaseTool


class _MockTool(BaseTool):
    """Minimal mock tool for testing ToolLayer."""

    def __init__(self):
        super().__init__('mock_tool', 'A test tool')

    def run(self, *args, **kwargs):
        return {'success': True, 'data': kwargs}


class _CalcTool(BaseTool):

    def __init__(self):
        super().__init__('calculator', 'Performs math')

    def run(self, *args, **kwargs):
        import ast
        import operator
        expression = kwargs.get('expression', args[0] if args else '0')
        # Safe expression evaluator using AST (no eval)
        _ops = {ast.Add: operator.add, ast.Sub: operator.sub,
                ast.Mult: operator.mul, ast.Div: operator.truediv}
        tree = ast.parse(expression, mode='eval').body
        if isinstance(tree, ast.Constant):
            return {'success': True, 'result': tree.value}
        if isinstance(tree, ast.BinOp) and type(tree.op) in _ops:
            left = ast.literal_eval(ast.Expression(body=tree.left))
            right = ast.literal_eval(ast.Expression(body=tree.right))
            return {'success': True, 'result': _ops[type(tree.op)](left, right)}
        return {'success': False, 'result': None}


class TestToolLayerRegister(unittest.TestCase):
    def test_register_and_list(self):
        tl = ToolLayer()
        tl.register(_MockTool())
        self.assertIn('mock_tool', tl.list())

    def test_register_multiple(self):
        tl = ToolLayer()
        tl.register(_MockTool())
        tl.register(_CalcTool())
        self.assertEqual(len(tl.list()), 2)


class TestToolLayerGet(unittest.TestCase):
    def test_get_existing(self):
        tl = ToolLayer()
        tl.register(_MockTool())
        t = tl.get('mock_tool')
        self.assertIsNotNone(t)
        self.assertEqual(t.name, 'mock_tool')

    def test_get_nonexistent(self):
        tl = ToolLayer()
        self.assertIsNone(tl.get('nonexistent'))


class TestToolLayerUse(unittest.TestCase):
    def test_use_success(self):
        tl = ToolLayer()
        tl.register(_MockTool())
        result = tl.use('mock_tool', key='value')
        self.assertTrue(result.get('success'))

    def test_use_nonexistent_raises(self):
        tl = ToolLayer()
        with self.assertRaises(KeyError):
            tl.use('unknown_tool')

    def test_use_with_params(self):
        tl = ToolLayer()
        tl.register(_CalcTool())
        result = tl.use('calculator', expression='2+3')
        self.assertEqual(result['result'], 5)


class TestToolLayerDescribe(unittest.TestCase):
    def test_describe_all(self):
        tl = ToolLayer()
        tl.register(_MockTool())
        tl.register(_CalcTool())
        desc = tl.describe()
        self.assertEqual(len(desc), 2)
        names = [d['name'] for d in desc]
        self.assertIn('mock_tool', names)
        self.assertIn('calculator', names)

    def test_describe_empty(self):
        tl = ToolLayer()
        self.assertEqual(tl.describe(), [])

    def test_describe_has_fields(self):
        tl = ToolLayer()
        tl.register(_MockTool())
        desc = tl.describe()[0]
        self.assertIn('name', desc)
        self.assertIn('description', desc)


class TestToolLayerListEmpty(unittest.TestCase):
    def test_list_empty(self):
        tl = ToolLayer()
        self.assertEqual(tl.list(), [])


# ═══════════════════════════════════════════════════════════════════════════
# 2. LoopCycle
# ═══════════════════════════════════════════════════════════════════════════
from loop.autonomous_loop import LoopCycle, LoopPhase, LearningQualityTracker


class TestLoopPhase(unittest.TestCase):
    def test_phases_exist(self):
        for name in ('OBSERVE', 'ANALYZE', 'PLAN', 'SIMULATE', 'ACT',
                      'EVALUATE', 'LEARN', 'REPAIR', 'IMPROVE', 'IDLE', 'STOPPED'):
            self.assertTrue(hasattr(LoopPhase, name))


class TestLoopCycle(unittest.TestCase):
    def test_create(self):
        c = LoopCycle(1)
        self.assertEqual(c.cycle_id, 1)
        self.assertEqual(c.phase, LoopPhase.IDLE)

    def test_overall_confidence_default(self):
        c = LoopCycle(1)
        self.assertEqual(c.overall_confidence, 1.0)

    def test_overall_confidence_min(self):
        c = LoopCycle(1)
        c.confidence['plan'] = 0.3
        self.assertEqual(c.overall_confidence, 0.3)

    def test_to_dict(self):
        c = LoopCycle(1)
        c.phase = LoopPhase.ACT
        c.success = True
        d = c.to_dict()
        self.assertEqual(d['cycle_id'], 1)
        self.assertEqual(d['phase'], 'act')
        self.assertTrue(d['success'])
        self.assertIn('overall_confidence', d)

    def test_errors_list(self):
        c = LoopCycle(1)
        c.errors.append('error 1')
        self.assertEqual(len(c.errors), 1)


# ═══════════════════════════════════════════════════════════════════════════
# 3. LearningQualityTracker
# ═══════════════════════════════════════════════════════════════════════════


class TestLearningQualityTracker(unittest.TestCase):
    def setUp(self):
        self.lqt = LearningQualityTracker()

    def test_unknown_area_score(self):
        self.assertAlmostEqual(self.lqt.quality_score('unknown'), 0.5)

    def test_record_and_score(self):
        self.lqt.record_use('planning', True)
        self.lqt.record_use('planning', True)
        self.lqt.record_use('planning', False)
        # Beta(2+1, 1+1) / (3+2) = 3/5 = 0.6
        score = self.lqt.quality_score('planning')
        self.assertAlmostEqual(score, 0.6, places=2)

    def test_confidence_zero(self):
        self.assertEqual(self.lqt.confidence('unknown'), 0.0)

    def test_confidence_grows(self):
        for _ in range(10):
            self.lqt.record_use('area1', True)
        self.assertEqual(self.lqt.confidence('area1'), 1.0)

    def test_weighted_score_unknown(self):
        self.assertAlmostEqual(self.lqt.weighted_score('unknown'), 0.5)

    def test_weighted_score_known(self):
        for _ in range(10):
            self.lqt.record_use('good', True)
        ws = self.lqt.weighted_score('good')
        self.assertGreater(ws, 0.7)

    def test_get_poor_strategies(self):
        for _ in range(10):
            self.lqt.record_use('bad_strat', False)
        poor = self.lqt.get_poor_strategies(min_uses=5)
        self.assertIn('bad_strat', poor)

    def test_get_effective_strategies(self):
        for _ in range(10):
            self.lqt.record_use('great_strat', True)
        effective = self.lqt.get_effective_strategies(min_uses=3)
        self.assertIn('great_strat', effective)

    def test_get_ranked(self):
        for _ in range(5):
            self.lqt.record_use('good', True)
        for _ in range(5):
            self.lqt.record_use('bad', False)
        ranked = self.lqt.get_ranked(min_confidence=0.0)
        self.assertGreater(len(ranked), 0)
        # First should have higher score
        if len(ranked) >= 2:
            self.assertGreaterEqual(ranked[0][1], ranked[1][1])

    def test_summary(self):
        self.lqt.record_use('area', True)
        s = self.lqt.summary()
        self.assertIn('area', s)
        self.assertIn('quality', s['area'])

    def test_to_dict_and_load(self):
        self.lqt.record_use('a', True)
        self.lqt.record_use('b', False)
        d = self.lqt.to_dict()
        lqt2 = LearningQualityTracker()
        lqt2.load_from_dict(d)
        self.assertAlmostEqual(lqt2.quality_score('a'), self.lqt.quality_score('a'))

    def test_decay_applied(self):
        # Record exactly DECAY_EVERY records to trigger decay
        for _ in range(LearningQualityTracker._DECAY_EVERY):
            self.lqt.record_use('decaying', True)
        # After decay, uses should be < DECAY_EVERY
        uses = self.lqt.get_uses('decaying')
        self.assertLess(uses, LearningQualityTracker._DECAY_EVERY)

    def test_get_uses_unknown(self):
        self.assertEqual(self.lqt.get_uses('nope'), 0.0)


# ═══════════════════════════════════════════════════════════════════════════
# 4. AutonomousLoop basic API (no start, just state + step)
# ═══════════════════════════════════════════════════════════════════════════
from loop.autonomous_loop import AutonomousLoop


class TestAutonomousLoopInit(unittest.TestCase):
    def test_create_minimal(self):
        loop = AutonomousLoop()
        self.assertFalse(loop.is_running)
        self.assertIsNone(loop.current_goal)
        self.assertEqual(loop._cycle_count, 0)

    def test_create_with_deps(self):
        monitoring = SimpleNamespace(log=MagicMock(), record_metric=MagicMock())
        loop = AutonomousLoop(monitoring=monitoring)
        self.assertFalse(loop.is_running)


class TestAutonomousLoopGoal(unittest.TestCase):
    def test_set_goal(self):
        loop = AutonomousLoop()
        loop.set_goal('build a python module')
        self.assertEqual(loop.current_goal, 'build a python module')

    def test_set_goal_resets_decomposition(self):
        loop = AutonomousLoop()
        loop._goal_decomposed = True
        loop._subgoal_queue = ['a', 'b']
        loop.set_goal('new goal')
        self.assertFalse(loop._goal_decomposed)
        self.assertEqual(loop._subgoal_queue, [])


class TestAutonomousLoopStop(unittest.TestCase):
    def test_stop(self):
        loop = AutonomousLoop()
        loop._running = True
        loop.stop()
        self.assertFalse(loop.is_running)


class TestAutonomousLoopHistory(unittest.TestCase):
    def test_empty_history(self):
        loop = AutonomousLoop()
        self.assertEqual(loop.get_history(), [])

    def test_get_current_cycle_none(self):
        loop = AutonomousLoop()
        self.assertIsNone(loop.get_current_cycle())


class TestAutonomousLoopInterrupt(unittest.TestCase):
    def test_push_interrupt(self):
        loop = AutonomousLoop()
        loop.push_interrupt('disk_full', priority=1, source='monitor')
        self.assertEqual(len(loop._interrupt_queue), 1)
        self.assertEqual(loop._interrupt_queue[0]['event'], 'disk_full')
        self.assertEqual(loop._interrupt_queue[0]['priority'], 1)

    def test_push_multiple_interrupts(self):
        loop = AutonomousLoop()
        loop.push_interrupt('event1', priority=2)
        loop.push_interrupt('event2', priority=3)
        self.assertEqual(len(loop._interrupt_queue), 2)


class TestAutonomousLoopStep(unittest.TestCase):
    def test_step_minimal(self):
        """Step with no deps should still produce a LoopCycle."""
        loop = AutonomousLoop()
        loop.set_goal('test goal')
        cycle = loop.step()
        self.assertIsInstance(cycle, LoopCycle)
        self.assertIsNotNone(cycle.to_dict())

    def test_step_increments_count(self):
        loop = AutonomousLoop()
        loop.set_goal('test')
        loop.step()
        self.assertGreaterEqual(loop._cycle_count, 1)


class TestAutonomousLoopPreview(unittest.TestCase):
    def test_preview_none(self):
        self.assertEqual(AutonomousLoop._preview(None), "")

    def test_preview_long_string(self):
        s = 'x' * 500
        r = AutonomousLoop._preview(s, limit=100)
        self.assertEqual(len(r), 100)

    def test_preview_short(self):
        self.assertEqual(AutonomousLoop._preview('hello'), 'hello')


if __name__ == '__main__':
    unittest.main()
