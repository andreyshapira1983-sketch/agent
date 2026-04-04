# pyright: reportPrivateUsage=false, reportAttributeAccessIssue=false, reportAssignmentType=false, reportArgumentType=false
# pylint: disable=protected-access,unused-argument
# ruff: noqa: SLF001, ARG001

import os
import sys
import tempfile
import threading
import types
import unittest
from collections import deque
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests

from communication.telegram_bot import TelegramBot


class _Resp:
    def __init__(self, payload=None, status_code=200, text='ok', content=b'data', raise_exc=None):
        self._payload = payload or {'ok': True}
        self.status_code = status_code
        self.text = text
        self.content = content
        self._raise_exc = raise_exc

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self._raise_exc:
            raise self._raise_exc


class _Session:
    def __init__(self):
        self.posts = []
        self.gets = []
        self.post_queue = []
        self.get_queue = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        if self.post_queue:
            item = self.post_queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return _Resp({'ok': True, 'result': []})

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        if self.get_queue:
            item = self.get_queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return _Resp({'ok': True}, content=b'x')


class _Mon:
    def __init__(self):
        self.events = []

    def info(self, msg, source=''):
        self.events.append(('info', str(msg), source))

    def warning(self, msg, source=''):
        self.events.append(('warning', str(msg), source))

    def error(self, msg, source=''):
        self.events.append(('error', str(msg), source))


class _Core:
    def __init__(self):
        self.llm = SimpleNamespace(infer=lambda text, system='': f'infer:{text}|{system}')

    def converse(self, text, system='', history=None):
        _ = history
        return f'conv:{text}|{system[:20]}'

    def process_task(self, text):
        return f'processed:{text}'


