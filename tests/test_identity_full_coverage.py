"""100% coverage tests for core/identity.py"""

import time
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from core.identity import AgentCapabilityStatus, IdentityCore


# ── AgentCapabilityStatus ────────────────────────────────────────────────────

class CapabilityStatusBasicTests(unittest.TestCase):

    def test_default_creation(self):
        cap = AgentCapabilityStatus("test", "desc")
        self.assertEqual(cap.name, "test")
        self.assertEqual(cap.description, "desc")
        self.assertTrue(cap.available)
        self.assertEqual(cap.proficiency, 0.5)
        self.assertEqual(cap.usage_count, 0)
        self.assertIsNone(cap.last_used)

    def test_proficiency_clamped_high(self):
        cap = AgentCapabilityStatus("x", "d", proficiency=5.0)
        self.assertEqual(cap.proficiency, 1.0)

    def test_proficiency_clamped_low(self):
        cap = AgentCapabilityStatus("x", "d", proficiency=-3.0)
        self.assertEqual(cap.proficiency, 0.0)

    def test_custom_params(self):
        cap = AgentCapabilityStatus("n", "d", available=False, proficiency=0.75)
        self.assertFalse(cap.available)
        self.assertEqual(cap.proficiency, 0.75)


class CapabilityStatusRecordUseTests(unittest.TestCase):

    def test_success_increases_proficiency(self):
        cap = AgentCapabilityStatus("t", "d", proficiency=0.5)
        cap.record_use(success=True)
        self.assertEqual(cap.usage_count, 1)
        self.assertAlmostEqual(cap.proficiency, 0.52)
        self.assertIsNotNone(cap.last_used)

    def test_failure_decreases_proficiency(self):
        cap = AgentCapabilityStatus("t", "d", proficiency=0.5)
        cap.record_use(success=False)
        self.assertAlmostEqual(cap.proficiency, 0.49)

    def test_proficiency_capped_at_1(self):
        cap = AgentCapabilityStatus("t", "d", proficiency=0.99)
        cap.record_use(success=True)
        self.assertEqual(cap.proficiency, 1.0)

    def test_proficiency_capped_at_0(self):
        cap = AgentCapabilityStatus("t", "d", proficiency=0.005)
        cap.record_use(success=False)
        self.assertEqual(cap.proficiency, 0.0)

    def test_multiple_uses(self):
        cap = AgentCapabilityStatus("t", "d", proficiency=0.5)
        cap.record_use(True)
        cap.record_use(False)
        cap.record_use(True)
        self.assertEqual(cap.usage_count, 3)


class CapabilityStatusToDictTests(unittest.TestCase):

    def test_to_dict(self):
        cap = AgentCapabilityStatus("cap", "description", available=True, proficiency=0.123)
        d = cap.to_dict()
        self.assertEqual(d['name'], 'cap')
        self.assertEqual(d['description'], 'description')
        self.assertTrue(d['available'])
        self.assertEqual(d['proficiency'], 0.123)
        self.assertEqual(d['usage_count'], 0)
        self.assertNotIn('last_used', d)

    def test_to_dict_rounds_proficiency(self):
        cap = AgentCapabilityStatus("x", "d", proficiency=0.33333)
        d = cap.to_dict()
        self.assertEqual(d['proficiency'], 0.333)


# ── IdentityCore Init ────────────────────────────────────────────────────────

