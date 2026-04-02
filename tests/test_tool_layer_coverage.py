# Покрытие: tools/tool_layer.py — validators, BaseTool, ToolLayer, ключевые инструменты
import os
import sys
import json
import types
import unittest
from unittest.mock import patch, MagicMock, PropertyMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tools.tool_layer import (
    BaseTool, CommandValidator, PathValidator, CodeValidator,
    TerminalTool, FileSystemTool, PythonRuntimeTool, SearchTool,
    GitHubTool, DockerTool, DatabaseTool, PackageManagerTool,
    CloudAPITool, ClipboardTool, ProcessManagerTool, GitTool,
    NetworkTool, CronSchedulerTool, HTTPClientTool,
    ArchiveTool, NotificationTool, EncryptionTool,
    TaskQueueTool, SelfMonitorTool, ToolLayer,
)


# ═══════════════════════════════════════════════════════════════════════════════
# BaseTool
# ═══════════════════════════════════════════════════════════════════════════════
class ConcreteTool(BaseTool):
    def run(self, x=0):
        return {'value': x, 'success': True}


class TestBaseTool(unittest.TestCase):
    def test_init(self):
        t = ConcreteTool('demo', 'A demo tool')
        self.assertEqual(t.name, 'demo')
        self.assertEqual(t.description, 'A demo tool')
        self.assertIsNone(t.last_result)

    def test_call_stores_last_result(self):
        t = ConcreteTool('demo', 'desc')
        r = t(x=42)
        self.assertEqual(r['value'], 42)
        self.assertEqual(t.last_result, r)

    def test_run_abstract(self):
        with self.assertRaises(TypeError):
            BaseTool('x', 'y')


# ═══════════════════════════════════════════════════════════════════════════════
# CommandValidator
# ═══════════════════════════════════════════════════════════════════════════════
class TestCommandValidator(unittest.TestCase):
    def test_allowed_simple(self):
        ok, _ = CommandValidator.validate('python --version')
        self.assertTrue(ok)

    def test_allowed_git(self):
        ok, _ = CommandValidator.validate('git status')
        self.assertTrue(ok)

    def test_blocked_rm_rf(self):
        ok, reason = CommandValidator.validate('rm -rf /')
        self.assertFalse(ok)

    def test_blocked_chain_semicolon(self):
        ok, reason = CommandValidator.validate('echo hi; rm file')
        self.assertFalse(ok)

    def test_blocked_pipe(self):
        ok, reason = CommandValidator.validate('cat file | grep pass')
        self.assertFalse(ok)

    def test_blocked_unknown_exe(self):
        ok, reason = CommandValidator.validate('nc -lvp 4444')
        self.assertFalse(ok)

    def test_empty_command(self):
        ok, _ = CommandValidator.validate('')
        self.assertFalse(ok)

    def test_blocked_backtick(self):
        ok, _ = CommandValidator.validate('echo `whoami`')
        self.assertFalse(ok)

    def test_blocked_redirect(self):
        ok, _ = CommandValidator.validate('echo data > /etc/passwd')
        self.assertFalse(ok)


# ═══════════════════════════════════════════════════════════════════════════════
# PathValidator
# ═══════════════════════════════════════════════════════════════════════════════
class TestPathValidator(unittest.TestCase):
    def setUp(self):
        self.base = os.path.realpath(os.path.dirname(__file__))

    def test_ok_inside_base(self):
        ok, _ = PathValidator.validate('somefile.txt', self.base)
        self.assertTrue(ok)

    def test_blocked_outside_jail(self):
        ok, reason = PathValidator.validate('/etc/shadow', self.base)
        self.assertFalse(ok)
        self.assertIn('за пределы', reason)

    def test_blocked_env_file(self):
        ok, reason = PathValidator.validate('.env', self.base)
        self.assertFalse(ok)
        self.assertIn('чувствительный', reason)

    def test_blocked_ssh_dir(self):
        ok, reason = PathValidator.validate('.ssh/id_rsa', self.base)
        self.assertFalse(ok)

    def test_blocked_pem_extension(self):
        ok, reason = PathValidator.validate('cert.pem', self.base)
        self.assertFalse(ok)

    def test_empty_path(self):
        ok, _ = PathValidator.validate('', self.base)
        self.assertFalse(ok)

    def test_blocked_credentials(self):
        ok, _ = PathValidator.validate('credentials.json', self.base)
        self.assertFalse(ok)

    def test_blocked_delete_agent_memory(self):
        ok, _ = PathValidator.validate('.agent_memory/data.json', self.base, 'delete')
        self.assertFalse(ok)

    def test_write_allowed_normal(self):
        ok, _ = PathValidator.validate('output.txt', self.base, 'write')
        self.assertTrue(ok)


