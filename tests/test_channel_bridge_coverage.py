"""Tests for communication/channel_bridge.py — ChannelBridge, ChannelEvent."""
import time
from unittest.mock import MagicMock
import pytest

from communication.channel_bridge import ChannelBridge, ChannelEvent


class TestChannelEvent:
    def test_init(self):
        ev = ChannelEvent('task_received', 'telegram', 'hello')
        assert ev.type == 'task_received'
        assert ev.source == 'telegram'
        assert ev.text == 'hello'
        assert isinstance(ev.ts, float)
        assert ev.extra == {}

    def test_init_with_extra(self):
        ev = ChannelEvent('message', 'web', 'hi', extra={'key': 'val'})
        assert ev.extra == {'key': 'val'}

    def test_to_dict(self):
        ev = ChannelEvent('reply', 'loop', 'done', extra={'foo': 42})
        d = ev.to_dict()
        assert d['type'] == 'reply'
        assert d['source'] == 'loop'
        assert d['text'] == 'done'
        assert d['foo'] == 42
        assert 'ts' in d


class TestChannelBridge:
    def test_register_and_emit(self):
        bridge = ChannelBridge()
        received = []
        bridge.register('web', received.append)
        ev = ChannelEvent('message', 'telegram', 'hi')
        bridge.emit(ev)
        assert len(received) == 1
        assert received[0].text == 'hi'

    def test_emit_skips_source_channel(self):
        bridge = ChannelBridge()
        tg_received = []
        web_received = []
        bridge.register('telegram', tg_received.append)
        bridge.register('web', web_received.append)
        ev = ChannelEvent('message', 'telegram', 'hi')
        bridge.emit(ev)
        assert len(tg_received) == 0  # source channel skipped
        assert len(web_received) == 1

    def test_unregister(self):
        bridge = ChannelBridge()
        received = []
        bridge.register('web', received.append)
        bridge.unregister('web')
        ev = ChannelEvent('message', 'telegram', 'hi')
        bridge.emit(ev)
        assert len(received) == 0

    def test_unregister_unknown(self):
        bridge = ChannelBridge()
        bridge.unregister('nonexistent')  # should not raise

    def test_emit_listener_error_with_monitoring(self):
        mon = MagicMock()
        bridge = ChannelBridge(monitoring=mon)
        bridge.register('web', lambda ev: (_ for _ in ()).throw(RuntimeError("boom")))
        ev = ChannelEvent('message', 'telegram', 'hi')
        bridge.emit(ev)  # should not raise
        mon.warning.assert_called_once()

    def test_emit_listener_error_no_monitoring(self):
        bridge = ChannelBridge()
        bridge.register('web', lambda ev: (_ for _ in ()).throw(RuntimeError("boom")))
        ev = ChannelEvent('message', 'telegram', 'hi')
        bridge.emit(ev)  # should not raise

    def test_recent_empty(self):
        bridge = ChannelBridge()
        assert bridge.recent() == []

    def test_recent_with_events(self):
        bridge = ChannelBridge()
        ev1 = ChannelEvent('message', 'telegram', 'first')
        ev2 = ChannelEvent('reply', 'loop', 'second')
        bridge.emit(ev1)
        bridge.emit(ev2)
        result = bridge.recent()
        assert len(result) == 2

    def test_recent_since_ts(self):
        bridge = ChannelBridge()
        ev1 = ChannelEvent('message', 'telegram', 'old')
        ev1.ts = 100.0
        bridge._history.append(ev1)
        ev2 = ChannelEvent('reply', 'loop', 'new')
        ev2.ts = 200.0
        bridge._history.append(ev2)
        result = bridge.recent(since_ts=150.0)
        assert len(result) == 1
        assert result[0]['text'] == 'new'

    def test_recent_limit(self):
        bridge = ChannelBridge()
        for i in range(10):
            ev = ChannelEvent('message', 'telegram', f'msg{i}')
            bridge._history.append(ev)
        result = bridge.recent(limit=3)
        assert len(result) == 3

    def test_task_received_helper(self):
        bridge = ChannelBridge()
        received = []
        bridge.register('web', received.append)
        bridge.task_received('telegram', 'do something')
        assert len(received) == 1
        assert received[0].type == 'task_received'

    def test_task_progress_helper(self):
        bridge = ChannelBridge()
        received = []
        bridge.register('web', received.append)
        bridge.task_progress('loop', '50% done')
        assert received[0].type == 'task_progress'

    def test_task_done_helper(self):
        bridge = ChannelBridge()
        received = []
        bridge.register('web', received.append)
        bridge.task_done('loop', 'completed')
        assert received[0].type == 'task_done'

    def test_user_message_helper(self):
        bridge = ChannelBridge()
        received = []
        bridge.register('web', received.append)
        bridge.user_message('telegram', 'hello')
        assert received[0].type == 'message'

    def test_agent_reply_helper(self):
        bridge = ChannelBridge()
        received = []
        bridge.register('telegram', received.append)
        bridge.agent_reply('web', 'response')
        assert received[0].type == 'reply'

    def test_history_maxlen(self):
        bridge = ChannelBridge()
        for i in range(250):
            bridge.emit(ChannelEvent('msg', 'x', str(i)))
        assert len(bridge._history) <= 200