class IdentityCoreInitTests(unittest.TestCase):

    def test_defaults(self):
        ic = IdentityCore()
        self.assertEqual(ic.name, "Agent")
        self.assertEqual(ic.role, "Autonomous AI Agent")
        self.assertIn("Работать как равный партнёр", ic.mission)
        self.assertIsNone(ic.cognitive_core)
        self.assertIsNone(ic.monitoring)
        self.assertEqual(len(ic.values), 5)
        self.assertEqual(len(ic.limitations), 4)
        self.assertEqual(len(ic._capabilities), 8)

    def test_custom_params(self):
        core = SimpleNamespace()
        mon = MagicMock()  # needs .info() method
        ic = IdentityCore(name="Bob", role="Helper", mission="Help",
                          cognitive_core=core, monitoring=mon)
        self.assertEqual(ic.name, "Bob")
        self.assertEqual(ic.role, "Helper")
        self.assertEqual(ic.mission, "Help")
        self.assertIs(ic.cognitive_core, core)
        self.assertIs(ic.monitoring, mon)

    def test_default_capabilities_registered(self):
        ic = IdentityCore()
        names = set(ic._capabilities.keys())
        expected = {"reasoning", "planning", "code_generation", "research",
                    "communication", "tool_use", "learning", "self_reflection"}
        self.assertEqual(names, expected)


# ── Identity methods ─────────────────────────────────────────────────────────

class DescribeTests(unittest.TestCase):

    def test_describe_keys(self):
        ic = IdentityCore()
        d = ic.describe()
        for key in ('name', 'role', 'mission', 'values', 'limitations',
                     'capabilities', 'uptime_hours'):
            self.assertIn(key, d)

    def test_describe_only_available(self):
        ic = IdentityCore()
        ic._capabilities['reasoning'].available = False
        d = ic.describe()
        cap_names = [c['name'] for c in d['capabilities']]
        self.assertNotIn('reasoning', cap_names)

    def test_describe_uptime(self):
        ic = IdentityCore()
        ic._created_at = time.time() - 7200
        d = ic.describe()
        self.assertAlmostEqual(d['uptime_hours'], 2.0, delta=0.1)


class IntroduceTests(unittest.TestCase):

    def test_introduce_without_core(self):
        ic = IdentityCore(name="TestBot", role="Tester", mission="Test all")
        intro = ic.introduce()
        self.assertIn("TestBot", intro)
        self.assertIn("Tester", intro)
        self.assertIn("Test all", intro)

    def test_introduce_with_core(self):
        core = SimpleNamespace(reasoning=lambda x: "Я великий агент!")
        ic = IdentityCore(cognitive_core=core)
        intro = ic.introduce()
        self.assertEqual(intro, "Я великий агент!")


class SettersTests(unittest.TestCase):

    def test_set_name(self):
        ic = IdentityCore()
        ic.set_name("NewName")
        self.assertEqual(ic.name, "NewName")

    def test_set_mission(self):
        ic = IdentityCore()
        ic.set_mission("NewMission")
        self.assertEqual(ic.mission, "NewMission")

    def test_add_value_new(self):
        ic = IdentityCore()
        count_before = len(ic.values)
        ic.add_value("Новая ценность")
        self.assertEqual(len(ic.values), count_before + 1)

    def test_add_value_duplicate(self):
        ic = IdentityCore()
        ic.add_value("Честность и прозрачность")
        self.assertEqual(ic.values.count("Честность и прозрачность"), 1)

    def test_add_limitation_new(self):
        ic = IdentityCore()
        count_before = len(ic.limitations)
        ic.add_limitation("Не летаю")
        self.assertEqual(len(ic.limitations), count_before + 1)

    def test_add_limitation_duplicate(self):
        ic = IdentityCore()
        existing = ic.limitations[0]
        ic.add_limitation(existing)
        self.assertEqual(ic.limitations.count(existing), 1)


# ── Capabilities ─────────────────────────────────────────────────────────────

class RegisterCapabilityTests(unittest.TestCase):

    def test_register(self):
        ic = IdentityCore()
        cap = ic.register_capability("new_cap", "A new capability", proficiency=0.9)
        self.assertIsInstance(cap, AgentCapabilityStatus)
        self.assertIn("new_cap", ic._capabilities)
        self.assertAlmostEqual(cap.proficiency, 0.9)

    def test_register_overwrites(self):
        ic = IdentityCore()
        ic.register_capability("reasoning", "Override", proficiency=0.1)
        self.assertAlmostEqual(ic._capabilities['reasoning'].proficiency, 0.1)


