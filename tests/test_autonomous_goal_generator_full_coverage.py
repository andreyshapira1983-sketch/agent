# pyright: reportPrivateUsage=false, reportAttributeAccessIssue=false, reportAssignmentType=false, reportArgumentType=false, reportOptionalMemberAccess=false
# pylint: disable=protected-access,unused-argument
# ruff: noqa: SLF001, ARG001

import os
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

from core.autonomous_goal_generator import AutonomousGoalGenerator


class _Reflection:
    def __init__(self, insights):
        self._insights = insights

    def get_insights(self):
        return self._insights


class _Identity:
    def __init__(self, inv):
        self._inv = inv

    def get_real_capability_inventory(self):
        return self._inv


class _GoalManager:
    def __init__(self):
        self.calls = []
        self.active = []

    def add_goal(self, description, priority=None, tags=None):
        self.calls.append((description, priority, tags))

    def get_active_goals(self):
        return self.active


class _GoalManagerGetAll:
    def __init__(self, goals):
        self._goals = goals

    def add_goal(self, description, priority=None, tags=None):
        _ = (description, priority, tags)

    def get_all(self):
        return self._goals


class _Core:
    def __init__(self, answer='Создать модуль для x'):
        self.answer = answer

    def reasoning(self, text):
        _ = text
        return self.answer


class _AgentSpawner:
    def __init__(self, result):
        self.result = result

    def build_and_register(self, **kwargs):
        _ = kwargs
        return self.result


class _ModuleBuilder:
    def __init__(self, ok=True):
        self.ok = ok

    def build_module(self, **kwargs):
        _ = kwargs
        if self.ok:
            return types.SimpleNamespace(ok=True, file_path='x.py', error='')
        return types.SimpleNamespace(ok=False, file_path='', error='boom')


class _Mon:
    def __init__(self):
        self.logs = []

    def info(self, msg, source=''):
        self.logs.append((msg, source))


