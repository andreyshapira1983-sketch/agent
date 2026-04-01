import unittest
from typing import cast
from types import SimpleNamespace
from unittest.mock import patch

from agents.agent_spawner import AgentSpawner


class _TestableAgentSpawner(AgentSpawner):
    def instantiate_public(self, mod, class_name, cognitive_core=None, tools=None):
        return self._instantiate(mod, class_name, cognitive_core, tools)

    def register_in_manager_public(self, instance):
        return self._register_in_manager(instance)

    def log_public(self, msg):
        return self._log(msg)


class _Mon:
    def __init__(self, fail=False):
        self.fail = fail
        self.events = []

    def info(self, msg, source=''):
        if self.fail:
            raise RuntimeError('monitor down')
        self.events.append((msg, source))


class _CtorBoth:
    def __init__(self, cognitive_core=None, tools=None):
        self.cc = cognitive_core
        self.tools = tools


class _CtorCcOnly:
    def __init__(self, cognitive_core=None):
        self.cc = cognitive_core


class _CtorNoArgs:
    def __init__(self):
        self.ok = True


class _CtorTypeThenFail:
    def __init__(self):
        raise RuntimeError('boom')


class _ManagerOk:
    def __init__(self):
        self.items = []

    def register(self, instance):
        self.items.append(instance)


class _ManagerFail:
    def register(self, instance):
        _ = instance
        raise RuntimeError('reg fail')


class _AgentWithName:
    def __init__(self, name='alpha'):
        self.name = name


class _AgentNoName:
    pass