class RecordCapabilityUseTests(unittest.TestCase):

    def test_known_capability(self):
        ic = IdentityCore()
        ic.record_capability_use("reasoning", True)
        self.assertEqual(ic._capabilities['reasoning'].usage_count, 1)

    def test_unknown_capability_no_error(self):
        ic = IdentityCore()
        ic.record_capability_use("nonexistent", True)


class CanDoTests(unittest.TestCase):

    def test_no_core(self):
        ic = IdentityCore()
        capable, explanation = ic.can_do("some task")
        self.assertTrue(capable)
        self.assertEqual(explanation, "Нет данных для оценки")

    def test_with_core_yes(self):
        core = SimpleNamespace(
            reasoning=lambda x: "ОТВЕТ: да\nОБЪЯСНЕНИЕ: Легко справлюсь"
        )
        ic = IdentityCore(cognitive_core=core)
        capable, explanation = ic.can_do("simple task")
        self.assertTrue(capable)
        self.assertEqual(explanation, "Легко справлюсь")

    def test_with_core_no(self):
        core = SimpleNamespace(
            reasoning=lambda x: "ОТВЕТ: нет\nОБЪЯСНЕНИЕ: Слишком сложно"
        )
        ic = IdentityCore(cognitive_core=core)
        capable, explanation = ic.can_do("hard task")
        self.assertFalse(capable)
        self.assertEqual(explanation, "Слишком сложно")

    def test_with_core_no_match(self):
        core = SimpleNamespace(reasoning=lambda x: "Непонятный ответ без паттернов")
        ic = IdentityCore(cognitive_core=core)
        capable, explanation = ic.can_do("task")
        self.assertFalse(capable)
        self.assertIn("Непонятный ответ", explanation)


# ── Self-assessment ──────────────────────────────────────────────────────────

class SelfAssessTests(unittest.TestCase):

    def test_no_history(self):
        ic = IdentityCore()
        result = ic.self_assess()
        self.assertIsNone(result['score'])

    def test_with_history(self):
        ic = IdentityCore()
        for _ in range(5):
            ic._performance_history.append({'success': True})
        for _ in range(5):
            ic._performance_history.append({'success': False})
        result = ic.self_assess()
        self.assertAlmostEqual(result['success_rate'], 0.5)
        self.assertEqual(result['recent_tasks'], 10)

    def test_with_more_than_20_entries(self):
        ic = IdentityCore()
        for i in range(25):
            ic._performance_history.append({'success': i >= 15})
        result = ic.self_assess()
        self.assertEqual(result['recent_tasks'], 20)

    def test_strongest_capability(self):
        ic = IdentityCore()
        ic._performance_history.append({'success': True})
        result = ic.self_assess()
        self.assertIsNotNone(result['strongest_capability'])

    def test_no_capabilities(self):
        ic = IdentityCore()
        ic._capabilities.clear()
        ic._performance_history.append({'success': True})
        result = ic.self_assess()
        self.assertIsNone(result['strongest_capability'])
        self.assertEqual(result['avg_capability_proficiency'], 0.0)


class RecordPerformanceTests(unittest.TestCase):

    def test_basic_record(self):
        ic = IdentityCore()
        ic.record_performance("task1", True)
        self.assertEqual(len(ic._performance_history), 1)
        entry = ic._performance_history[0]
        self.assertEqual(entry['task'], 'task1')
        self.assertTrue(entry['success'])
        self.assertIsNone(entry['capability'])

    def test_with_capability(self):
        ic = IdentityCore()
        ic.record_performance("task2", True, capability_used="reasoning")
        self.assertEqual(ic._capabilities['reasoning'].usage_count, 1)

    def test_with_unknown_capability(self):
        ic = IdentityCore()
        ic.record_performance("task3", False, capability_used="nonexistent")
        self.assertEqual(len(ic._performance_history), 1)


# ── record_action_stats ──────────────────────────────────────────────────────

