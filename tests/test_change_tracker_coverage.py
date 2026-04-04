"""Tests for monitoring/change_tracker.py — ChangeTracker."""
import json
import os
import tempfile
from unittest.mock import patch
import pytest

from monitoring.change_tracker import ChangeTracker


class TestChangeTracker:
    @pytest.fixture(autouse=True)
    def _tmp_log(self, tmp_path):
        self.log_path = str(tmp_path / "changes.jsonl")
        self.tracker = ChangeTracker(log_path=self.log_path)

    def test_init(self):
        assert self.tracker._path == self.log_path
        assert self.tracker._buffer == []

    def test_file_changed(self):
        self.tracker.file_changed('test.py', op='create', agent='core', details='new file')
        self.tracker.flush()
        entries = self._read_all()
        assert len(entries) == 1
        assert entries[0]['kind'] == 'file_change'
        assert entries[0]['data']['path'] == 'test.py'
        assert entries[0]['data']['op'] == 'create'
        assert entries[0]['agent'] == 'core'

    def test_command_ran(self):
        self.tracker.command_ran('pip install X', exit_code=0, agent='sandbox', output_preview='ok')
        self.tracker.flush()
        entries = self._read_all()
        assert len(entries) == 1
        assert entries[0]['kind'] == 'command'
        assert entries[0]['data']['command'] == 'pip install X'
        assert entries[0]['data']['exit_code'] == 0

    def test_error_occurred(self):
        self.tracker.error_occurred('TimeoutError', 'LLM timeout', agent='llm')
        self.tracker.flush()
        entries = self._read_all()
        assert len(entries) == 1
        assert entries[0]['kind'] == 'error'
        assert entries[0]['data']['error_type'] == 'TimeoutError'

    def test_action_taken(self):
        self.tracker.action_taken('dispatch', result='ok', agent='loop', data={'step': 1})
        self.tracker.flush()
        entries = self._read_all()
        assert entries[0]['kind'] == 'action'
        assert entries[0]['data']['action'] == 'dispatch'
        assert entries[0]['data']['step'] == 1

    def test_rollback(self):
        self.tracker.rollback('file.py', reason='syntax error', agent='self_repair')
        self.tracker.flush()
        entries = self._read_all()
        assert entries[0]['kind'] == 'rollback'
        assert entries[0]['data']['what'] == 'file.py'

    def test_auto_flush_on_buffer_full(self):
        tracker = ChangeTracker(log_path=self.log_path, max_buffer=3)
        tracker.file_changed('a.py')
        tracker.file_changed('b.py')
        tracker.file_changed('c.py')  # should trigger auto flush
        # After auto-flush, file should have entries
        entries = self._read_all()
        assert len(entries) >= 3

    def test_recent_empty(self):
        entries = self.tracker.recent()
        assert entries == []

    def test_recent_returns_entries(self):
        self.tracker.file_changed('a.py')
        self.tracker.command_ran('ls')
        self.tracker.flush()
        entries = self.tracker.recent(n=10)
        assert len(entries) == 2

    def test_recent_with_kind_filter(self):
        self.tracker.file_changed('a.py')
        self.tracker.command_ran('ls')
        self.tracker.flush()
        entries = self.tracker.recent(kind='command')
        assert len(entries) == 1
        assert entries[0]['kind'] == 'command'

    def test_recent_limit(self):
        for i in range(10):
            self.tracker.file_changed(f'file{i}.py')
        self.tracker.flush()
        entries = self.tracker.recent(n=3)
        assert len(entries) == 3

    def test_recent_file_not_found(self):
        tracker = ChangeTracker(log_path='/nonexistent/path/changes.jsonl')
        tracker._buffer = []
        tracker._path = '/nonexistent/path/changes.jsonl'
        entries = tracker.recent()
        assert entries == []

    def test_output_preview_truncated(self):
        long_output = 'x' * 1000
        self.tracker.command_ran('cmd', output_preview=long_output)
        self.tracker.flush()
        entries = self._read_all()
        assert len(entries[0]['data']['output_preview']) <= 500

    def test_message_truncated(self):
        long_msg = 'y' * 1000
        self.tracker.error_occurred('E', long_msg)
        self.tracker.flush()
        entries = self._read_all()
        assert len(entries[0]['data']['message']) <= 500

    def test_traceback_truncated(self):
        long_tb = 'z' * 3000
        self.tracker.error_occurred('E', 'msg', traceback_str=long_tb)
        self.tracker.flush()
        entries = self._read_all()
        assert len(entries[0]['data']['traceback']) <= 2000

    def _read_all(self):
        entries = []
        if os.path.exists(self.log_path):
            with open(self.log_path, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        entries.append(json.loads(line))
        return entries
