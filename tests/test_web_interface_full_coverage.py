# pyright: reportPrivateUsage=false, reportAttributeAccessIssue=false, reportAssignmentType=false, reportArgumentType=false, reportOptionalMemberAccess=false
# pylint: disable=protected-access,unused-argument,redefined-builtin
# ruff: noqa: SLF001, ARG001

import io
import os
import types
import unittest
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from communication.web_interface import WebInterface


class _ToolLayer:
    def __init__(self):
        self.calls = []

    def use(self, tool, **kwargs):
        self.calls.append((tool, kwargs))
        action = kwargs.get('action')
        if tool == 'process_manager' and action == 'system':
            return {
                'success': True,
                'cpu_percent': 1,
                'memory_percent': 2,
                'memory_available_mb': 3,
                'disk_percent': 4,
                'disk_free_gb': 5,
            }
        if tool == 'process_manager' and action == 'list':
            return {'success': True, 'total': 1, 'processes': [{'pid': 1, 'name': 'p', 'cpu_percent': 0.1, 'memory_percent': 0.2}]}
        if tool == 'pdf_generator':
            return {'success': True, 'path': kwargs.get('output', 'x.pdf')}
        if tool == 'search':
            return {'success': True, 'results': [{'title': 't1', 'url': 'u1'}, {'title': 't2', 'url': 'u2'}]}
        if tool == 'screenshot':
            return {'success': True, 'path': kwargs.get('save_path', 'x.png')}
        if tool == 'spreadsheet':
            return {'success': True, 'path': kwargs.get('path', 'x.xlsx')}
        if tool == 'translate':
            return {'success': True, 'translated': 'hello'}
        if tool == 'powerpoint':
            return {'success': True, 'path': kwargs.get('output', 'x.pptx')}
        if tool == 'git':
            return {'success': True, 'stdout': 'ok'}
        if tool == 'network':
            if action == 'ping':
                return {'success': True, 'reachable': True, 'stdout': 'pong'}
            if action == 'dns':
                return {'success': True, 'hostname': 'h', 'ip': '1.1.1.1'}
            if action == 'port_check':
                return {'success': True, 'open': False, 'port': kwargs.get('port', 80), 'host': kwargs.get('host', 'localhost')}
            if action == 'http':
                return {'success': True, 'status_code': 200, 'content': 'ok'}
        if tool == 'filesystem':
            if action == 'write':
                return {'success': True, 'written': kwargs.get('path', 'f.txt')}
            if action == 'read':
                return {'success': True, 'content': 'abc'}
            if action == 'list':
                return {'success': True, 'entries': ['a', 'b']}
        if tool == 'python':
            return {'success': True, 'output': 'py ok'}
        if tool == 'terminal':
            return {'success': True, 'stdout': 'term ok'}
        if tool == 'package_manager':
            return {'success': True}
        if tool == 'clipboard' and action == 'write':
            return {'success': True}
        if tool == 'clipboard' and action == 'read':
            return {'success': True, 'text': 'clip'}
        return {'success': False, 'error': 'x'}


class _Loop:
    def __init__(self):
        self._cycle_count = 5
        self._running = True
        self._goal = 'g'
        self.tool_layer = _ToolLayer()
        self.set_goal = MagicMock()


class _Core:
    def converse(self, message, system='', history=None):
        _ = (system, history)
        return f'reply:{message}'


class _Monitoring:
    def __init__(self):
        self.events = []

    def info(self, msg, source=''):
        self.events.append(('info', msg, source))

    def warning(self, msg, source=''):
        self.events.append(('warning', msg, source))

    def error(self, msg, source=''):
        self.events.append(('error', msg, source))

    def summary(self):
        return {'core_smoke': {'passed': 1}}


class _PB:
    def summary(self, **kwargs):
        _ = kwargs
        return {'s': 1}

    def compact_status_text(self, **kwargs):
        _ = kwargs
        return 'pb'

    def record_conversation(self, **kwargs):
        _ = kwargs


class _RespCtx:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        _ = (exc_type, exc, tb)
        return False

    def read(self) -> bytes:
        return b'{"rates":{"ILS":3.7},"date":"2026-01-01"}'