class RecordActionStatsTests(unittest.TestCase):

    def test_creates_new_capability(self):
        ic = IdentityCore()
        ic.record_action_stats('search', True)
        self.assertIn('action:search', ic._capabilities)
        self.assertEqual(ic._capabilities['action:search'].usage_count, 1)

    def test_known_action_type_description(self):
        ic = IdentityCore()
        ic.record_action_stats('python', True)
        self.assertIn('Python', ic._capabilities['action:python'].description)

    def test_unknown_action_type_description(self):
        ic = IdentityCore()
        ic.record_action_stats('custom_thing', False)
        self.assertIn('custom_thing', ic._capabilities['action:custom_thing'].description)

    def test_reuse_existing(self):
        ic = IdentityCore()
        ic.record_action_stats('bash', True)
        ic.record_action_stats('bash', False)
        self.assertEqual(ic._capabilities['action:bash'].usage_count, 2)

    def test_count_param(self):
        ic = IdentityCore()
        ic.record_action_stats('read', True, count=5)
        self.assertEqual(ic._capabilities['action:read'].usage_count, 5)

    def test_all_known_action_types(self):
        ic = IdentityCore()
        known = ['search', 'python', 'bash', 'write', 'read',
                 'api', 'browser', 'github', 'docker', 'db']
        for action in known:
            ic.record_action_stats(action, True)
            self.assertIn(f'action:{action}', ic._capabilities)


# ── get_real_capability_inventory ────────────────────────────────────────────

class RealCapabilityInventoryTests(unittest.TestCase):

    def test_empty_no_actions(self):
        ic = IdentityCore()
        inv = ic.get_real_capability_inventory()
        self.assertEqual(inv['proven'], [])
        self.assertEqual(inv['total_proven'], 0)
        self.assertIn('Ещё не выполнял', inv['summary'])
        self.assertEqual(len(inv['untested']), 8)
        self.assertEqual(len(inv['never_tried']), 10)

    def test_with_actions(self):
        ic = IdentityCore()
        ic.record_action_stats('bash', True, count=3)
        ic.record_action_stats('python', False)
        inv = ic.get_real_capability_inventory()
        self.assertEqual(inv['total_proven'], 2)
        self.assertEqual(inv['proven'][0]['action'], 'bash')
        self.assertEqual(inv['proven'][0]['uses'], 3)
        self.assertIn('bash', inv['summary'])
        self.assertNotIn('python', inv['never_tried'])
        self.assertNotIn('bash', inv['never_tried'])

    def test_proven_fields(self):
        ic = IdentityCore()
        ic.record_action_stats('search', True)
        inv = ic.get_real_capability_inventory()
        p = inv['proven'][0]
        for key in ('action', 'uses', 'proficiency', 'last_used', 'description'):
            self.assertIn(key, p)

    def test_untested_default_caps_after_use(self):
        ic = IdentityCore()
        ic._capabilities['reasoning'].usage_count = 1
        inv = ic.get_real_capability_inventory()
        self.assertNotIn('reasoning', inv['untested'])
        self.assertEqual(len(inv['untested']), 7)


# ── Introspect ───────────────────────────────────────────────────────────────

class IntrospectTests(unittest.TestCase):

    def test_without_core(self):
        ic = IdentityCore(name="Bot")
        result = ic.introspect("Кто ты?")
        self.assertIn("Bot", result)

    def test_with_core(self):
        core = SimpleNamespace(reasoning=lambda x: "Я размышляю о себе")
        ic = IdentityCore(cognitive_core=core)
        result = ic.introspect("Кто ты?")
        self.assertEqual(result, "Я размышляю о себе")


# ── get_capabilities & summary ───────────────────────────────────────────────

class GetCapabilitiesTests(unittest.TestCase):

    def test_available_only(self):
        ic = IdentityCore()
        ic._capabilities['reasoning'].available = False
        caps = ic.get_capabilities(available_only=True)
        names = [c['name'] for c in caps]
        self.assertNotIn('reasoning', names)

    def test_all(self):
        ic = IdentityCore()
        ic._capabilities['reasoning'].available = False
        caps = ic.get_capabilities(available_only=False)
        names = [c['name'] for c in caps]
        self.assertIn('reasoning', names)