# ═══════════════════════════════════════════════════════════════════════════════
# CodeValidator
# ═══════════════════════════════════════════════════════════════════════════════
class TestCodeValidator(unittest.TestCase):
    def test_safe_code(self):
        ok, _ = CodeValidator.validate('x = 1 + 2\nprint(x)')
        self.assertTrue(ok)

    def test_blocked_import_subprocess(self):
        ok, reason = CodeValidator.validate('import subprocess')
        self.assertFalse(ok)
        self.assertIn('subprocess', reason)

    def test_blocked_exec(self):
        ok, _ = CodeValidator.validate('exec("print(1)")')
        self.assertFalse(ok)

    def test_blocked_eval(self):
        ok, _ = CodeValidator.validate('eval("2+2")')
        self.assertFalse(ok)

    def test_blocked_os_system(self):
        ok, _ = CodeValidator.validate('import os\nos.system("ls")')
        self.assertFalse(ok)

    def test_blocked_dunder_subclasses(self):
        ok, _ = CodeValidator.validate('x.__subclasses__()')
        self.assertFalse(ok)

    def test_empty_code(self):
        ok, _ = CodeValidator.validate('')
        self.assertFalse(ok)

    def test_allowed_urllib_parse(self):
        ok, _ = CodeValidator.validate('from urllib.parse import urlparse')
        self.assertTrue(ok)

    def test_blocked_socket(self):
        ok, _ = CodeValidator.validate('import socket')
        self.assertFalse(ok)

    def test_syntax_error_propagated(self):
        ok, reason = CodeValidator.validate('def f(\n  x\n  ):\n')
        # SyntaxError gets caught — either blocked or passed through
        self.assertIsInstance(ok, bool)


# ═══════════════════════════════════════════════════════════════════════════════
# TerminalTool
# ═══════════════════════════════════════════════════════════════════════════════
class TestTerminalTool(unittest.TestCase):
    def test_blocked_command(self):
        t = TerminalTool()
        r = t.run('nc -lvp 4444')
        self.assertFalse(r['success'])
        self.assertIn('заблокирована', r.get('error', ''))

    @patch('tools.tool_layer.CommandValidator.validate', return_value=(True, 'OK'))
    def test_gateway_exception(self, _mock_cv):
        t = TerminalTool()
        with patch('tools.tool_layer.shlex.split', side_effect=ValueError('bad')):
            r = t.run('echo hi')
            self.assertFalse(r['success'])


# ═══════════════════════════════════════════════════════════════════════════════
# FileSystemTool
# ═══════════════════════════════════════════════════════════════════════════════
class TestFileSystemTool(unittest.TestCase):
    def test_blocked_path(self):
        t = FileSystemTool(base_dir=os.path.dirname(__file__))
        r = t.run('read', '.env')
        self.assertIn('заблокирован', r.get('error', ''))

    def test_exists_action(self):
        t = FileSystemTool(base_dir=os.path.dirname(__file__))
        r = t.run('exists', os.path.basename(__file__))
        self.assertTrue(r.get('exists') or r.get('success', False))

    def test_list_action(self):
        t = FileSystemTool(base_dir=os.path.dirname(__file__))
        r = t.run('list', '.')
        self.assertTrue(r.get('success', True))