class TelegramBotCoverageTests(unittest.TestCase):
    def make_bot(self, **kwargs):
        bot = TelegramBot(token='tok', allowed_chat_ids=[1], **kwargs)
        bot._session = _Session()
        return bot

    def test_init_with_tool_layer_builds_executor(self):
        fake_mod = types.ModuleType('execution.task_executor')

        class _TE:
            def __init__(self, tool_layer, working_dir):
                self.tool_layer = tool_layer
                self.working_dir = working_dir

        fake_mod.TaskExecutor = _TE
        tool_layer = SimpleNamespace(working_dir='wd')

        with patch.dict(sys.modules, {'execution.task_executor': fake_mod}):
            bot = TelegramBot(token='tok', tool_layer=tool_layer)
        self.assertIsNotNone(bot._task_executor)

    def test_init_with_tool_layer_exception_path(self):
        fake_mod = types.ModuleType('execution.task_executor')

        class _TE:
            def __init__(self, tool_layer, working_dir):
                _ = (tool_layer, working_dir)

        fake_mod.TaskExecutor = _TE
        bad_tool_layer = SimpleNamespace(get=lambda key: (_ for _ in ()).throw(RuntimeError('bad get')))
        with patch.dict(sys.modules, {'execution.task_executor': fake_mod}):
            bot = TelegramBot(token='tok', tool_layer=bad_tool_layer)
        self.assertIsNone(bot._task_executor)

    def test_start_stop_send_broadcast(self):
        bot = self.make_bot()
        with patch.object(bot, '_api', return_value=True) as api:
            bot.start()
            self.assertTrue(bot._running)
            # start() при уже запущенном боте — ранний return
            bot._running = True
            bot.start()
            bot.stop()
            self.assertFalse(bot._running)
            self.assertTrue(bot.send(1, 'x'))
            bot.broadcast('hello')
        self.assertTrue(api.called)

    def test_request_approval_no_allowed_ids(self):
        bot = TelegramBot(token='tok', allowed_chat_ids=[])
        self.assertFalse(bot.request_approval('x', {'a': 1}))

    def test_request_approval_timeout_and_answered(self):
        bot = self.make_bot()
        with patch.object(bot, '_api', return_value=True):
            self.assertFalse(bot.request_approval('run', {'x': 1}, timeout=0))

        # answered=True ветка
        bot2 = self.make_bot()

        def _wait(self_event, timeout=0):
            _ = timeout
            with bot2._approvals_lock:
                for entry in bot2._pending_approvals.values():
                    entry['result'] = True
            return True

        with patch.object(bot2, '_api', return_value=True):
            with patch.object(threading.Event, 'wait', _wait):
                self.assertTrue(bot2.request_approval('run', {'x': 1}, timeout=1))

    def test_resolve_approval_sets_event(self):
        bot = self.make_bot()
        ev = threading.Event()
        with bot._approvals_lock:
            bot._pending_approvals['a1'] = {'event': ev, 'result': False}
        bot._resolve_approval('a1', True)
        self.assertTrue(ev.is_set())

    def test_get_updates_branches(self):
        bot = self.make_bot()
        bot._session.post_queue = [_Resp({'ok': True, 'result': [{'update_id': 5}]})]
        ups = bot._get_updates()
        self.assertEqual(len(ups), 1)
        self.assertEqual(bot._offset, 6)

        # ok=false → теперь возвращает [] и логирует, а не бросает
        bot._session.post_queue = [_Resp({'ok': False, 'description': 'bad'})]
        self.assertEqual(bot._get_updates(), [])

        bot._session.post_queue = [ValueError('boom')]
        self.assertEqual(bot._get_updates(), [])

    def test_poll_loop_error_and_success(self):
        bot = self.make_bot()
        bot._running = True
        state = {'n': 0}

        def _get():
            state['n'] += 1
            if state['n'] == 1:
                raise RuntimeError('x')
            bot._running = False
            return []

        bot._get_updates = _get
        with patch('communication.telegram_bot.time.sleep', return_value=None):
            bot._poll_loop()
        self.assertEqual(bot._poll_backoff, 1.0)

    def test_poll_loop_dispatches_updates(self):
        bot = self.make_bot()
        bot._running = True
        bot._handle_update = MagicMock(side_effect=lambda upd: setattr(bot, '_running', False))
        bot._get_updates = lambda: [{'update_id': 1}]
        bot._poll_loop()
        bot._handle_update.assert_called_once()

    def test_handle_update_routing(self):
        bot = self.make_bot()
        bot._handle_callback_query = MagicMock()
        bot._handle_voice = MagicMock()
        bot._handle_photo = MagicMock()
        bot._handle_video = MagicMock()
        bot._handle_document = MagicMock()
        bot._handle_sticker = MagicMock()
        bot._handle_location = MagicMock()
        bot._handle_contact = MagicMock()
        bot._handle_command = MagicMock()
        bot._handle_chat = MagicMock()

        bot._handle_update({'callback_query': {'id': '1'}})
        self.assertTrue(bot._handle_callback_query.called)

        base_msg = {'chat': {'id': 1}, 'from': {'id': 7, 'username': 'u'}}
        bot._handle_update({'message': dict(base_msg, voice={'file_id': 'v'})})
        bot._handle_update({'message': dict(base_msg, photo=[{'file_id': 'p'}])})
        bot._handle_update({'message': dict(base_msg, video={'file_id': 'v'})})
        bot._handle_update({'message': dict(base_msg, document={'file_id': 'd'})})
        bot._handle_update({'message': dict(base_msg, sticker={'emoji': '🙂'})})
        bot._handle_update({'message': dict(base_msg, location={'latitude': 1, 'longitude': 2})})
        bot._handle_update({'message': dict(base_msg, contact={'first_name': 'A'})})
        bot._handle_update({'message': dict(base_msg, text='/help')})
        bot._handle_update({'message': dict(base_msg, text='hello')})
        self.assertTrue(bot._handle_voice.called)
        self.assertTrue(bot._handle_chat.called)

    def test_handle_update_early_returns(self):
        bot = self.make_bot()
        # Нет ни callback_query, ни message
        bot._handle_update({'update_id': 1})

        # Нет текста и ни один медиамаршрут не сработал
        bot._handle_chat = MagicMock()
        bot._handle_update({'message': {'chat': {'id': 1}, 'from': {'id': 1}}})
        bot._handle_chat.assert_not_called()

    def test_handle_update_access_denied_and_approval_text(self):
        bot = TelegramBot(token='tok', allowed_chat_ids=[10])
        bot.send = MagicMock(return_value=True)
        bot._handle_update({'message': {'chat': {'id': 1}, 'from': {'id': 1}, 'text': 'x'}})
        bot.send.assert_called()

        bot2 = self.make_bot()
        bot2._try_resolve_approval_from_text = MagicMock(return_value=True)
        bot2._handle_update({'message': {'chat': {'id': 1}, 'from': {'id': 1}, 'text': 'да'}})
        bot2._try_resolve_approval_from_text.assert_called_once()

    def test_handle_command_all_branches(self):
        bot = self.make_bot()
        bot.send = MagicMock(return_value=True)
        bot._welcome_text = MagicMock(return_value='w')
        bot._help_text = MagicMock(return_value='h')
        bot._status_text = MagicMock(return_value='s')
        bot._budget_text = MagicMock(return_value='b')
        bot._set_goal = MagicMock(return_value='g')
        bot._run_goal = MagicMock(return_value='r')
        bot._stop_loop = MagicMock(return_value='st')
        bot._search = MagicMock(return_value='sr')
        bot._verify = MagicMock(return_value='v')

        cmds = [
            '/start', '/help', '/status', '/budget',
            '/goal', '/goal x', '/run', '/run x', '/stop',
            '/search', '/search q', '/verify', '/verify c', '/unknown'
        ]
        for c in cmds:
            bot._handle_command(1, c, 'a', 'u')
        self.assertTrue(bot.send.called)

    def test_handle_command_start_with_proactive(self):
        pm = SimpleNamespace(on_user_message=MagicMock(), mark_startup_done=MagicMock())
        bot = self.make_bot(proactive_mind=pm)
        bot.send = MagicMock(return_value=True)
        bot._welcome_text = MagicMock(return_value='w')
        bot._handle_command(1, '/start', 'a', 'u')
        pm.on_user_message.assert_called_once()
        pm.mark_startup_done.assert_called_once()

    def test_handle_callback_query_and_text_resolution(self):
        bot = self.make_bot()
        bot._api = MagicMock(return_value=True)
        bot.send = MagicMock(return_value=True)
        bot._resolve_approval = MagicMock()
        bot._handle_callback_query({'id': 'cid', 'data': 'approval:abc:yes', 'message': {'chat': {'id': 1}}})
        bot._resolve_approval.assert_called_with('abc', True)

        # invalid callback data branches
        bot._handle_callback_query({'id': 'cid', 'data': 'x'})
        bot._handle_callback_query({'id': 'cid', 'data': 'approval:x'})

        ev = threading.Event()
        with bot._approvals_lock:
            bot._pending_approvals = {
                'id1': {'event': ev, 'result': False, 'chat_ids': [1], 'created_at': 1.0},
                'id2': {'event': ev, 'result': False, 'chat_ids': [1], 'created_at': 2.0},
            }
        self.assertTrue(bot._try_resolve_approval_from_text(1, 'да id1'))
        self.assertTrue(bot._try_resolve_approval_from_text(1, 'нет'))
        self.assertFalse(bot._try_resolve_approval_from_text(2, 'да'))
        self.assertFalse(bot._try_resolve_approval_from_text(1, 'maybe'))

    def test_try_resolve_approval_more_false_paths(self):
        bot = self.make_bot()
        ev = threading.Event()
        with bot._approvals_lock:
            bot._pending_approvals = {
                'id1': {'event': ev, 'result': False, 'chat_ids': [2], 'created_at': 1.0},
            }
        # requested_id указан, но чат не разрешён и fallback тоже не найдёт
        self.assertFalse(bot._try_resolve_approval_from_text(1, 'да id1'))

        # пустой текст -> parts пуст
        self.assertFalse(bot._try_resolve_approval_from_text(1, '   '))

        # нет pending approvals -> ранний return False
        with bot._approvals_lock:
            bot._pending_approvals = {}
        self.assertFalse(bot._try_resolve_approval_from_text(1, 'да'))

    def test_handle_chat_branches(self):
        bot = self.make_bot(cognitive_core=None)
        bot.send = MagicMock(return_value=True)
        bot._handle_chat(1, 'x', 'a')
        bot.send.assert_called()

        bot2 = self.make_bot(cognitive_core=_Core())
        bot2.send = MagicMock(return_value=True)
        bot2._is_actionable_request = MagicMock(return_value=True)
        bot2._execute_actionable_request = MagicMock(return_value='reply')
        bot2._handle_chat(1, 'x', 'a')
        self.assertTrue(bot2.send.called)

        bot3 = self.make_bot(cognitive_core=_Core())
        bot3.send = MagicMock(return_value=True)
        bot3._is_actionable_request = MagicMock(return_value=True)
        bot3._execute_actionable_request = MagicMock(return_value='')
        bot3._handle_chat(1, 'x', 'a')
        bot3._execute_actionable_request.assert_called_once()

        bot4 = self.make_bot(cognitive_core=_Core())
        bot4.send = MagicMock(return_value=True)
        bot4._is_actionable_request = MagicMock(return_value=False)
        bot4._compose_chat_response = MagicMock(return_value='ok')
        bot4._handle_chat(1, 'x', 'a')
        bot4.send.assert_called()

    def test_handle_chat_proactive_and_persistent_branches(self):
        pm = SimpleNamespace(on_user_message=MagicMock())
        pb = SimpleNamespace(record_conversation=MagicMock())
        bot = self.make_bot(cognitive_core=_Core(), proactive_mind=pm)
        bot.persistent_brain = pb
        bot.send = MagicMock(return_value=True)

        bot._is_actionable_request = MagicMock(return_value=True)
        bot._execute_actionable_request = MagicMock(return_value='done')
        bot._handle_chat(1, 'task', 'a')
        pm.on_user_message.assert_called()
        pb.record_conversation.assert_called()

        pb.record_conversation.reset_mock()
        bot._is_actionable_request = MagicMock(return_value=False)
        bot._compose_chat_response = MagicMock(return_value='resp')
        bot._handle_chat(1, 'task', 'a')
        pb.record_conversation.assert_called()

    def test_build_chat_knowledge_context_branches(self):
        bot = self.make_bot(cognitive_core=_Core(), knowledge=None)
        self.assertEqual(bot._build_chat_knowledge_context('q'), '')

        class _K:
            def get_relevant_knowledge(self, text):
                _ = text
                return {
                    'd': {'k': 'v'},
                    'l': [SimpleNamespace(doc=SimpleNamespace(doc_id='1', text='tt'), score=0.7)],
                    'o': 'x',
                }

        bot2 = self.make_bot(cognitive_core=_Core(), knowledge=_K())
        out = bot2._build_chat_knowledge_context('q')
        self.assertIn('doc_id', out)

        class _K2:
            def get_relevant_knowledge(self, text):
                raise RuntimeError('e')

        bot3 = self.make_bot(cognitive_core=_Core(), knowledge=_K2())
        self.assertEqual(bot3._build_chat_knowledge_context('q'), '')

    def test_build_chat_knowledge_context_to_dict_and_breaks(self):
        class _ItemToDict:
            def to_dict(self):
                return {'doc_id': 'd1', 'score': 0.8, 'text_preview': 'tp'}

        class _ItemBad:
            def to_dict(self):
                raise ValueError('bad')

        class _K:
            def get_relevant_knowledge(self, text):
                _ = text
                return {
                    'list': [_ItemToDict(), _ItemBad(), 'tail'],
                    'dict': {'a': 1, 'b': 2},
                }

        bot = self.make_bot(cognitive_core=_Core(), knowledge=_K())
        out = bot._build_chat_knowledge_context('q', max_items=1)
        self.assertIn('doc_id', out)

        # not found -> return ""
        class _KEmpty:
            def get_relevant_knowledge(self, text):
                _ = text
                return {}

        bot_empty = self.make_bot(cognitive_core=_Core(), knowledge=_KEmpty())
        self.assertEqual(bot_empty._build_chat_knowledge_context('q'), '')

        # except/fallback item path + dict break path
        class _Bad:
            def to_dict(self):
                raise ValueError('bad')

        class _KBreak:
            def get_relevant_knowledge(self, text):
                _ = text
                return {'dict': {'a': 1, 'b': 2}, 'list': [_Bad()]}

        bot_break = self.make_bot(cognitive_core=_Core(), knowledge=_KBreak())
        out_break = bot_break._build_chat_knowledge_context('q', max_items=3)
        self.assertIn('[dict] a:', out_break)
        self.assertIn('[list]', out_break)

        class _KDictOnly:
            def get_relevant_knowledge(self, text):
                _ = text
                return {'dict': {'a': 1, 'b': 2, 'c': 3}}

        # max_items=1 -> должна сработать ветка break внутри dict-итерации
        bot_dict_break = self.make_bot(cognitive_core=_Core(), knowledge=_KDictOnly())
        out_dict_break = bot_dict_break._build_chat_knowledge_context('q', max_items=1)
        self.assertIn('[dict] a:', out_dict_break)

    def test_compose_chat_response_branches(self):
        core = _Core()
        bot = self.make_bot(cognitive_core=core)
        bot._chat_history['a'] = [{'role': 'user', 'content': 'x'}] * 25
        out = bot._compose_chat_response('привет', 'a')
        self.assertTrue(out)

        class _S:
            def get_actor(self, actor_id):
                _ = actor_id
                return None

            def register_actor(self, actor_id, name):
                _ = actor_id
                return SimpleNamespace(preferred_style=SimpleNamespace(value='formal'))

            def detect_style(self, actor_id, text):
                _ = (actor_id, text)

            def detect_tone(self, text, actor_id=None):
                _ = (text, actor_id)

            def suggest_response_tone(self, actor_id):
                _ = actor_id
                return 'urgent'

            def adapt_response(self, response, actor_id=None, tone=None, style=None, context=None):
                _ = (actor_id, tone, style, context)
                return 'adapted:' + response

        bot2 = self.make_bot(cognitive_core=core, social_model=_S())
        out2 = bot2._compose_chat_response('сделай x', 'a')
        self.assertIn('adapted', out2)

        class _S2(_S):
            def adapt_response(self, response, actor_id=None, tone=None, style=None, context=None):
                _ = (response, actor_id, tone, style, context)
                if actor_id is not None:
                    raise TypeError('fallback')
                raise ValueError('ignore')

        bot3 = self.make_bot(cognitive_core=core, social_model=_S2())
        core.converse = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('x'))
        out3 = bot3._compose_chat_response('сделай x', 'a')
        self.assertTrue(out3)

    def test_compose_chat_response_without_core_and_with_memory_knowledge(self):
        bot = self.make_bot(cognitive_core=None)
        self.assertEqual(bot._compose_chat_response('x', 'a'), '')

        core = _Core()
        pb = SimpleNamespace(get_memory_context=lambda text: f'mem:{text}')

        class _K:
            def get_relevant_knowledge(self, text):
                return {'k': {'x': text}}

        class _S:
            def get_actor(self, actor_id):
                raise RuntimeError('social fail')

        bot2 = self.make_bot(cognitive_core=core, knowledge=_K(), social_model=_S())
        bot2.persistent_brain = pb
        out = bot2._compose_chat_response('sделай', 'a')
        self.assertTrue(out)

        # adapt_response ValueError ветка
        class _S3:
            def adapt_response(self, response, actor_id=None, tone=None, style=None, context=None):
                _ = (response, actor_id, tone, style, context)
                raise ValueError('x')

        bot3 = self.make_bot(cognitive_core=core, social_model=_S3())
        out3 = bot3._compose_chat_response('сделай x', 'a')
        self.assertTrue(out3)

        class _S4:
            def get_actor(self, actor_id):
                _ = actor_id
                return SimpleNamespace(preferred_style=SimpleNamespace(value='x'))

            def register_actor(self, actor_id, name):
                _ = (actor_id, name)
                return None

            def detect_style(self, actor_id, text):
                _ = (actor_id, text)
                raise RuntimeError('boom')

            def suggest_response_tone(self, actor_id):
                _ = actor_id
                return 'friendly'

        bot4 = self.make_bot(cognitive_core=core, social_model=_S4())
        self.assertTrue(bot4._compose_chat_response('сделай x', 'a'))

    def test_wrapper_helpers(self):
        bot = self.make_bot(cognitive_core=_Core())
        self.assertTrue(bot._looks_internal_telemetry('success rate score'))
        self.assertFalse(bot._is_small_talk(''))

    def test_small_talk_and_actionable(self):
        bot = self.make_bot(cognitive_core=_Core())
        self.assertTrue(bot._is_small_talk('привет'))
        self.assertFalse(bot._is_small_talk('сделай сложную задачу сейчас'))
        self.assertFalse(bot._is_actionable_request(''))
        self.assertFalse(bot._is_actionable_request('привет как дела'))
        self.assertTrue(bot._is_actionable_request('сделай отчёт'))
        self.assertFalse(bot._is_actionable_request('что это такое'))
        self.assertTrue(bot._is_actionable_request('объясни и исправить код'))

    def test_execute_actionable_request_without_loop(self):
        bot = self.make_bot(cognitive_core=_Core())
        bot._task_executor = SimpleNamespace(execute=lambda t: {'success': True, 'task_type': 'x', 'note': 'ok'})
        self.assertEqual(bot._execute_actionable_request('x', chat_id=1), 'ok')

        bot2 = self.make_bot(cognitive_core=_Core())
        bot2._task_executor = SimpleNamespace(execute=lambda t: (_ for _ in ()).throw(RuntimeError('e')))
        self.assertIn('processed:', bot2._execute_actionable_request('x'))

        bot3 = self.make_bot(cognitive_core=None)
        self.assertIsNone(bot3._execute_actionable_request('x'))

        # cognitive_core без process_task -> AttributeError ветка
        bot4 = self.make_bot(cognitive_core=SimpleNamespace())
        self.assertIsNone(bot4._execute_actionable_request('x'))

    def test_execute_actionable_request_with_loop(self):
        bot = self.make_bot(cognitive_core=_Core())
        bot.loop = SimpleNamespace(
            set_goal=lambda g: g,
            step=lambda: SimpleNamespace(to_dict=lambda: {
                'action_result': {'file': '', 'success': True, 'task_type': 't', 'note': 'n'},
                'plan': 'p', 'evaluation': 'e', 'errors': ['x']
            })
        )
        out = bot._execute_actionable_request('x', chat_id=1)
        self.assertTrue(out)

        # file-ветка с reply is not None
        with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as f:
            fp = f.name
        try:
            bot_file = self.make_bot(cognitive_core=_Core())
            bot_file.loop = SimpleNamespace(
                set_goal=lambda g: g,
                step=lambda: SimpleNamespace(to_dict=lambda: {'action_result': {'file': fp, 'success': True, 'task_type': 't'}})
            )
            bot_file._send_document = MagicMock(return_value=True)
            out_file = bot_file._execute_actionable_request('x', chat_id=1)
            self.assertEqual(out_file, '')
        finally:
            os.unlink(fp)

        # no action_result -> plan/evaluation/errors ветка
        bot_plan = self.make_bot(cognitive_core=_Core())
        bot_plan.loop = SimpleNamespace(
            set_goal=lambda g: g,
            step=lambda: SimpleNamespace(to_dict=lambda: {'plan': 'P', 'evaluation': 'E', 'errors': ['e1', 'e2']})
        )
        out_plan = bot_plan._execute_actionable_request('x', chat_id=1)
        self.assertIn('План:', out_plan)

        bot2 = self.make_bot(cognitive_core=_Core())
        bot2.loop = SimpleNamespace(
            set_goal=lambda g: g,
            step=lambda: SimpleNamespace(to_dict=lambda: {'action_result': 'done'})
        )
        self.assertIn('done', bot2._execute_actionable_request('x'))

        bot3 = self.make_bot(cognitive_core=_Core())
        bot3.loop = SimpleNamespace(
            set_goal=lambda g: g,
            step=lambda: 'plain'
        )
        self.assertIn('plain', bot3._execute_actionable_request('x'))

        bot4 = self.make_bot(cognitive_core=_Core())
        bot4.loop = SimpleNamespace(set_goal=lambda g: (_ for _ in ()).throw(RuntimeError('e')))
        self.assertIsNone(bot4._execute_actionable_request('x'))

    def test_format_task_result_and_send_file_helpers(self):
        bot = self.make_bot(cognitive_core=_Core())
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
            fp = f.name
        try:
            bot._send_photo = MagicMock(return_value=True)
            bot._send_document = MagicMock(return_value=True)
            self.assertEqual(bot._format_task_result({'file': fp, 'success': True, 'task_type': 'img'}, 1), '')
            bot._send_photo.assert_called_once()
        finally:
            os.unlink(fp)

        self.assertIn('<b>a</b>:', bot._format_task_result({'success': True, 'task_type': 't', 'data': {'a': 1}}, 1))
        self.assertIn('✅', bot._format_task_result({'success': True, 'task_type': 't', 'note': ''}, 1))
        self.assertIn('Ошибка', bot._format_task_result({'success': False, 'task_type': 't'}, 1))

    def test_format_task_result_partial_caption_typo_branch_raises(self):
        bot = self.make_bot(cognitive_core=_Core())
        with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as f:
            fp = f.name
        try:
            # Ветка partial-caption должна исполняться без падения
            bot._send_document = MagicMock(return_value=True)
            bot._format_task_result({'file': fp, 'success': False, 'task_type': 't'}, 1)
        finally:
            os.unlink(fp)

    def test_send_document_and_photo_and_text_templates(self):
        bot = self.make_bot(cognitive_core=_Core())
        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = f.name
        try:
            bot._session.post_queue = [_Resp({'ok': True}, status_code=200)]
            self.assertTrue(bot._send_document(1, path, 'cap'))
            bot._session.post_queue = [_Resp({'ok': False}, status_code=200, text='bad')]
            self.assertFalse(bot._send_document(1, path, 'cap'))
            bot._session.post_queue = [requests.RequestException('x')]
            self.assertFalse(bot._send_document(1, path, 'cap'))

            bot._session.post_queue = [_Resp({'ok': True}, status_code=200)]
            self.assertTrue(bot._send_photo(1, path, 'cap'))
            bot._session.post_queue = [_Resp({'ok': False}, status_code=200, text='bad')]
            self.assertFalse(bot._send_photo(1, path, 'cap'))
            bot._session.post_queue = [requests.RequestException('x')]
            self.assertFalse(bot._send_photo(1, path, 'cap'))
        finally:
            os.unlink(path)

        self.assertIn('Команды', bot._help_text())
        self.assertIn('Привет', bot._welcome_text())
        bot.personality = 'p'
        self.assertIn('infer:', bot._welcome_text())

    def test_status_budget_goal_run_stop_search_verify(self):
        mon = SimpleNamespace(summary=lambda: {'total_logs': 1, 'errors': 0, 'core_smoke': {'passed': 1, 'failed': 0, 'last_event': {'message': 'ok'}}})
        hw = SimpleNamespace(collect=lambda: SimpleNamespace(cpu_percent=1.0, memory_percent=2.0, memory_used_mb=1, memory_total_mb=2, disk_percent=3.0, disk_free_gb=4.0))
        loop = SimpleNamespace(_running=True, _cycle_count=9, set_goal=lambda g: g, step=lambda: SimpleNamespace(to_dict=lambda: {'cycle_id': 1, 'analysis': 'a', 'plan': 'p', 'evaluation': 'e', 'errors': ['x']}), stop=lambda: None)
        gm = SimpleNamespace(get_all=lambda: [SimpleNamespace(status='ACTIVE', description='d')], add=lambda d: SimpleNamespace(description=d, goal_id='g1'))
        pb = SimpleNamespace(compact_status_text=lambda **k: 'line1\nline2')
        budget = SimpleNamespace(summary=lambda: {'spent': {'money': 1, 'tokens': 2, 'requests': 3}, 'limits': {'money': 9, 'tokens': 10, 'requests': 11}, 'overall_status': 'ok'})
        search = SimpleNamespace(run=lambda q, num_results=5: {'results': [{'title': 't', 'url': 'https://x', 'snippet': 's'}]})

        class _Status:
            value = 'verified'

        verify = SimpleNamespace(verify=lambda c: SimpleNamespace(status=_Status(), confidence=0.7, notes='n'))

        bot = self.make_bot(cognitive_core=_Core(), monitoring=mon, hardware=hw, autonomous_loop=loop, goal_manager=gm, budget=budget, search_tool=search, verifier=verify)
        bot.persistent_brain = pb

        self.assertIn('Состояние агента', bot._status_text())
        self.assertIn('Бюджет', bot._budget_text())
        self.assertIn('Цель добавлена', bot._set_goal('x'))
        self.assertIn('Цикл #', bot._run_goal('x'))
        self.assertIn('остановлен', bot._stop_loop())
        self.assertIn('Результаты', bot._search('x'))
        self.assertIn('VERIFIED', bot._verify('claim'))

        # error branches
        bot2 = self.make_bot(cognitive_core=_Core(), budget=None, autonomous_loop=None, goal_manager=None, search_tool=None, verifier=None)
        self.assertIn('не подключён', bot2._budget_text())
        self.assertIn('не подключён', bot2._run_goal('x'))
        self.assertIn('не подключён', bot2._stop_loop())
        self.assertIn('не подключён', bot2._search('x'))
        self.assertIn('не подключён', bot2._verify('x'))

    def test_status_and_misc_error_branches(self):
        # status exceptions in subsystems
        bot = self.make_bot(cognitive_core=_Core())
        bot.monitoring = SimpleNamespace(summary=lambda: (_ for _ in ()).throw(OSError('x')))
        bot.hardware = SimpleNamespace(collect=lambda: (_ for _ in ()).throw(RuntimeError('x')))

        class _BadLoop:
            def __getattr__(self, name):
                _ = name
                raise ValueError('x')

        bot.loop = _BadLoop()
        bot.goal_manager = SimpleNamespace(get_all=lambda: (_ for _ in ()).throw(RuntimeError('x')))
        bot.persistent_brain = SimpleNamespace(compact_status_text=lambda **k: (_ for _ in ()).throw(OSError('x')))
        self.assertIn('⚠️', bot._status_text())

        # _welcome_text: llm.infer exception -> fallback help-text
        b0 = self.make_bot(cognitive_core=SimpleNamespace(llm=SimpleNamespace(infer=lambda *a, **k: (_ for _ in ()).throw(RuntimeError('x')))))
        b0.personality = 'p'
        self.assertIn('Привет', b0._welcome_text())

        # budget/set_goal/run/stop/search/verify exception ветки
        b2 = self.make_bot(cognitive_core=_Core(), budget=SimpleNamespace(summary=lambda: (_ for _ in ()).throw(RuntimeError('x'))))
        self.assertIn('Ошибка', b2._budget_text())

        b3 = self.make_bot(cognitive_core=_Core(), goal_manager=None)
        self.assertIn('не подключён', b3._set_goal('x'))
        b4 = self.make_bot(cognitive_core=_Core(), goal_manager=SimpleNamespace(add=lambda d: (_ for _ in ()).throw(RuntimeError('x'))))
        self.assertIn('Ошибка', b4._set_goal('x'))

        b5 = self.make_bot(cognitive_core=_Core(), autonomous_loop=SimpleNamespace(set_goal=lambda g: g, step=lambda: 'plain'))
        self.assertIn('✅ Выполнено', b5._run_goal('x'))
        b5e = self.make_bot(cognitive_core=_Core(), autonomous_loop=SimpleNamespace(set_goal=lambda g: g, step=lambda: (_ for _ in ()).throw(RuntimeError('x'))))
        self.assertIn('Ошибка выполнения', b5e._run_goal('x'))
        b6 = self.make_bot(cognitive_core=_Core(), autonomous_loop=SimpleNamespace(stop=lambda: (_ for _ in ()).throw(RuntimeError('x'))))
        self.assertIn('Ошибка', b6._stop_loop())

        b7 = self.make_bot(cognitive_core=_Core(), search_tool=SimpleNamespace(run=lambda q, num_results=5: {'results': []}))
        self.assertIn('Ничего не найдено', b7._search('x'))
        b8 = self.make_bot(cognitive_core=_Core(), search_tool=SimpleNamespace(run=lambda q, num_results=5: (_ for _ in ()).throw(RuntimeError('x'))))
        self.assertIn('Ошибка поиска', b8._search('x'))

        b9 = self.make_bot(cognitive_core=_Core(), verifier=SimpleNamespace(verify=lambda c: (_ for _ in ()).throw(RuntimeError('x'))))
        self.assertIn('Ошибка', b9._verify('x'))

    def test_media_and_format_document_input(self):
        bot = self.make_bot(cognitive_core=_Core())
        bot._handle_chat = MagicMock()

        # photo success + fallback
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
            p = f.name
        try:
            bot._download_file = MagicMock(return_value=p)
            bot.image_recognizer = None
            bot._handle_photo(1, [{'file_id': 'x'}], 'cap', 'a', 'u')
            self.assertTrue(bot._handle_chat.called)
        finally:
            if os.path.exists(p):
                os.unlink(p)

        # unlink exception branch in _handle_photo
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as fpe:
            pe = fpe.name
        bot._download_file = MagicMock(return_value=pe)
        bot.image_recognizer = SimpleNamespace(describe=lambda path: 'desc')
        with patch('os.unlink', side_effect=RuntimeError('x')):
            bot._handle_photo(1, [{'file_id': 'x'}], '', 'a', 'u')

        bot._download_file = MagicMock(return_value=None)
        bot.send = MagicMock(return_value=True)
        bot._handle_photo(1, [{'file_id': 'x'}], '', 'a', 'u')
        bot.send.assert_called()

        # video thumb path + fallback
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
            p2 = f.name
        try:
            bot._download_file = MagicMock(return_value=p2)
            bot.image_recognizer = SimpleNamespace(describe=lambda path: 'vdesc')
            bot._handle_video(1, {'duration': 3, 'thumbnail': {'file_id': 't'}}, 'c', 'a', 'u')
        finally:
            if os.path.exists(p2):
                os.unlink(p2)
        # _handle_video unlink exception branch
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as fv:
            pv = fv.name
        bot._download_file = MagicMock(return_value=pv)
        with patch('os.unlink', side_effect=PermissionError('x')):
            bot._handle_video(1, {'duration': 3, 'thumbnail': {'file_id': 't'}}, 'c', 'a', 'u')

        bot._handle_video(1, {'duration': 1}, '', 'a', 'u')
        bot._handle_video(1, {'duration': 1}, 'caption', 'a', 'u')

        # document success + fallback
        with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as f:
            pd = f.name
        try:
            bot._download_file = MagicMock(return_value=pd)
            bot.document_parser = SimpleNamespace(parse=lambda path: SimpleNamespace(text='hello', pages=1))
            bot._handle_document(1, {'file_id': 'd', 'file_name': 'a.txt', 'mime_type': 'text/plain'}, 'cap', 'a', 'u')
            self.assertTrue(bot._handle_chat.called)
        finally:
            if os.path.exists(pd):
                os.unlink(pd)

        # fallback ветка документа + unlink exception
        with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as fd2:
            pd2 = fd2.name
        bot._download_file = MagicMock(return_value=pd2)
        bot.document_parser = SimpleNamespace(parse=lambda path: SimpleNamespace(text='   ', pages=0))
        with patch('os.unlink', side_effect=FileNotFoundError('x')):
            bot._handle_document(1, {'file_id': 'd', 'file_name': 'a.bin', 'mime_type': 'application/octet-stream'}, 'cap2', 'a', 'u')

        bot._download_file = MagicMock(return_value=None)
        bot.send = MagicMock(return_value=True)
        bot._handle_document(1, {'file_id': 'd', 'file_name': 'a.txt', 'mime_type': 'text/plain'}, '', 'a', 'u')

        txt = bot._format_document_chat_input('f.txt', 'preview', 'оцени', 2)
        self.assertIn('Не проси прислать файл заново', txt)

        bot._handle_sticker(1, {'emoji': '🙂'}, 'a')
        bot._handle_location(1, {'latitude': 1, 'longitude': 2}, 'a')
        bot._handle_contact(1, {'first_name': 'A', 'last_name': 'B', 'phone_number': '+1234'}, 'a')
        bot._handle_contact(1, {'first_name': 'A', 'last_name': 'B', 'phone_number': ''}, 'a')

    def test_voice_and_chat_voice_branches(self):
        bot = self.make_bot(cognitive_core=_Core())
        bot.send = MagicMock(return_value=True)
        bot._handle_command = MagicMock()
        bot._handle_chat_voice = MagicMock()

        # no recognizer
        bot.speech_recognizer = None
        bot._handle_voice(1, {'file_id': 'x'}, 'a', 'u')

        # no file_id
        bot.speech_recognizer = SimpleNamespace(transcribe=lambda p: SimpleNamespace(text='x'))
        bot._handle_voice(1, {}, 'a', 'u')

        # download fail
        bot._download_file = MagicMock(return_value=None)
        bot._handle_voice(1, {'file_id': 'x'}, 'a', 'u')

        # transcribe fail
        with tempfile.NamedTemporaryFile(delete=False) as f:
            p = f.name
        bot._download_file = MagicMock(return_value=p)
        bot.speech_recognizer = SimpleNamespace(transcribe=lambda p: (_ for _ in ()).throw(RuntimeError('e')))
        bot._handle_voice(1, {'file_id': 'x'}, 'a', 'u')

        # empty text
        with tempfile.NamedTemporaryFile(delete=False) as f2:
            p2 = f2.name
        bot._download_file = MagicMock(return_value=p2)
        bot.speech_recognizer = SimpleNamespace(transcribe=lambda p: SimpleNamespace(text='   '))
        bot._handle_voice(1, {'file_id': 'x'}, 'a', 'u')

        # command / chat route
        with tempfile.NamedTemporaryFile(delete=False) as f3:
            p3 = f3.name
        bot._download_file = MagicMock(return_value=p3)
        bot.speech_recognizer = SimpleNamespace(transcribe=lambda p: SimpleNamespace(text='/help'))
        bot._handle_voice(1, {'file_id': 'x'}, 'a', 'u')
        with tempfile.NamedTemporaryFile(delete=False) as f4:
            p4 = f4.name
        bot._download_file = MagicMock(return_value=p4)
        bot.speech_recognizer = SimpleNamespace(transcribe=lambda p: SimpleNamespace(text='hello'))
        bot._handle_voice(1, {'file_id': 'x'}, 'a', 'u')

        # _handle_voice unlink exception branch
        with tempfile.NamedTemporaryFile(delete=False) as f5:
            p5 = f5.name
        bot._download_file = MagicMock(return_value=p5)
        bot.speech_recognizer = SimpleNamespace(transcribe=lambda p: SimpleNamespace(text='hello'))
        with patch('os.unlink', side_effect=PermissionError('x')):
            bot._handle_voice(1, {'file_id': 'x'}, 'a', 'u')

        # _handle_chat_voice branches
        b2 = self.make_bot(cognitive_core=None)
        b2.send = MagicMock(return_value=True)
        b2._handle_chat_voice(1, 'x', 'a')

        b3 = self.make_bot(cognitive_core=_Core())
        b3.send = MagicMock(return_value=True)
        b3.proactive_mind = SimpleNamespace(on_user_message=MagicMock())
        b3.persistent_brain = SimpleNamespace(record_conversation=MagicMock())
        b3._is_actionable_request = MagicMock(return_value=True)
        b3._execute_actionable_request = MagicMock(return_value='done')
        b3.speech_synthesizer = SimpleNamespace(synthesize=lambda t: 'a.opus', cleanup=lambda p: None)
        b3.send_voice = MagicMock(return_value=True)
        b3._handle_chat_voice(1, 'x', 'a')

        # actionable + send_voice=False -> fallback send + return
        b3f = self.make_bot(cognitive_core=_Core())
        b3f.send = MagicMock(return_value=True)
        b3f._is_actionable_request = MagicMock(return_value=True)
        b3f._execute_actionable_request = MagicMock(return_value='done')
        b3f.speech_synthesizer = SimpleNamespace(synthesize=lambda t: 'a.opus', cleanup=lambda p: None)
        b3f.send_voice = MagicMock(return_value=False)
        b3f._handle_chat_voice(1, 'x', 'a')
        b3f.send.assert_called()

        b4 = self.make_bot(cognitive_core=_Core())
        b4.send = MagicMock(return_value=True)
        b4.persistent_brain = SimpleNamespace(record_conversation=MagicMock())
        b4._is_actionable_request = MagicMock(return_value=False)
        b4._compose_chat_response = MagicMock(return_value='resp')
        b4.speech_synthesizer = SimpleNamespace(synthesize=lambda t: 'a.opus', cleanup=lambda p: None)
        b4.send_voice = MagicMock(return_value=False)
        b4._handle_chat_voice(1, 'x', 'a')
        b4.send.assert_called()

        # actionable path, но пустой ответ -> ранний return
        b5 = self.make_bot(cognitive_core=_Core())
        b5.send = MagicMock(return_value=True)
        b5._is_actionable_request = MagicMock(return_value=True)
        b5._execute_actionable_request = MagicMock(return_value='')
        b5._handle_chat_voice(1, 'x', 'a')
        b5.send.assert_not_called()

    def test_send_voice_download_api_escape_log(self):
        bot = self.make_bot(cognitive_core=_Core())
        with tempfile.NamedTemporaryFile(delete=False) as f:
            p = f.name
        try:
            # sendVoice ok
            bot._session.post_queue = [_Resp({'ok': True})]
            self.assertTrue(bot.send_voice(1, p))

            # fallback ok
            bot._session.post_queue = [_Resp({'ok': False}), _Resp({'ok': True})]
            self.assertTrue(bot.send_voice(1, p))

            # fallback fail
            bot._session.post_queue = [_Resp({'ok': False}), _Resp({'ok': False})]
            self.assertFalse(bot.send_voice(1, p))

            # exception
            bot._session.post_queue = [requests.RequestException('x')]
            self.assertFalse(bot.send_voice(1, p))
        finally:
            os.unlink(p)

        # _download_file success
        bot._session.post_queue = [_Resp({'ok': True, 'result': {'file_path': 'a/b.txt'}})]
        bot._session.get_queue = [_Resp(content=b'abc')]
        path = bot._download_file('fid')
        self.assertTrue(path and os.path.isfile(path))
        if path:
            os.unlink(path)

        # _download_file failure branches
        bot._session.post_queue = [_Resp({'ok': False})]
        self.assertIsNone(bot._download_file('fid'))
        bot._session.post_queue = [requests.RequestException('x')]
        self.assertIsNone(bot._download_file('fid'))

        self.assertIn('sendMessage', bot._url('sendMessage'))
        bot._session.post_queue = [_Resp({'ok': True})]
        self.assertTrue(bot._api('x', {'a': 1}))
        bot._session.post_queue = [requests.RequestException('x')]
        self.assertFalse(bot._api('x', {'a': 1}))

        self.assertEqual(bot._escape('<a&>'), '&lt;a&amp;&gt;')

        mon = _Mon()
        bot2 = self.make_bot(cognitive_core=_Core(), monitoring=mon)
        bot2._log('token tok here', level='error')
        self.assertTrue(mon.events)

        bot3 = TelegramBot(token='tok', monitoring=None)
        with patch('builtins.print') as p:
            bot3._log('hello')
        p.assert_called()