class SummaryTests(unittest.TestCase):

    def test_summary_keys(self):
        ic = IdentityCore()
        s = ic.summary()
        self.assertEqual(s['name'], 'Agent')
        self.assertEqual(s['role'], 'Autonomous AI Agent')
        self.assertEqual(s['capabilities'], 8)
        self.assertIsNone(s['success_rate'])
        self.assertEqual(s['values_count'], 5)

    def test_summary_with_history(self):
        ic = IdentityCore()
        ic._performance_history.append({'success': True})
        s = ic.summary()
        self.assertIsNotNone(s['success_rate'])


# ── _scan_module_file ────────────────────────────────────────────────────────

class ScanModuleFileTests(unittest.TestCase):
    """Test _scan_module_file directly for classification logic."""

    def _make_ic(self):
        ic = IdentityCore.__new__(IdentityCore)
        ic._capabilities = {}
        ic.monitoring = None
        ic.name = 'Test'
        return ic

    def _make_importlib_util_mock(self, docstring="Module doc"):
        mock_importlib_util = MagicMock()
        mock_spec = MagicMock()
        mock_spec.loader = MagicMock()
        mock_module = MagicMock()
        mock_module.__doc__ = docstring
        mock_importlib_util.spec_from_file_location.return_value = mock_spec
        mock_importlib_util.module_from_spec.return_value = mock_module
        return mock_importlib_util

    def _empty_discovery(self):
        return {'skills': [], 'agents': [], 'subsystems': [], 'tools': [], 'total_modules': 0}

    def test_skills_classification(self):
        ic = self._make_ic()
        d = self._empty_discovery()
        ic._scan_module_file('skills', '/fake/skills/s.py', 's.py', d, self._make_importlib_util_mock())
        self.assertEqual(len(d['skills']), 1)
        self.assertEqual(d['skills'][0]['name'], 's')
        self.assertEqual(d['total_modules'], 1)

    def test_agents_classification(self):
        ic = self._make_ic()
        d = self._empty_discovery()
        ic._scan_module_file('agents', '/fake/agents/a.py', 'a.py', d, self._make_importlib_util_mock())
        self.assertEqual(len(d['agents']), 1)

    def test_core_subsystem(self):
        ic = self._make_ic()
        d = self._empty_discovery()
        ic._scan_module_file('core', '/fake/core/brain.py', 'brain.py', d, self._make_importlib_util_mock())
        self.assertEqual(len(d['subsystems']), 1)
        self.assertEqual(d['subsystems'][0]['layer'], 3)
        self.assertEqual(d['subsystems'][0]['module'], 'core.brain')

    def test_execution_subsystem(self):
        ic = self._make_ic()
        d = self._empty_discovery()
        ic._scan_module_file('execution', '/f/execution/x.py', 'x.py', d, self._make_importlib_util_mock())
        self.assertEqual(len(d['subsystems']), 1)
        self.assertEqual(d['subsystems'][0]['layer'], 8)

    def test_knowledge_subsystem(self):
        ic = self._make_ic()
        d = self._empty_discovery()
        ic._scan_module_file('knowledge', '/f/knowledge/k.py', 'k.py', d, self._make_importlib_util_mock())
        self.assertEqual(d['subsystems'][0]['layer'], 2)

    def test_llm_subsystem(self):
        ic = self._make_ic()
        d = self._empty_discovery()
        ic._scan_module_file('llm', '/f/llm/l.py', 'l.py', d, self._make_importlib_util_mock())
        self.assertEqual(d['subsystems'][0]['layer'], 32)

    def test_learning_subsystem(self):
        ic = self._make_ic()
        d = self._empty_discovery()
        ic._scan_module_file('learning', '/f/learning/m.py', 'm.py', d, self._make_importlib_util_mock())
        self.assertEqual(d['subsystems'][0]['layer'], 9)

    def test_environment_subsystem(self):
        ic = self._make_ic()
        d = self._empty_discovery()
        ic._scan_module_file('environment', '/f/environment/e.py', 'e.py', d, self._make_importlib_util_mock())
        self.assertEqual(d['subsystems'][0]['layer'], 28)

    def test_tools_goes_to_else(self):
        """'tools' category is not in the subsystem list → goes to else → discovery['tools']"""
        ic = self._make_ic()
        d = self._empty_discovery()
        ic._scan_module_file('tools', '/f/tools/t.py', 't.py', d, self._make_importlib_util_mock())
        self.assertEqual(len(d['tools']), 1)
        self.assertEqual(d['tools'][0]['type'], 'tools')

    def test_self_improvement_goes_to_else(self):
        ic = self._make_ic()
        d = self._empty_discovery()
        ic._scan_module_file('self_improvement', '/f/si/o.py', 'o.py', d, self._make_importlib_util_mock())
        self.assertEqual(len(d['tools']), 1)
        self.assertEqual(d['tools'][0]['type'], 'self_improvement')

    def test_exception_during_scan(self):
        ic = self._make_ic()
        d = self._empty_discovery()
        mock_importlib_util = MagicMock()
        mock_importlib_util.spec_from_file_location.side_effect = OSError("boom")
        with patch('builtins.print'):
            ic._scan_module_file('core', '/f/core/x.py', 'x.py', d, mock_importlib_util)
        self.assertEqual(d['total_modules'], 0)

    def test_spec_none(self):
        ic = self._make_ic()
        d = self._empty_discovery()
        mock_importlib_util = MagicMock()
        mock_importlib_util.spec_from_file_location.return_value = None
        ic._scan_module_file('core', '/f/core/x.py', 'x.py', d, mock_importlib_util)
        self.assertEqual(d['total_modules'], 0)

    def test_spec_loader_none(self):
        ic = self._make_ic()
        d = self._empty_discovery()
        mock_importlib_util = MagicMock()
        mock_spec = MagicMock()
        mock_spec.loader = None
        mock_importlib_util.spec_from_file_location.return_value = mock_spec
        ic._scan_module_file('core', '/f/core/x.py', 'x.py', d, mock_importlib_util)
        self.assertEqual(d['total_modules'], 0)

    def test_no_docstring(self):
        ic = self._make_ic()
        d = self._empty_discovery()
        ic._scan_module_file('skills', '/f/skills/s.py', 's.py', d, self._make_importlib_util_mock(docstring=None))
        # docstring is None → 'No description' used, but split gives empty first line
        # Actually: module.__doc__ is None → we mock it as None
        # But MagicMock __doc__ = None → `module.__doc__ or 'No description'` → 'No description'
        self.assertEqual(d['skills'][0]['description'], 'No description')