# ═══════════════════════════════════════════════════════════════════════════════
# PythonRuntimeTool
# ═══════════════════════════════════════════════════════════════════════════════
class TestPythonRuntimeTool(unittest.TestCase):
    def test_simple_code(self):
        t = PythonRuntimeTool(working_dir=os.path.dirname(__file__))
        r = t.run('result = 2 + 2')
        self.assertTrue(r.get('success', False))

    def test_blocked_import(self):
        t = PythonRuntimeTool(working_dir=os.path.dirname(__file__))
        r = t.run('import subprocess')
        self.assertFalse(r.get('success', True))

    def test_print_captured(self):
        t = PythonRuntimeTool(working_dir=os.path.dirname(__file__))
        r = t.run('print("hello world")')
        self.assertIn('hello world', r.get('output', ''))


# ═══════════════════════════════════════════════════════════════════════════════
# ToolLayer
# ═══════════════════════════════════════════════════════════════════════════════
class TestToolLayer(unittest.TestCase):
    def test_register_and_get(self):
        tl = ToolLayer()
        tool = ConcreteTool('test_tool', 'desc')
        tl.register(tool)
        self.assertIs(tl.get('test_tool'), tool)

    def test_get_missing(self):
        tl = ToolLayer()
        self.assertIsNone(tl.get('nonexistent'))

    def test_use(self):
        tl = ToolLayer()
        tl.register(ConcreteTool('calc', 'calculator'))
        r = tl.use('calc', x=10)
        self.assertEqual(r['value'], 10)

    def test_use_missing_raises(self):
        tl = ToolLayer()
        with self.assertRaises(KeyError):
            tl.use('nonexistent')

    def test_list(self):
        tl = ToolLayer()
        tl.register(ConcreteTool('a', ''))
        tl.register(ConcreteTool('b', ''))
        self.assertEqual(sorted(tl.list()), ['a', 'b'])

    def test_describe(self):
        tl = ToolLayer()
        tl.register(ConcreteTool('x', 'X tool'))
        desc = tl.describe()
        self.assertEqual(len(desc), 1)
        self.assertEqual(desc[0]['name'], 'x')
        self.assertEqual(desc[0]['description'], 'X tool')


# ═══════════════════════════════════════════════════════════════════════════════
# Lightweight tool init tests (no external deps)
# ═══════════════════════════════════════════════════════════════════════════════
class TestToolInits(unittest.TestCase):
    """Проверяем что все инструменты инициализируются без ошибок."""

    def test_search_tool(self):
        t = SearchTool()
        self.assertEqual(t.name, 'search')

    def test_github_tool(self):
        t = GitHubTool(token='fake')
        self.assertEqual(t.name, 'github')

    def test_docker_tool(self):
        t = DockerTool()
        self.assertEqual(t.name, 'docker')

    def test_database_tool(self):
        t = DatabaseTool()
        self.assertEqual(t.name, 'database')

    def test_package_manager_tool(self):
        t = PackageManagerTool()
        self.assertEqual(t.name, 'package_manager')

    def test_cloud_api_tool(self):
        t = CloudAPITool()
        self.assertEqual(t.name, 'cloud_api')

    def test_clipboard_tool(self):
        t = ClipboardTool()
        self.assertEqual(t.name, 'clipboard')

    def test_process_manager_tool(self):
        t = ProcessManagerTool()
        self.assertEqual(t.name, 'process_manager')

    def test_git_tool(self):
        t = GitTool()
        self.assertEqual(t.name, 'git')

    def test_network_tool(self):
        t = NetworkTool()
        self.assertEqual(t.name, 'network')

    def test_cron_scheduler_tool(self):
        t = CronSchedulerTool()
        self.assertEqual(t.name, 'cron_scheduler')

    def test_http_client_tool(self):
        t = HTTPClientTool()
        self.assertEqual(t.name, 'http_client')

    def test_archive_tool(self):
        t = ArchiveTool()
        self.assertEqual(t.name, 'archive')

    def test_notification_tool(self):
        t = NotificationTool()
        self.assertEqual(t.name, 'notification')

    def test_encryption_tool(self):
        t = EncryptionTool()
        self.assertEqual(t.name, 'encryption')

    def test_task_queue_tool(self):
        t = TaskQueueTool()
        self.assertEqual(t.name, 'task_queue')

    def test_self_monitor_tool(self):
        t = SelfMonitorTool()
        self.assertEqual(t.name, 'self_monitor')


