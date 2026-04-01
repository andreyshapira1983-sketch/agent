import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.module_builder import ModuleBuilder
from monitoring.monitoring import Monitoring
from self_repair.self_repair import SelfRepairSystem


class SmokeHookTests(unittest.TestCase):
    def test_self_repair_smoke_success_records_log_and_metric(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, 'core'), exist_ok=True)
            smoke_runner = os.path.join(tmpdir, 'smoke_runner.py')
            with open(smoke_runner, 'w', encoding='utf-8') as f:
                f.write('print("ok")\n')

            monitoring = Monitoring(print_logs=False)
            repair = SelfRepairSystem(monitoring=monitoring, working_dir=tmpdir)
            target = os.path.join(tmpdir, 'core', 'cognitive_core.py')

            with patch('self_repair.self_repair.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout='OK', stderr='')):
                ok, _ = repair._run_core_smoke_if_needed(target, target + '.bak')

            self.assertTrue(ok)
            metric = monitoring.get_metric('core_smoke_passed')
            self.assertIsNotNone(metric)
            self.assertEqual(metric.count() if metric else 0, 1)
            logs = monitoring.get_logs(source='self_repair')
            self.assertTrue(any(entry['message'] == 'core_smoke_passed' for entry in logs))
            summary = monitoring.summary().get('core_smoke', {})
            self.assertEqual(summary.get('passed'), 1)
            self.assertEqual(summary.get('failed'), 0)
            self.assertEqual((summary.get('last_event') or {}).get('message'), 'core_smoke_passed')

    def test_module_builder_smoke_failure_records_log_and_metric(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, 'core'), exist_ok=True)
            smoke_runner = os.path.join(tmpdir, 'smoke_runner.py')
            with open(smoke_runner, 'w', encoding='utf-8') as f:
                f.write('print("ok")\n')

            monitoring = Monitoring(print_logs=False)
            builder = ModuleBuilder(monitoring=monitoring, working_dir=tmpdir)
            target = os.path.join(tmpdir, 'core', 'generated_module.py')

            with patch('core.module_builder.subprocess.run', return_value=SimpleNamespace(returncode=1, stdout='', stderr='boom')):
                ok, message = builder._run_core_smoke_if_needed(target)

            self.assertFalse(ok)
            self.assertIsNotNone(message)
            assert message is not None
            self.assertIn('smoke_runner провалился', message)
            metric = monitoring.get_metric('core_smoke_failed')
            self.assertIsNotNone(metric)
            self.assertEqual(metric.count() if metric else 0, 1)
            logs = monitoring.get_logs(source='module_builder')
            self.assertTrue(any(entry['message'] == 'core_smoke_failed' for entry in logs))
            summary = monitoring.summary().get('core_smoke', {})
            self.assertEqual(summary.get('failed'), 1)
            self.assertEqual((summary.get('last_event') or {}).get('message'), 'core_smoke_failed')

    def test_non_core_file_skips_smoke_runner(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            monitoring = Monitoring(print_logs=False)
            repair = SelfRepairSystem(monitoring=monitoring, working_dir=tmpdir)
            outside_file = os.path.join(tmpdir, 'tools', 'helper.py')

            with patch('self_repair.self_repair.subprocess.run') as run_mock:
                ok, message = repair._run_core_smoke_if_needed(outside_file, outside_file + '.bak')

            self.assertTrue(ok)
            self.assertIn('не требуется', message)
            run_mock.assert_not_called()
            self.assertIsNone(monitoring.get_metric('core_smoke_passed'))
            self.assertIsNone(monitoring.get_metric('core_smoke_failed'))


if __name__ == '__main__':
    unittest.main()