# ── discover_modules ─────────────────────────────────────────────────────────

class DiscoverModulesTests(unittest.TestCase):

    def test_nonexistent_root(self):
        ic = IdentityCore()
        d = ic.discover_modules('/nonexistent_path_xyz')
        self.assertEqual(d['total_modules'], 0)
        self.assertEqual(d['summary'], 'Модули не обнаружены')

    def test_with_real_agent_root(self):
        ic = IdentityCore()
        d = ic.discover_modules('C:/Users/andre/Downloads/agent')
        self.assertGreater(d['total_modules'], 0)
        for key in ('skills', 'agents', 'subsystems', 'tools', 'total_modules', 'summary'):
            self.assertIn(key, d)

    def test_skips_dunder_and_non_py(self):
        ic = IdentityCore()
        with patch('os.path.isdir', return_value=True), \
             patch('os.listdir', return_value=['__init__.py', 'readme.md']):
            d = ic.discover_modules('/fake')
            self.assertEqual(d['total_modules'], 0)

    def test_summary_with_skills_agents_subsystems(self):
        """Verify summary generation when all three types present."""
        ic = IdentityCore()
        # Mock discover to return pre-built discovery
        fake_discovery = {
            'skills': [{'name': 'sk1'}],
            'agents': [{'name': 'ag1'}],
            'subsystems': [{'name': 'sub1'}],
            'tools': [],
            'total_modules': 3,
        }
        with patch.object(ic, '_scan_module_file') as mock_scan:
            def populate(cat, fp, fn, disc, imp):
                if cat == 'skills':
                    disc['skills'].append({'name': 'sk1'})
                    disc['total_modules'] += 1
                elif cat == 'agents':
                    disc['agents'].append({'name': 'ag1'})
                    disc['total_modules'] += 1
                elif cat == 'core':
                    disc['subsystems'].append({'name': 'sub1'})
                    disc['total_modules'] += 1
            mock_scan.side_effect = populate
            with patch('os.path.isdir', return_value=True), \
                 patch('os.listdir', return_value=['m.py']):
                d = ic.discover_modules('/fake')
                self.assertIn('Навыки', d['summary'])
                self.assertIn('Агенты', d['summary'])
                self.assertIn('Подсистемы', d['summary'])
                self.assertIn('|', d['summary'])