class TelegramBotBehaviorTests(unittest.TestCase):
    """Сценарные/поведенческие тесты — проверяют смысл поведения, а не ветки."""

    def make_bot(self, **kwargs):
        bot = TelegramBot(token='tok', allowed_chat_ids=[1], **kwargs)
        bot._session = _Session()
        return bot

    # ── HTML escaping ─────────────────────────────────────────────────────────

    def test_escape_html_special_chars(self):
        bot = self.make_bot()
        self.assertEqual(bot._escape('<script>alert(1)</script>'),
                         '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertEqual(bot._escape('a & b < c > d'),
                         'a &amp; b &lt; c &gt; d')

    # ── _detect_file_intent: default=analyze ──────────────────────────────────

    def test_file_intent_empty_caption_is_analyze(self):
        """Пустая подпись → analyze (безопасный режим по умолчанию)."""
        self.assertEqual(TelegramBot._detect_file_intent(''), 'analyze')
        self.assertEqual(TelegramBot._detect_file_intent(None), 'analyze')

    def test_file_intent_analyze_keywords(self):
        self.assertEqual(TelegramBot._detect_file_intent('оцени'), 'analyze')
        self.assertEqual(TelegramBot._detect_file_intent('что думаешь'), 'analyze')
        self.assertEqual(TelegramBot._detect_file_intent('проверь'), 'analyze')

    def test_file_intent_execute_only_explicit(self):
        self.assertEqual(TelegramBot._detect_file_intent('выполни'), 'execute')
        self.assertEqual(TelegramBot._detect_file_intent('сделай как написано'), 'execute')

    def test_file_intent_ambiguous_is_analyze(self):
        """Неясная подпись → analyze (fail-safe)."""
        self.assertEqual(TelegramBot._detect_file_intent('вот файлик'), 'analyze')
        self.assertEqual(TelegramBot._detect_file_intent('для тебя'), 'analyze')

    # ── send() split ──────────────────────────────────────────────────────────

    def test_send_short_message_single_api_call(self):
        bot = self.make_bot()
        bot._api = MagicMock(return_value=True)
        bot.send(1, 'short text')
        bot._api.assert_called_once()

    def test_send_long_message_splits(self):
        bot = self.make_bot()
        calls = []
        bot._api = lambda method, payload: (calls.append(payload), True)[-1]
        long_text = 'line\n' * 1200  # ~6000 chars > 4096
        bot.send(1, long_text)
        self.assertGreaterEqual(len(calls), 2)
        for c in calls:
            self.assertLessEqual(len(c['text']), 4096)

    # ── _get_updates contract: never raises ───────────────────────────────────

    def test_get_updates_ok_false_returns_empty(self):
        bot = self.make_bot()
        bot._session.post_queue = [_Resp({'ok': False, 'description': 'Unauthorized'})]
        result = bot._get_updates()
        self.assertEqual(result, [])

    def test_get_updates_network_error_returns_empty(self):
        bot = self.make_bot()
        bot._session.post_queue = [requests.RequestException('timeout')]
        result = bot._get_updates()
        self.assertEqual(result, [])

    # ── approval: double-resolve protection ───────────────────────────────────

    def test_resolve_approval_double_resolve_ignored(self):
        bot = self.make_bot()
        ev = threading.Event()
        with bot._approvals_lock:
            bot._pending_approvals['a1'] = {'event': ev, 'result': False}
        bot._resolve_approval('a1', True)
        self.assertTrue(ev.is_set())
        # Второй вызов — ignored, result не меняется
        bot._resolve_approval('a1', False)
        with bot._approvals_lock:
            entry = bot._pending_approvals.get('a1')
        self.assertTrue(entry['result'])  # остался True от первого вызова

    def test_resolve_approval_unknown_id_noop(self):
        bot = self.make_bot()
        # Не должен падать
        bot._resolve_approval('nonexistent', True)

    # ── monitoring fix: uses self.monitoring, not self._monitoring ─────────────

    def test_request_approval_empty_chats_uses_monitoring(self):
        mon = _Mon()
        bot = TelegramBot(token='tok', allowed_chat_ids=[], monitoring=mon)
        result = bot.request_approval('test', 'payload')
        self.assertFalse(result)
        # Теперь monitoring.warning реально вызывается
        self.assertTrue(any('request_approval' in str(e) for e in mon.events))

    # ── log data privacy ──────────────────────────────────────────────────────

    def test_log_masks_token(self):
        mon = _Mon()
        bot = self.make_bot(monitoring=mon)
        bot._log('Error at https://api.telegram.org/bottok/getMe')
        last_msg = mon.events[-1][1]
        self.assertNotIn('tok', last_msg.replace('***TOKEN***', ''))