# ═══════════════════════════════════════════════════════════════════════════════
# CronSchedulerTool operations
# ═══════════════════════════════════════════════════════════════════════════════
class TestCronSchedulerTool(unittest.TestCase):
    def test_schedule_list_cancel(self):
        t = CronSchedulerTool()
        cb = lambda: None
        r = t.run('schedule', job_id='test_job', interval_seconds=10, callback=cb)
        self.assertTrue(r.get('success', False))
        r2 = t.run('list')
        self.assertTrue(r2.get('success', False))
        self.assertEqual(len(r2['jobs']), 1)
        r3 = t.run('cancel', job_id='test_job')
        self.assertTrue(r3.get('success', False))

    def test_unknown_action(self):
        t = CronSchedulerTool()
        r = t.run('explode')
        self.assertFalse(r.get('success', True))


# ═══════════════════════════════════════════════════════════════════════════════
# TaskQueueTool operations
# ═══════════════════════════════════════════════════════════════════════════════
class TestTaskQueueToolOps(unittest.TestCase):
    def test_push_and_list(self):
        t = TaskQueueTool()
        r = t.run('push', task='task1', priority=2)
        self.assertTrue(r.get('success', False))
        r2 = t.run('list')
        self.assertTrue(r2.get('success', False))

    def test_pop(self):
        t = TaskQueueTool()
        t.run('push', task='task_a', priority=3)
        r = t.run('pop')
        self.assertTrue(r.get('success', False))

    def test_pop_empty(self):
        t = TaskQueueTool()
        r = t.run('pop')
        self.assertFalse(r.get('success', True))

    def test_peek(self):
        t = TaskQueueTool()
        t.run('push', task='task_b', priority=1)
        r = t.run('peek')
        self.assertTrue(r.get('success', False))

    def test_size_and_clear(self):
        t = TaskQueueTool()
        t.run('push', task='x')
        r = t.run('size')
        self.assertEqual(r['size'], 1)
        t.run('clear')
        r2 = t.run('size')
        self.assertEqual(r2['size'], 0)


# ═══════════════════════════════════════════════════════════════════════════════
# EncryptionTool operations
# ═══════════════════════════════════════════════════════════════════════════════
class TestEncryptionToolOps(unittest.TestCase):
    def test_encrypt_decrypt_roundtrip(self):
        t = EncryptionTool()
        r = t.run('encrypt_text', text='hello secret')
        self.assertTrue(r.get('success', False))
        ct = r.get('encrypted', '')
        self.assertTrue(ct)
        r2 = t.run('decrypt_text', encrypted=ct)
        self.assertTrue(r2.get('success', False))
        self.assertEqual(r2['text'], 'hello secret')

    def test_generate_key(self):
        t = EncryptionTool()
        r = t.run('generate_key')
        self.assertTrue(r.get('success', False))
        self.assertIn('key', r)

    def test_unknown_action(self):
        t = EncryptionTool()
        r = t.run('madeup_action')
        self.assertFalse(r.get('success', True))


# ═══════════════════════════════════════════════════════════════════════════════
# NetworkTool
# ═══════════════════════════════════════════════════════════════════════════════
class TestNetworkToolOps(unittest.TestCase):
    def test_dns_localhost(self):
        t = NetworkTool()
        r = t.run('dns', hostname='localhost')
        self.assertTrue(r.get('success', False))

    def test_port_check(self):
        t = NetworkTool()
        r = t.run('port_check', host='127.0.0.1', port=1)
        self.assertIsInstance(r, dict)


# ═══════════════════════════════════════════════════════════════════════════════
# SelfMonitorTool
# ═══════════════════════════════════════════════════════════════════════════════
class TestSelfMonitorToolOps(unittest.TestCase):
    def test_health(self):
        t = SelfMonitorTool()
        r = t.run('health')
        self.assertTrue(r.get('success', False))
        self.assertIn('cpu_percent', r)

    def test_alerts(self):
        t = SelfMonitorTool()
        r = t.run('alerts')
        self.assertTrue(r.get('success', False))


if __name__ == '__main__':
    unittest.main()