# ── _get_layer_number ────────────────────────────────────────────────────────

class GetLayerNumberTests(unittest.TestCase):

    def test_known_categories(self):
        ic = IdentityCore()
        self.assertEqual(ic._get_layer_number('core'), 3)
        self.assertEqual(ic._get_layer_number('knowledge'), 2)
        self.assertEqual(ic._get_layer_number('execution'), 8)
        self.assertEqual(ic._get_layer_number('learning'), 9)
        self.assertEqual(ic._get_layer_number('self_improvement'), 12)
        self.assertEqual(ic._get_layer_number('environment'), 28)
        self.assertEqual(ic._get_layer_number('tools'), 5)
        self.assertEqual(ic._get_layer_number('llm'), 32)

    def test_unknown_category(self):
        ic = IdentityCore()
        self.assertEqual(ic._get_layer_number('unknown'), 0)


# ── modules_status_report ────────────────────────────────────────────────────

class ModulesStatusReportTests(unittest.TestCase):

    def test_empty_report(self):
        ic = IdentityCore(name="TestAgent")
        report = ic.modules_status_report('/nonexistent_xyz')
        self.assertIn('TestAgent', report)
        self.assertIn('Всего модулей: 0', report)

    def test_report_with_all_sections(self):
        ic = IdentityCore()
        fake = {
            'skills': [{'name': 's1', 'description': 'skill desc'}],
            'agents': [{'name': 'a1', 'description': 'agent desc'}],
            'subsystems': [{'name': 'sub1', 'layer': 3, 'description': 'sub desc'}],
            'tools': [{'name': 't1', 'type': 'tools', 'description': 'tool desc'}],
            'total_modules': 4,
            'summary': 'test',
        }
        with patch.object(ic, 'discover_modules', return_value=fake):
            report = ic.modules_status_report('/fake')
            self.assertIn('НАВЫКИ', report)
            self.assertIn('s1', report)
            self.assertIn('АГЕНТЫ', report)
            self.assertIn('a1', report)
            self.assertIn('ПОДСИСТЕМЫ', report)
            self.assertIn('sub1', report)
            self.assertIn('ИНСТРУМЕНТЫ', report)
            self.assertIn('t1', report)


# ── _log ─────────────────────────────────────────────────────────────────────

class LogTests(unittest.TestCase):

    def test_log_with_monitoring(self):
        mon = MagicMock()
        ic = IdentityCore.__new__(IdentityCore)
        ic._capabilities = {}
        ic.monitoring = mon
        ic._log("test message")
        mon.info.assert_called_once_with("test message", source='identity')

    def test_log_without_monitoring(self):
        ic = IdentityCore.__new__(IdentityCore)
        ic._capabilities = {}
        ic.monitoring = None
        with patch('builtins.print') as mock_print:
            ic._log("hello")
            mock_print.assert_called_once()
            self.assertIn("hello", mock_print.call_args[0][0])


if __name__ == '__main__':
    unittest.main()