class AgentSpawnerCoverageTests(unittest.TestCase):
    def test_build_and_register_without_module_builder(self):
        sp = AgentSpawner(module_builder=None)
        out = sp.build_and_register('x', 'd')
        self.assertFalse(out['ok'])
        self.assertIn('module_builder', out['error'])

    def test_build_and_register_builder_error(self):
        mb = SimpleNamespace(build_agent=lambda n, d: SimpleNamespace(ok=False, error='nope'))
        sp = AgentSpawner(module_builder=mb)
        out = sp.build_and_register('x', 'd')
        self.assertFalse(out['ok'])
        self.assertEqual(out['error'], 'nope')

    def test_build_and_register_instantiate_fail(self):
        mod = SimpleNamespace(Bad=_CtorTypeThenFail)
        mb = SimpleNamespace(
            build_agent=lambda n, d: SimpleNamespace(
                ok=True,
                error=None,
                module_object=mod,
                class_name='Bad',
                file_path='dynamic/bad.py',
            )
        )
        sp = AgentSpawner(module_builder=mb)
        out = sp.build_and_register('bad', 'desc', cognitive_core=object(), tools=object())
        self.assertFalse(out['ok'])
        self.assertIn('Не удалось создать экземпляр', out['error'])

    def test_build_and_register_success_and_manager_register(self):
        mod = SimpleNamespace(Good=_CtorBoth)
        manager = _ManagerOk()
        mb = SimpleNamespace(
            build_agent=lambda n, d: SimpleNamespace(
                ok=True,
                error=None,
                module_object=mod,
                class_name='Good',
                file_path='dynamic/good.py',
            )
        )
        sp = AgentSpawner(module_builder=mb, agent_system=manager)
        out = sp.build_and_register('good', 'desc', cognitive_core='cc', tools='tools')

        self.assertTrue(out['ok'])
        rows = {row['name']: row for row in sp.list_dynamic()}
        self.assertIn('good', rows)
        self.assertEqual(rows['good']['class_name'], 'Good')
        self.assertEqual(rows['good']['file_path'], 'dynamic/good.py')
        self.assertEqual(len(manager.items), 1)
        created = manager.items[0]
        self.assertIsInstance(created, _CtorBoth)
        self.assertEqual(created.cc, 'cc')
        self.assertEqual(created.tools, 'tools')

    def test_register_instance_without_name(self):
        sp = AgentSpawner()
        self.assertFalse(sp.register_instance(_AgentNoName()))

    def test_register_instance_success(self):
        manager = _ManagerOk()
        sp = AgentSpawner(agent_system=manager)
        obj = _AgentWithName('agent1')
        self.assertTrue(sp.register_instance(obj))
        self.assertIs(sp.get('agent1'), obj)
        self.assertEqual(len(manager.items), 1)

    def test_restore_from_registry_without_module_builder(self):
        sp = AgentSpawner(module_builder=None)
        self.assertIsNone(sp.restore_from_registry())

    def test_restore_from_registry_skips_invalid_and_loads_valid(self):
        mod = SimpleNamespace(C1=_CtorNoArgs)

        class _MB:
            def load_all_from_registry(self):
                return {
                    'agents': [
                        {'name': 'skip1', 'module': None, 'class_name': 'C1'},
                        {'name': 'skip2', 'module': mod, 'class_name': ''},
                        {'name': 'ok1', 'module': mod, 'class_name': 'C1', 'file_path': 'f.py'},
                    ]
                }

        mon = _Mon()
        manager = _ManagerOk()
        sp = AgentSpawner(module_builder=_MB(), agent_system=manager, monitoring=mon)
        sp.restore_from_registry()

        rows = {row['name']: row for row in sp.list_dynamic()}
        self.assertIn('ok1', rows)
        self.assertEqual(rows['ok1']['file_path'], 'f.py')
        self.assertEqual(len(manager.items), 1)
        self.assertTrue(any('Восстановлено 1 динамических агентов' in e[0] for e in mon.events))

    def test_spawn_from_entry_without_builder(self):
        sp = AgentSpawner(module_builder=None)
        self.assertIsNone(sp.spawn_from_entry({'name': 'x', 'class_name': 'C', 'file_path': 'x.py'}))

    def test_spawn_from_entry_with_missing_fields(self):
        sp = AgentSpawner(module_builder=SimpleNamespace())
        self.assertIsNone(sp.spawn_from_entry({'name': 'x', 'class_name': '', 'file_path': 'x.py'}))
        self.assertIsNone(sp.spawn_from_entry({'name': 'x', 'class_name': 'C', 'file_path': ''}))

    def test_spawn_from_entry_without_import_method(self):
        mon = _Mon()
        sp = AgentSpawner(module_builder=SimpleNamespace(), monitoring=mon)
        self.assertIsNone(sp.spawn_from_entry({'name': 'x', 'class_name': 'C', 'file_path': 'x.py'}))
        self.assertTrue(any('не имеет метода _import_file' in e[0] for e in mon.events))

    def test_spawn_from_entry_import_returns_none(self):
        mb = SimpleNamespace(_import_file=lambda file_path, name: None)
        sp = AgentSpawner(module_builder=mb)
        self.assertIsNone(sp.spawn_from_entry({'name': 'x', 'class_name': 'C', 'file_path': 'x.py'}))

    def test_spawn_from_entry_success(self):
        mod = SimpleNamespace(C1=_CtorCcOnly)
        mb = SimpleNamespace(_import_file=lambda file_path, name: mod)
        sp = AgentSpawner(module_builder=mb)
        inst = sp.spawn_from_entry(
            {'name': 'x', 'class_name': 'C1', 'file_path': 'x.py'},
            cognitive_core='cc',
            tools='tools',
        )
        self.assertIsInstance(inst, _CtorCcOnly)
        typed_inst = cast(_CtorCcOnly, inst)
        self.assertEqual(typed_inst.cc, 'cc')

    def test_list_dynamic_and_get(self):
        sp = AgentSpawner()
        with patch('agents.agent_spawner.time.time', return_value=123.0):
            self.assertTrue(sp.register_instance(_AgentWithName('x')))
        rows = sp.list_dynamic()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['name'], 'x')
        self.assertEqual(rows[0]['class_name'], '_AgentWithName')
        self.assertIsNone(rows[0]['file_path'])
        self.assertEqual(rows[0]['created_at'], 123.0)
        self.assertIsNotNone(sp.get('x'))
        self.assertIsNone(sp.get('missing'))

    def test_instantiate_missing_class(self):
        mon = _Mon()
        sp = _TestableAgentSpawner(monitoring=mon)
        self.assertIsNone(sp.instantiate_public(SimpleNamespace(), 'Nope', None, None))
        self.assertTrue(any("Класс 'Nope' не найден" in e[0] for e in mon.events))

    def test_instantiate_first_constructor_path(self):
        sp = _TestableAgentSpawner()
        mod = SimpleNamespace(C=_CtorBoth)
        inst = sp.instantiate_public(mod, 'C', 'cc', 'tools')
        self.assertIsNotNone(inst)
        self.assertIsInstance(inst, _CtorBoth)
        typed_inst = cast(_CtorBoth, inst)
        self.assertEqual(typed_inst.cc, 'cc')
        self.assertEqual(typed_inst.tools, 'tools')

    def test_instantiate_fallback_to_cc_only(self):
        sp = _TestableAgentSpawner()
        mod = SimpleNamespace(C=_CtorCcOnly)
        inst = sp.instantiate_public(mod, 'C', 'cc', 'tools')
        self.assertIsNotNone(inst)
        self.assertIsInstance(inst, _CtorCcOnly)
        typed_inst = cast(_CtorCcOnly, inst)
        self.assertEqual(typed_inst.cc, 'cc')

    def test_instantiate_fallback_to_no_args(self):
        sp = _TestableAgentSpawner()
        mod = SimpleNamespace(C=_CtorNoArgs)
        inst = sp.instantiate_public(mod, 'C', 'cc', 'tools')
        self.assertIsNotNone(inst)
        self.assertIsInstance(inst, _CtorNoArgs)

    def test_instantiate_all_fail_logs(self):
        mon = _Mon()
        sp = _TestableAgentSpawner(monitoring=mon)
        mod = SimpleNamespace(C=_CtorTypeThenFail)
        self.assertIsNone(sp.instantiate_public(mod, 'C', 'cc', 'tools'))
        self.assertTrue(any('Не удалось создать экземпляр C' in e[0] for e in mon.events))

    def test_register_in_manager_none(self):
        sp = _TestableAgentSpawner(agent_system=None)
        self.assertIsNone(sp.register_in_manager_public(object()))

    def test_register_in_manager_direct_success(self):
        manager = _ManagerOk()
        sp = _TestableAgentSpawner(agent_system=manager)
        obj = object()
        sp.register_in_manager_public(obj)
        self.assertEqual(len(manager.items), 1)

    def test_register_in_manager_direct_exception_logged(self):
        mon = _Mon()
        sp = _TestableAgentSpawner(agent_system=_ManagerFail(), monitoring=mon)
        sp.register_in_manager_public(object())
        self.assertTrue(any('Ошибка регистрации' in e[0] for e in mon.events))

    def test_register_in_manager_via_agent_system_handle(self):
        manager = _ManagerOk()
        agent_system = SimpleNamespace(handle=lambda *a, **k: None, _manager=manager)
        sp = _TestableAgentSpawner(agent_system=agent_system)
        sp.register_in_manager_public(object())
        self.assertEqual(len(manager.items), 1)

    def test_register_in_manager_no_register_method(self):
        agent_system = SimpleNamespace(handle=lambda *a, **k: None, _manager=SimpleNamespace())
        sp = _TestableAgentSpawner(agent_system=agent_system)
        self.assertIsNone(sp.register_in_manager_public(object()))

    def test_log_with_monitoring(self):
        mon = _Mon()
        sp = _TestableAgentSpawner(monitoring=mon)
        sp.log_public('hello')
        self.assertEqual(mon.events[-1], ('hello', 'agent_spawner'))

    def test_log_with_monitoring_exception(self):
        sp = _TestableAgentSpawner(monitoring=_Mon(fail=True))
        # Должно молча проглотить ошибку мониторинга
        self.assertIsNone(sp.log_public('hello'))

    def test_log_without_monitoring_prints(self):
        sp = _TestableAgentSpawner(monitoring=None)
        with patch('builtins.print') as p:
            sp.log_public('hello')
        p.assert_called_once_with('hello')


if __name__ == '__main__':
    unittest.main()