class AutonomousGoalGeneratorCoverageTests(unittest.TestCase):
    def test_generate_from_reflection_paths(self):
        g = AutonomousGoalGenerator(reflection=None)
        self.assertEqual(g.generate_from_reflection(), [])

        g2 = AutonomousGoalGenerator(reflection=types.SimpleNamespace(get_insights=lambda: (_ for _ in ()).throw(RuntimeError('x'))))
        self.assertEqual(g2.generate_from_reflection(), [])

        g3 = AutonomousGoalGenerator(reflection=_Reflection([]))
        self.assertEqual(g3.generate_from_reflection(), [])

        g4 = AutonomousGoalGenerator(reflection=_Reflection(['нет инструмента для y', 'обычный текст']))
        g4._add_goal = MagicMock()
        g4._mark_recent = MagicMock()
        added = g4.generate_from_reflection()
        self.assertEqual(len(added), 1)

        g5 = AutonomousGoalGenerator(reflection=_Reflection(['нет инструмента']))
        g5._insight_to_goal = MagicMock(return_value='goal')
        g5._is_duplicate = MagicMock(return_value=True)
        self.assertEqual(g5.generate_from_reflection(), [])

    def test_generate_from_inventory_paths(self):
        g = AutonomousGoalGenerator(identity=None)
        self.assertEqual(g.generate_from_inventory(), [])

        g2 = AutonomousGoalGenerator(identity=types.SimpleNamespace(get_real_capability_inventory=lambda: (_ for _ in ()).throw(RuntimeError('x'))))
        self.assertEqual(g2.generate_from_inventory(), [])

        inv = {
            'proven': [
                {'action': 'a', 'description': 'd', 'uses': 3, 'proficiency': 0.2},
                {'action': 'b', 'description': 'd2', 'uses': 6, 'proficiency': 0.9},
            ],
            'never_tried': ['python'],
            'summary': 'ok',
        }
        g3 = AutonomousGoalGenerator(identity=_Identity(inv))
        g3._add_goal = MagicMock()
        g3._mark_recent = MagicMock()
        g3._is_duplicate = MagicMock(return_value=False)
        out = g3.generate_from_inventory()
        self.assertEqual(len(out), 3)

        inv2 = {'proven': [], 'never_tried': ['unknown'], 'summary': ''}
        g4 = AutonomousGoalGenerator(identity=_Identity(inv2))
        g4._is_duplicate = MagicMock(return_value=False)
        self.assertEqual(g4.generate_from_inventory(), [])

        g5 = AutonomousGoalGenerator(identity=_Identity(inv))
        g5._is_duplicate = MagicMock(return_value=True)
        self.assertEqual(g5.generate_from_inventory(), [])

    def test_propose_from_gap_and_new_agent_module(self):
        g = AutonomousGoalGenerator()
        self.assertIsNone(g.propose_from_gap(''))

        g2 = AutonomousGoalGenerator(cognitive_core=_Core('Создать x'))
        g2._add_goal = MagicMock()
        g2._mark_recent = MagicMock()
        g2._is_duplicate = MagicMock(return_value=False)
        self.assertEqual(g2.propose_from_gap('gap y'), 'Создать x')

        g3 = AutonomousGoalGenerator(cognitive_core=None)
        g3._add_goal = MagicMock()
        g3._mark_recent = MagicMock()
        g3._is_duplicate = MagicMock(return_value=False)
        self.assertIn('Создать агента или инструмент', g3.propose_from_gap('gap y') or '')

        g4 = AutonomousGoalGenerator(cognitive_core=_Core('Создать x'))
        g4._is_duplicate = MagicMock(return_value=True)
        self.assertIsNone(g4.propose_from_gap('gap y'))

        sp_ok = _AgentSpawner({'ok': True, 'name': 'n', 'error': None})
        g5 = AutonomousGoalGenerator(agent_spawner=sp_ok)
        self.assertTrue(g5.propose_new_agent('n', 'd', immediate=True)['ok'])

        sp_fail = _AgentSpawner({'ok': False, 'name': 'n', 'error': 'e'})
        g6 = AutonomousGoalGenerator(agent_spawner=sp_fail)
        g6._add_goal = MagicMock()
        self.assertFalse(g6.propose_new_agent('n', 'd', immediate=True)['ok'])

        g7 = AutonomousGoalGenerator(agent_spawner=None)
        g7._is_duplicate = MagicMock(return_value=False)
        g7._add_goal = MagicMock()
        g7._mark_recent = MagicMock()
        self.assertFalse(g7.propose_new_agent('n', 'd', immediate=False)['ok'])

        g8 = AutonomousGoalGenerator(module_builder=None)
        self.assertIsNone(g8.propose_new_module('m', 'd'))

        g9 = AutonomousGoalGenerator(module_builder=_ModuleBuilder(ok=True))
        self.assertTrue(g9.propose_new_module('m', 'd').ok)

        g10 = AutonomousGoalGenerator(module_builder=_ModuleBuilder(ok=False))
        self.assertFalse(g10.propose_new_module('m', 'd').ok)

    def test_insight_gap_add_duplicate_mark_and_log(self):
        g = AutonomousGoalGenerator(cognitive_core=None)
        self.assertIsNone(g._insight_to_goal('привет мир'))
        self.assertIn('Улучшить:', g._insight_to_goal('нет агента для x') or '')

        g2 = AutonomousGoalGenerator(cognitive_core=_Core('Создать модуль\nвторая строка'))
        self.assertEqual(g2._insight_to_goal('нет инструмента y'), 'Создать модуль')

        g3 = AutonomousGoalGenerator(cognitive_core=types.SimpleNamespace(reasoning=lambda _t: (_ for _ in ()).throw(RuntimeError('x'))))
        self.assertIn('Улучшить:', g3._insight_to_goal('missing tool y') or '')

        g4 = AutonomousGoalGenerator(cognitive_core=None)
        self.assertIsNone(g4._gap_to_goal('x'))

        g5 = AutonomousGoalGenerator(cognitive_core=_Core('Создать z'))
        self.assertEqual(g5._gap_to_goal('x'), 'Создать z')

        g6 = AutonomousGoalGenerator(cognitive_core=_Core('x'))
        self.assertIsNone(g6._gap_to_goal('x'))

        g7 = AutonomousGoalGenerator(cognitive_core=types.SimpleNamespace(reasoning=lambda _t: (_ for _ in ()).throw(RuntimeError('x'))))
        self.assertIsNone(g7._gap_to_goal('x'))

        # _add_goal branches
        g8 = AutonomousGoalGenerator(goal_manager=None)
        self.assertIsNone(g8._add_goal('d', 2, ['t']))

        gm = _GoalManager()
        g9 = AutonomousGoalGenerator(goal_manager=gm)
        fake_mod = types.ModuleType('core.goal_manager')

        class _Priority:
            CRITICAL = 'c'
            MEDIUM = 'm'
            HIGH = 'h'

        fake_mod.GoalPriority = _Priority
        with patch.dict('sys.modules', {'core.goal_manager': fake_mod}):
            g9._add_goal('d1', 1, ['t'])
            g9._add_goal('d2', 2, ['t'])
            g9._add_goal('d3', 3, ['t'])
            g9._add_goal('d4', 99, ['t'])
        self.assertEqual(len(gm.calls), 4)

        gm_fail = types.SimpleNamespace(add_goal=lambda **k: (_ for _ in ()).throw(RuntimeError('x')))
        g10 = AutonomousGoalGenerator(goal_manager=gm_fail)
        g10._add_goal('d', 2, ['t'])

        # _is_duplicate
        g11 = AutonomousGoalGenerator(goal_manager=None)
        goal = 'abc'
        import hashlib as _hl
        _key = _hl.md5(goal[:80].encode('utf-8', errors='replace')).hexdigest()
        g11._recent_goals[_key] = time_value = 123.0
        with patch('core.autonomous_goal_generator.time.time', return_value=time_value + 10):
            self.assertTrue(g11._is_duplicate(goal))

        gm2 = _GoalManager()
        gm2.active = [{'description': 'something abc text'}]
        g12 = AutonomousGoalGenerator(goal_manager=gm2)
        self.assertTrue(g12._is_duplicate('abc'))

        g13 = AutonomousGoalGenerator(goal_manager=_GoalManagerGetAll([{'status': 'active', 'description': 'goal xyz'}]))
        self.assertTrue(g13._is_duplicate('xyz'))

        # get_active_goals returns one non-list truthy goal
        g13b = AutonomousGoalGenerator(goal_manager=types.SimpleNamespace(get_active_goals=lambda: {'description': 'goal zzz'}))
        self.assertTrue(g13b._is_duplicate('zzz'))

        g14 = AutonomousGoalGenerator(goal_manager=types.SimpleNamespace(get_active_goals=lambda: (_ for _ in ()).throw(RuntimeError('x'))))
        self.assertFalse(g14._is_duplicate('zzz'))

        # _mark_recent cleanup
        g15 = AutonomousGoalGenerator()
        with patch('core.autonomous_goal_generator.time.time', side_effect=[10.0, 10000.0]):
            g15._mark_recent('g1')
        self.assertTrue(isinstance(g15._recent_goals, dict))

        # _log
        mon = _Mon()
        g16 = AutonomousGoalGenerator(monitoring=mon)
        g16._log('x')
        self.assertTrue(mon.logs)

        g17 = AutonomousGoalGenerator(monitoring=types.SimpleNamespace(info=lambda *a, **k: (_ for _ in ()).throw(RuntimeError('x'))))
        g17._log('x')

        g18 = AutonomousGoalGenerator(monitoring=None)
        with patch('builtins.print') as p:
            g18._log('x')
        p.assert_called_once()

    def test_scan_working_dir_paths(self):
        g = AutonomousGoalGenerator(goal_manager=_GoalManager())
        g._add_goal = MagicMock()
        g._mark_recent = MagicMock()

        with patch('os.scandir', side_effect=OSError('x')):
            self.assertEqual(g.scan_working_dir('x'), [])

        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, 'folder'), exist_ok=True)
            # skipped by extension
            with open(os.path.join(td, 'a.bin'), 'w', encoding='utf-8') as f:
                f.write('x')
            # skipped by name
            with open(os.path.join(td, 'requirements.txt'), 'w', encoding='utf-8') as f:
                f.write('x')
            # skipped by prefix
            with open(os.path.join(td, '.hidden.txt'), 'w', encoding='utf-8') as f:
                f.write('x')
            # target file
            with open(os.path.join(td, 'task.txt'), 'w', encoding='utf-8') as f:
                f.write('do task')

            out = g.scan_working_dir(td)
            self.assertEqual(len(out), 1)

            # duplicate by seen cache
            out2 = g.scan_working_dir(td)
            self.assertEqual(out2, [])

        # duplicate by active goals
        gm = _GoalManager()
        gm.active = [{'description': 'task2.txt do this'}]
        g2 = AutonomousGoalGenerator(goal_manager=gm)
        with tempfile.TemporaryDirectory() as td2:
            with open(os.path.join(td2, 'task2.txt'), 'w', encoding='utf-8') as f:
                f.write('x')
            self.assertEqual(g2.scan_working_dir(td2), [])

        # get_active_goals exception branch inside scan_working_dir
        g2b = AutonomousGoalGenerator(goal_manager=types.SimpleNamespace(get_active_goals=lambda: (_ for _ in ()).throw(RuntimeError('x'))))
        g2b._add_goal = MagicMock()
        g2b._mark_recent = MagicMock()
        with tempfile.TemporaryDirectory() as td2b:
            with open(os.path.join(td2b, 'task2b.txt'), 'w', encoding='utf-8') as f:
                f.write('x')
            out2b = g2b.scan_working_dir(td2b)
            self.assertEqual(len(out2b), 1)

        # _is_duplicate branch from generated goal
        g3 = AutonomousGoalGenerator(goal_manager=_GoalManager())
        g3._is_duplicate = MagicMock(return_value=True)
        with tempfile.TemporaryDirectory() as td3:
            with open(os.path.join(td3, 'task3.txt'), 'w', encoding='utf-8') as f:
                f.write('x')
            self.assertEqual(g3.scan_working_dir(td3), [])

        # open preview fails
        g4 = AutonomousGoalGenerator(goal_manager=_GoalManager())
        g4._add_goal = MagicMock()
        g4._mark_recent = MagicMock()
        delattr(g4, '_seen_workdir_files')
        with tempfile.TemporaryDirectory() as td4:
            p = os.path.join(td4, 'task4.txt')
            with open(p, 'w', encoding='utf-8') as f:
                f.write('x')
            with patch('builtins.open', side_effect=OSError('x')):
                out = g4.scan_working_dir(td4)
                self.assertEqual(len(out), 1)

        # outer per-entry exception branch
        class _BadEntry:
            def is_file(self):
                raise RuntimeError('bad entry')

        g5 = AutonomousGoalGenerator(goal_manager=_GoalManager())
        with patch('os.scandir', return_value=[_BadEntry()]):
            self.assertEqual(g5.scan_working_dir('x'), [])


if __name__ == '__main__':
    unittest.main()