class TelegramSinkTests(unittest.TestCase):
    """Тесты для telegram_sink.py."""

    def test_escape_html(self):
        from llm.telegram_sink import TelegramSink
        self.assertEqual(TelegramSink._escape_html('<b>test</b>'),
                         '&lt;b&gt;test&lt;/b&gt;')

    def test_format_escapes_source_and_message(self):
        from llm.telegram_sink import TelegramSink
        sink = TelegramSink.__new__(TelegramSink)
        sink.token = 'test_token'
        sink._scrubber = None
        entry = {'level': 'ERROR', 'source': '<xss>', 'message': 'a & b', 'time_str': '12:00'}
        text = sink._format(entry)
        self.assertNotIn('<xss>', text)
        self.assertIn('&lt;xss&gt;', text)
        self.assertIn('a &amp; b', text)

    def test_send_returns_false_when_no_requests(self):
        from llm.telegram_sink import TelegramSink
        sink = TelegramSink.__new__(TelegramSink)
        sink.token = 'test'
        sink._lock = threading.Lock()
        sink._error_count = 0
        # Simulate requests=None
        import llm.telegram_sink as _mod
        orig = _mod.requests
        try:
            _mod.requests = None
            result = sink._send('test')
            self.assertFalse(result)
        finally:
            _mod.requests = orig

    def test_write_tracks_evictions(self):
        from llm.telegram_sink import TelegramSink
        sink = TelegramSink.__new__(TelegramSink)
        sink.token = 'test'
        sink.chat_id = '1'
        sink.min_level = 'ERROR'
        sink._lock = threading.Lock()
        sink._queue = deque(maxlen=2)
        sink._dropped_count = 0
        sink._stop_event = threading.Event()
        sink._last_send = 0.0
        # Fill queue to max
        sink.write({'level': 'ERROR', 'message': 'a'})
        sink.write({'level': 'ERROR', 'message': 'b'})
        self.assertEqual(sink._dropped_count, 0)
        # This one evicts 'a'
        sink.write({'level': 'ERROR', 'message': 'c'})
        self.assertEqual(sink._dropped_count, 1)

    def test_should_send_filters_by_level(self):
        from llm.telegram_sink import TelegramSink
        sink = TelegramSink.__new__(TelegramSink)
        sink.min_level = 'WARNING'
        self.assertTrue(sink._should_send('ERROR'))
        self.assertTrue(sink._should_send('WARNING'))
        self.assertFalse(sink._should_send('INFO'))
        self.assertFalse(sink._should_send('UNKNOWN'))


if __name__ == '__main__':
    unittest.main()