class WebInterfaceCoverageTests(unittest.TestCase):
    def make_wi(self):
        return WebInterface(
            host='127.0.0.1',
            port=8000,
            cognitive_core=_Core(),
            autonomous_loop=_Loop(),
            goal_manager=MagicMock(),
            monitoring=_Monitoring(),
            persistent_brain=_PB(),
            max_history=3,
        )

    def _mk_handler_obj(self, wi, raw_body_methods=False):
        handler_cls = wi._make_handler()
        h = handler_cls.__new__(handler_cls)
        h.headers = {}
        h.path = '/'
        h.rfile = io.BytesIO()
        h.wfile = io.BytesIO()
        h._sent = []
        h.send_response = lambda code: h._sent.append(('code', code))
        h.send_header = lambda k, v: h._sent.append(('header', k, v))
        h.end_headers = lambda: h._sent.append(('end',))
        if not raw_body_methods:
            h._send_json = lambda data, code=200: h._sent.append(('json', code, data))
            h._send_html = lambda html: h._sent.append(('html', html[:20]))
        return h

    def test_init_env_branches(self):
        with patch.dict(os.environ, {'WEB_TOKEN': 'fixed-token', 'WEB_CHAT_TIMEOUT_SEC': '11'}, clear=False):
            wi = WebInterface()
            self.assertEqual(wi._token, 'fixed-token')
            self.assertEqual(wi._chat_timeout_sec, 11.0)

        with patch.dict(os.environ, {'WEB_CHAT_TIMEOUT_SEC': 'bad-timeout'}, clear=False):
            wi2 = WebInterface()
            self.assertEqual(wi2._chat_timeout_sec, 240.0)

    def test_auth_helpers_and_history_log(self):
        wi = self.make_wi()
        self.assertTrue(wi._check_token(wi._token))
        self.assertFalse(wi._check_token(''))

        req = types.SimpleNamespace(headers={'X-Agent-Token': wi._token})
        self.assertEqual(wi._token_from_request(req), wi._token)
        req2 = types.SimpleNamespace(headers={'Cookie': f'a=1; agent_token={wi._token}; b=2'})
        self.assertEqual(wi._token_from_request(req2), wi._token)
        req3 = types.SimpleNamespace(headers={'Cookie': 'a=1; b=2'})
        self.assertEqual(wi._token_from_request(req3), '')

        wi._add_history('user', '1')
        wi._add_history('user', '2')
        wi._add_history('user', '3')
        wi._add_history('user', '4')
        self.assertEqual(len(wi._history), 3)
        wi._log('hello')
        self.assertTrue(wi.monitoring.events)

        wi2 = WebInterface(monitoring=None)
        with patch('builtins.print') as p:
            wi2._log('x')
        p.assert_called_once()

    def test_start_stop_and_server_error_paths(self):
        wi = self.make_wi()

        class _Srv:
            def __init__(self, *_args, **_kwargs):
                self.shutdown_called = False
                self.handle_error_calls = 0

            def serve_forever(self):
                return

            def shutdown(self):
                self.shutdown_called = True

            def handle_error(self, request, client_address):
                _ = (request, client_address)
                self.handle_error_calls += 1

        with patch('communication.web_interface.ThreadingHTTPServer', _Srv):
            with patch('communication.web_interface.threading.Thread') as t:
                t.return_value.start = MagicMock()
                wi.start()
                self.assertIsNotNone(wi._server)

                # branch: suppressed connection reset/aborted
                with patch('sys.exc_info', return_value=(None, ConnectionResetError(), None)):
                    wi._server.handle_error(None, None)

                # branch: delegate to super handle_error
                with patch('sys.exc_info', return_value=(None, RuntimeError('x'), None)):
                    wi._server.handle_error(None, None)

                wi.stop()

        class _Exec1:
            def shutdown(self, wait=False, **kwargs):
                _ = wait
                if 'cancel_futures' in kwargs:
                    raise TypeError('x')
                return None

        class _Exec2:
            def __init__(self):
                self.calls = 0

            def shutdown(self, wait=False, cancel_futures=True):
                _ = cancel_futures
                self.calls += 1
                return wait

        wi._chat_executor = _Exec1()
        wi.stop()
        wi._chat_executor = _Exec2()
        wi.stop()

    def test_handler_helpers_and_options(self):
        wi = self.make_wi()
        h = self._mk_handler_obj(wi)
        h.headers = {'Origin': 'http://localhost:3000'}

        self.assertEqual(h._allowed_origin(), 'http://localhost:3000')
        h.headers = {'Origin': 'http://evil.com'}
        self.assertEqual(h._allowed_origin(), '')

        h.headers = {'Origin': ''}
        self.assertTrue(h._is_same_origin_or_local())
        h.headers = {'Origin': 'http://evil.com'}
        self.assertFalse(h._is_same_origin_or_local())

        h.headers = {'Origin': 'http://localhost:8080'}
        h.do_OPTIONS()
        h.headers = {'Origin': 'http://evil.com'}
        h.do_OPTIONS()

        h.headers = {'X-Agent-Token': wi._token}
        self.assertTrue(h._is_auth())
        h.log_message('x')

    def test_handler_send_json_html_real(self):
        wi = self.make_wi()
        h = self._mk_handler_obj(wi, raw_body_methods=True)
        h.headers = {'Origin': 'http://localhost:3000'}
        h._send_json({'ok': True}, 201)
        self.assertTrue(h.wfile.getvalue())

        h2 = self._mk_handler_obj(wi, raw_body_methods=True)
        h2._send_html('<b>x</b>')
        self.assertTrue(h2.wfile.getvalue())

    def test_handler_read_json_and_login_and_get_post(self):
        wi = self.make_wi()
        h = self._mk_handler_obj(wi)

        # _read_json branches
        h.headers = {'Content-Length': 'x'}
        self.assertEqual(h._read_json(), {})
        h.headers = {'Content-Length': '0'}
        self.assertEqual(h._read_json(), {})
        h.headers = {'Content-Length': str(wi.max_body_bytes + 1)}
        self.assertEqual(h._read_json(), {})
        h.headers = {'Content-Length': '3'}
        h.rfile = io.BytesIO(b'bad')
        self.assertEqual(h._read_json(), {})
        h.headers = {'Content-Length': '8'}
        h.rfile = io.BytesIO(b'{"a": 1}')
        self.assertEqual(h._read_json(), {'a': 1})

        # _redirect_login
        h._redirect_login()

        # login post branches
        h.headers = {'Content-Length': 'NaN'}
        h.rfile = io.BytesIO(b'')
        h._handle_login_post()

        h.headers = {'Content-Length': str(wi.max_body_bytes + 1)}
        h._handle_login_post()

        h.headers = {'Content-Length': '1000', 'X-Forwarded-Proto': 'https'}
        h.rfile = io.BytesIO(f'token={wi._token}'.encode('utf-8'))
        h._handle_login_post()

        h.headers = {'Content-Length': '11'}
        h.rfile = io.BytesIO(b'token=wrong')
        h._handle_login_post()

        # do_GET branches
        h.headers = {'Accept': 'text/html'}
        h.path = '/login'
        h.do_GET()

        h.path = '/'
        h.headers = {'Accept': 'text/html'}
        h._is_auth = lambda: False
        h.do_GET()

        h.path = '/status'
        h.headers = {'Accept': 'application/json'}
        h._is_auth = lambda: False
        h.do_GET()

        h._is_auth = lambda: True
        h.path = '/'
        h.do_GET()
        h.path = '/status'
        h.do_GET()
        h.path = '/history'
        h.do_GET()
        h.path = '/404'
        h.do_GET()

        # do_POST branches
        h.path = '/login'
        h.do_POST()
        h.path = '/chat'
        h._is_auth = lambda: False
        h.do_POST()
        h._is_auth = lambda: True
        h._is_same_origin_or_local = lambda: False
        h.do_POST()
        h._is_same_origin_or_local = lambda: True
        h._read_json = lambda: {'message': 'x'}
        h.path = '/chat'
        h.do_POST()
        h.path = '/goal'
        h.do_POST()
        h.path = '/other'
        h.do_POST()

    def test_quick_task_all_groups(self):
        wi = self.make_wi()
        tl = wi.loop.tool_layer

        self.assertIsNone(WebInterface(autonomous_loop=None)._get_tool_layer())
        wi_no_tool = self.make_wi()
        wi_no_tool.loop = types.SimpleNamespace(tool_layer=None)
        self.assertIsNone(wi_no_tool._handle_quick_task('x'))

        wi_exc = self.make_wi()
        wi_exc._qt_documents = MagicMock(side_effect=RuntimeError('boom'))
        self.assertFalse(wi_exc._handle_quick_task('x').get('ok'))

        # documents group
        self.assertTrue(wi._qt_documents('pdf отчёт систем', 'создай pdf отчёт о системе', tl))
        self.assertTrue(wi._qt_documents('найди статьи python 2', 'найди 2 статьи python', tl))
        self.assertTrue(wi._qt_documents('screenshot', 'screenshot', tl))
        self.assertTrue(wi._qt_documents('excel создай', 'создай excel', tl))
        self.assertTrue(wi._qt_documents('переведи english', 'переведи: привет', tl))
        self.assertTrue(wi._qt_documents('powerpoint презентация', 'сделай powerpoint', tl))
        self.assertTrue(wi._qt_documents('pdf создай', 'создай pdf с содержимым: abc', tl))

        # failure branches in documents
        bad_tl = _ToolLayer()
        bad_tl.use = lambda *a, **k: {'success': False}
        self.assertFalse(wi._qt_documents('pdf отчёт систем', 'x', bad_tl).get('ok'))
        self.assertFalse(wi._qt_documents('найди статьи', 'найди статьи', bad_tl).get('ok'))
        none_results_tl = _ToolLayer()
        none_results_tl.use = lambda *a, **k: {'success': True, 'results': []}
        self.assertFalse(wi._qt_documents('найди статьи', 'найди статьи', none_results_tl).get('ok'))
        self.assertFalse(wi._qt_documents('переведи english', 'x', bad_tl).get('ok'))
        self.assertFalse(wi._qt_documents('screenshot', 'x', bad_tl).get('ok'))
        self.assertFalse(wi._qt_documents('powerpoint', 'x', bad_tl).get('ok'))
        self.assertFalse(wi._qt_documents('pdf создай', 'x', bad_tl).get('ok'))

        class _PdfFailTL:
            def use(self, tool, **kwargs):
                _ = kwargs
                if tool == 'process_manager':
                    return {'success': True, 'cpu_percent': 1, 'memory_percent': 2, 'disk_percent': 3, 'disk_free_gb': 4}
                if tool == 'pdf_generator':
                    return {'success': False, 'error': 'pdf fail'}
                return {'success': True}

        class _ExcelFailTL:
            def use(self, tool, **kwargs):
                _ = kwargs
                if tool == 'spreadsheet':
                    return {'success': False, 'error': 'excel fail'}
                return {'success': True}

        self.assertFalse(wi._qt_documents('pdf отчёт систем', 'x', _PdfFailTL()).get('ok'))
        self.assertFalse(wi._qt_documents('excel создай', 'x', _ExcelFailTL()).get('ok'))

        str_tl = _ToolLayer()
        str_tl.use = lambda *a, **k: 'okstr'
        self.assertTrue(wi._qt_documents('переведи', 'переведи', str_tl).get('ok'))

        # system info
        self.assertTrue(wi._qt_system_info('проверь cpu', '', tl).get('ok'))
        self.assertTrue(wi._qt_system_info('список процессов', '', tl).get('ok'))
        self.assertFalse(wi._qt_system_info('проверь cpu', '', bad_tl).get('ok'))
        self.assertFalse(wi._qt_system_info('список процессов', '', bad_tl).get('ok'))

        # git
        self.assertIsNone(wi._qt_git('hello', '', tl))
        self.assertTrue(wi._qt_git('git status', '', tl).get('ok'))
        self.assertTrue(wi._qt_git('git log', '', tl).get('ok'))
        self.assertTrue(wi._qt_git('git branch', '', tl).get('ok'))
        self.assertIsNone(wi._qt_git('git foo', '', tl))
        self.assertFalse(wi._qt_git('git status', '', bad_tl).get('ok'))
        self.assertFalse(wi._qt_git('git log', '', bad_tl).get('ok'))
        self.assertFalse(wi._qt_git('git branch', '', bad_tl).get('ok'))

        # network
        self.assertTrue(wi._qt_network('ping google.com', 'ping google.com', tl).get('ok'))
        self.assertTrue(wi._qt_network('dns google.com', 'dns google.com', tl).get('ok'))
        self.assertTrue(wi._qt_network('port 443 localhost', 'port 443 localhost', tl).get('ok'))
        self.assertTrue(wi._qt_network('http get', 'do http https://example.com', tl).get('ok'))
        self.assertFalse(wi._qt_network('ping google.com', 'ping google.com', bad_tl).get('ok'))
        self.assertFalse(wi._qt_network('dns google.com', 'dns google.com', bad_tl).get('ok'))
        self.assertFalse(wi._qt_network('port 443 localhost', 'port 443 localhost', bad_tl).get('ok'))
        self.assertFalse(wi._qt_network('http get', 'do http https://example.com', bad_tl).get('ok'))

        # filesystem
        self.assertTrue(wi._qt_filesystem('создай файл', 'создай файл a.txt с содержимым: hi', tl).get('ok'))
        self.assertIsNone(wi._qt_filesystem('read file', 'прочитай файл', tl))
        self.assertTrue(wi._qt_filesystem('read file', 'прочитай файл a.txt', tl).get('ok'))
        self.assertTrue(wi._qt_filesystem('список файлов', 'список файлов', tl).get('ok'))
        self.assertFalse(wi._qt_filesystem('создай файл', 'создай файл a.txt', bad_tl).get('ok'))
        self.assertFalse(wi._qt_filesystem('read file', 'прочитай файл a.txt', bad_tl).get('ok'))
        self.assertFalse(wi._qt_filesystem('список файлов', 'список файлов', bad_tl).get('ok'))

        # execute
        self.assertTrue(wi._qt_execute_code('выполни python код', '```python\nprint(1)\n```', tl).get('ok'))
        self.assertTrue(wi._qt_execute_code('выполни python код', 'выполни python код', tl).get('ok'))
        self.assertTrue(wi._qt_execute_code('run terminal command', 'shell: dir', tl).get('ok'))
        self.assertTrue(wi._qt_execute_code('run terminal command', '"dir"', tl).get('ok'))
        self.assertTrue(wi._qt_execute_code('install пакет', 'install requests', tl).get('ok'))
        self.assertIsNone(wi._qt_execute_code('run terminal command', 'terminal', tl))
        self.assertIsNone(wi._qt_execute_code('pip пакет', '', tl))

        bad2 = _ToolLayer()
        bad2.use = lambda *a, **k: {'success': False, 'stderr': 'err'}
        self.assertFalse(wi._qt_execute_code('выполни python код', 'code: x', bad2).get('ok'))
        self.assertFalse(wi._qt_execute_code('run terminal command', 'shell: dir', bad2).get('ok'))
        self.assertFalse(wi._qt_execute_code('install пакет', 'install req', bad2).get('ok'))

    def test_misc_and_currency(self):
        wi = self.make_wi()
        tl = wi.loop.tool_layer

        with patch.object(WebInterface, '_city_datetime', side_effect=[datetime.now(timezone.utc), datetime.now(timezone.utc)]):
            out = wi._qt_misc('который час в москва и london', 'который час в москва и london', tl)
            self.assertTrue(out and out.get('ok'))

        class _BadOffset:
            def strftime(self, _fmt):
                return 'x'

            def utcoffset(self):
                raise TypeError('bad')

        with patch.object(WebInterface, '_city_datetime', side_effect=[_BadOffset(), _BadOffset()]):
            out_bad = wi._qt_misc('который час в москва и london', 'который час в москва и london', tl)
            self.assertTrue(out_bad and out_bad.get('ok'))

        with patch.object(WebInterface, '_city_datetime', return_value=None):
            out2 = wi._qt_misc('который час', 'который час', tl)
            self.assertTrue(out2 and out2.get('ok'))

        self.assertTrue(wi._qt_misc('буфер write', 'copy: "abc"', tl).get('ok'))
        self.assertTrue(wi._qt_misc('clipboard', 'clipboard', tl).get('ok'))
        self.assertIsNone(wi._qt_misc('поиск', '', tl))
        self.assertTrue(wi._qt_misc('поиск', 'найди погоду в тель-авиве и скажи', tl).get('ok'))
        self.assertFalse(wi._qt_misc('поиск', 'найди x', bad_tl := types.SimpleNamespace(use=lambda *a, **k: {'success': False})).get('ok'))
        self.assertIsNone(wi._qt_misc('none', 'none', tl))

        self.assertFalse(wi._qt_misc('буфер write', 'copy: "abc"', bad_tl).get('ok'))
        self.assertFalse(wi._qt_misc('clipboard', 'clipboard', bad_tl).get('ok'))

        with patch('urllib.request.urlopen', return_value=_RespCtx()):
            c = wi._handle_currency('usd to ils 10 usd')
            self.assertTrue(c and c.get('ok'))

        with patch('urllib.request.urlopen', return_value=_RespCtx()):
            c2 = wi._handle_currency('курс доллар шекель abc')
            self.assertTrue(c2 and c2.get('ok'))

        with patch('builtins.float', side_effect=ValueError('bad float')):
            with patch('urllib.request.urlopen', return_value=_RespCtx()):
                c3 = wi._handle_currency('usd to ils 10 usd')
                self.assertTrue(c3 and c3.get('ok'))

        class _NoRate(_RespCtx):
            def read(self) -> bytes:
                return b'{"rates":{},"date":"2026-01-01"}'

        with patch('urllib.request.urlopen', return_value=_NoRate()):
            self.assertIsNone(wi._handle_currency('курс доллар шекель'))

        with patch('urllib.request.urlopen', side_effect=RuntimeError('x')):
            self.assertIsNone(wi._handle_currency('курс доллар шекель'))

        self.assertIsNone(wi._handle_currency('привет'))

        # fallback import path for zoneinfo -> pytz
        fake_pytz = types.SimpleNamespace(timezone=lambda _name: timezone.utc)
        orig_import = __import__

        def _imp(name, *args, **kwargs):
            if name == 'zoneinfo':
                raise ImportError('no zoneinfo')
            if name == 'pytz':
                return fake_pytz
            return orig_import(name, *args, **kwargs)

        with patch('builtins.__import__', side_effect=_imp):
            self.assertIsNotNone(WebInterface._city_datetime('UTC'))

    def test_handle_chat_status_goal_and_city_datetime(self):
        wi = self.make_wi()

        # slash commands
        self.assertTrue(wi._handle_chat({'message': '/status'}).get('ok'))
        self.assertTrue(wi._handle_chat({'message': '/help'}).get('ok'))
        self.assertTrue(wi._handle_chat({'message': '/goal do'}).get('ok'))
        self.assertTrue(wi._handle_chat({'message': '/unknown'}).get('ok'))

        # empty
        self.assertFalse(wi._handle_chat({'message': ''}).get('ok'))

        # google oauth missing
        with patch('os.path.exists', return_value=False):
            r = wi._handle_chat({'message': 'отправь письмо через gmail'})
            self.assertFalse(r.get('ok'))

        # file-required without path
        r2 = wi._handle_chat({'message': 'прочитай pdf'})
        self.assertFalse(r2.get('ok'))

        # quick path
        wi._handle_quick_task = MagicMock(return_value={'ok': True, 'reply': 'quick'})
        self.assertTrue(wi._handle_chat({'message': 'создай pdf отчёт'}).get('ok'))

        # currency path
        wi_currency = self.make_wi()
        wi_currency._handle_currency = MagicMock(return_value={'ok': True, 'reply': 'fx'})
        wi_currency._handle_quick_task = MagicMock(return_value=None)
        self.assertTrue(wi_currency._handle_chat({'message': 'курс'}).get('ok'))

        # core path: with executor
        wi._handle_quick_task = MagicMock(return_value=None)
        wi._handle_currency = MagicMock(return_value=None)
        self.assertTrue(wi._handle_chat({'message': 'обычный вопрос'}).get('ok'))

        # core path: without executor
        wi2 = self.make_wi()
        wi2._chat_executor = None
        wi2._handle_quick_task = MagicMock(return_value=None)
        wi2._handle_currency = MagicMock(return_value=None)
        wi2._history = [{'role': ('user' if i % 2 == 0 else 'agent'), 'text': str(i)} for i in range(60)]
        self.assertTrue(wi2._handle_chat({'message': 'обычный вопрос'}).get('ok'))

        # no cognitive core
        wi3 = WebInterface(cognitive_core=None)
        wi3._handle_quick_task = MagicMock(return_value=None)
        wi3._handle_currency = MagicMock(return_value=None)
        self.assertTrue(wi3._handle_chat({'message': 'x'}).get('ok'))

        # timeout
        wi4 = self.make_wi()
        future = MagicMock()
        future.result.side_effect = FuturesTimeoutError()
        wi4._chat_executor = MagicMock(submit=MagicMock(return_value=future))
        wi4._handle_quick_task = MagicMock(return_value=None)
        wi4._handle_currency = MagicMock(return_value=None)
        self.assertTrue(wi4._handle_chat({'message': 'x'}).get('ok'))

        # exception
        wi5 = self.make_wi()
        future2 = MagicMock()
        future2.result.side_effect = RuntimeError('x')
        wi5._chat_executor = MagicMock(submit=MagicMock(return_value=future2))
        wi5._handle_quick_task = MagicMock(return_value=None)
        wi5._handle_currency = MagicMock(return_value=None)
        self.assertTrue(wi5._handle_chat({'message': 'x'}).get('ok'))

        # file upload branch + save failure covered
        wi6 = self.make_wi()
        wi6._handle_currency = MagicMock(return_value=None)
        wi6._handle_quick_task = MagicMock(return_value=None)
        with patch('builtins.open', side_effect=OSError('x')):
            self.assertTrue(wi6._handle_chat({'message': 'x', 'filename': 'a.txt', 'file_content': 'body'}).get('ok'))

        wi6_ok = self.make_wi()
        wi6_ok._handle_currency = MagicMock(return_value=None)
        wi6_ok._handle_quick_task = MagicMock(return_value=None)
        with patch('builtins.open', new_callable=MagicMock) as m_open:
            handle = MagicMock()
            m_open.return_value.__enter__.return_value = handle
            self.assertTrue(wi6_ok._handle_chat({'message': 'x', 'filename': 'a.txt', 'file_content': 'body'}).get('ok'))

        # persistent brain record exception
        wi7 = self.make_wi()
        wi7._handle_currency = MagicMock(return_value=None)
        wi7._handle_quick_task = MagicMock(return_value=None)
        wi7.persistent_brain = types.SimpleNamespace(record_conversation=lambda **k: (_ for _ in ()).throw(RuntimeError('x')))
        self.assertTrue(wi7._handle_chat({'message': 'x'}).get('ok'))

        # status branches
        s = wi._handle_status()
        self.assertTrue(s.get('ok'))

        wi.monitoring = types.SimpleNamespace(get_stats=lambda: {'a': 1})
        self.assertTrue(wi._handle_status().get('ok'))

        wi.monitoring = types.SimpleNamespace(summary=lambda: (_ for _ in ()).throw(RuntimeError('x')))
        self.assertIn('error', wi._handle_status())

        # goal branches
        self.assertFalse(wi._handle_goal({'goal': ''}).get('ok'))
        self.assertTrue(wi._handle_goal({'goal': 'abc'}).get('ok'))
        wi.loop = None
        self.assertFalse(wi._handle_goal({'goal': 'abc'}).get('ok'))
        wi.loop = types.SimpleNamespace(set_goal=lambda g: (_ for _ in ()).throw(RuntimeError('x')))
        self.assertFalse(wi._handle_goal({'goal': 'abc'}).get('ok'))

        # city datetime branches
        self.assertIsNotNone(WebInterface._city_datetime('UTC'))
        with patch.dict('sys.modules', {'zoneinfo': None}):
            with patch('builtins.__import__', side_effect=ImportError('x')):
                self.assertIsNone(WebInterface._city_datetime('UTC'))


if __name__ == '__main__':
    unittest.main()
