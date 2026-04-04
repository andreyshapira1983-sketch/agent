"""Tests for execution/task_executor.py — TaskExecutor."""
import os
import datetime
from unittest.mock import MagicMock, patch
import pytest

from execution.task_executor import TaskExecutor


class TestTaskExecutor:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.tool_layer = MagicMock()
        self.tool_layer.use.return_value = {'success': True}
        self.executor = TaskExecutor(self.tool_layer, working_dir=str(tmp_path))
        self.tmp_path = tmp_path

    def test_init(self):
        assert self.executor.tool_layer is self.tool_layer
        assert os.path.isdir(self.executor._outputs_dir)

    def test_out_path(self):
        p = self.executor._out('test.txt')
        assert p.endswith('test.txt')
        assert 'outputs' in p

    def test_timestamp(self):
        ts = self.executor._timestamp()
        assert len(ts) == 15  # YYYYMMDD_HHMMSS

    def test_date(self):
        d = self.executor._date()
        assert len(d) == 10  # YYYY-MM-DD

    def test_use_success(self):
        self.executor._use('search', query='test')
        self.tool_layer.use.assert_called_once_with('search', query='test')

    def test_use_error(self):
        self.tool_layer.use.side_effect = RuntimeError("tool error")
        result = self.executor._use('search')
        assert not result['success']
        assert 'tool error' in result['error']

    def test_detect(self):
        t = self.executor._detect('send email to john')
        assert isinstance(t, str)

    def test_extract_text(self):
        assert self.executor._extract_text('создай отчёт по продажам') == 'отчёт по продажам'
        assert self.executor._extract_text('generate report') == 'report'
        assert self.executor._extract_text('plain text') == 'plain text'

    def test_extract_text_truncation(self):
        long_text = 'x' * 1000
        result = self.executor._extract_text(long_text)
        assert len(result) <= 500

    # ── Individual executors ──────────────────────────────────────────────

    def test_exec_pdf(self):
        result = self.executor._exec_pdf('создай PDF отчёт')
        assert 'file' in result
        assert result['file'].endswith('.pdf')

    def test_exec_excel(self):
        result = self.executor._exec_excel('создай excel таблицу')
        assert 'file' in result
        assert result['file'].endswith('.xlsx')

    def test_exec_pptx(self):
        result = self.executor._exec_pptx('создай презентацию')
        assert 'file' in result
        assert result['file'].endswith('.pptx')

    def test_exec_archive_no_files(self):
        result = self.executor._exec_archive('запакуй файлы')
        assert not result['success']

    def test_exec_archive_with_files(self):
        # Create a file in outputs
        test_file = os.path.join(self.executor._outputs_dir, 'test.txt')
        with open(test_file, 'w', encoding='utf-8') as f:
            f.write('test')
        result = self.executor._exec_archive('запакуй файлы')
        assert 'file' in result

    def test_exec_screenshot(self):
        result = self.executor._exec_screenshot('сделай скриншот')
        assert 'file' in result
        assert result['file'].endswith('.png')

    def test_exec_chart(self):
        result = self.executor._exec_chart('построй график продаж')
        assert 'file' in result

    def test_exec_translate_russian(self):
        self.executor._exec_translate('переведи на русский hello world')
        self.tool_layer.use.assert_called_once()

    def test_exec_translate_english(self):
        result = self.executor._exec_translate('translate to english привет')
        assert result

    def test_exec_translate_hebrew(self):
        result = self.executor._exec_translate('переведи на иврит hello')
        assert result

    def test_exec_search(self):
        self.tool_layer.use.return_value = {
            'success': True,
            'results': [
                {'title': 'Result 1', 'url': 'http://example.com', 'snippet': 'test'}
            ]
        }
        result = self.executor._exec_search('найди информацию о Python')
        assert 'file' in result

    def test_exec_search_no_results(self):
        self.tool_layer.use.return_value = {'success': False}
        result = self.executor._exec_search('search query')
        assert result

    def test_exec_health(self):
        with patch('importlib.import_module', side_effect=ImportError):
            result = self.executor._exec_health('проверь здоровье')
        assert result['success']
        assert 'file' in result

    def test_exec_email_unread(self):
        self.executor._exec_email('покажи непрочитанные')
        self.tool_layer.use.assert_called()

    def test_exec_email_send(self):
        result = self.executor._exec_email('отправь письмо')
        assert result

    def test_exec_email_search(self):
        result = self.executor._exec_email('найди письмо от Ивана')
        assert result

    def test_exec_email_read(self):
        result = self.executor._exec_email('прочитай почту')
        assert result

    def test_exec_calendar_create(self):
        result = self.executor._exec_calendar('создай встречу на завтра')
        assert result

    def test_exec_calendar_free_slots(self):
        result = self.executor._exec_calendar('покажи свободные слоты')
        assert result

    def test_exec_calendar_list(self):
        result = self.executor._exec_calendar('что у меня сегодня')
        assert result

    def test_exec_contacts(self):
        result = self.executor._exec_contacts('найди контакт')
        assert not result['success']
        assert 'BLOCKED' in result['message']

    def test_exec_weather_success(self):
        self.tool_layer.use.return_value = {'success': True, 'temp': 25}
        result = self.executor._exec_weather('погода в Москве')
        assert result['success']

    def test_exec_weather_fallback(self):
        self.tool_layer.use.side_effect = [
            {'success': False},  # weather tool fails
            {'success': True, 'results': []},  # search fallback
        ]
        result = self.executor._exec_weather('погода в Москве')
        assert result.get('intent') == 'weather'

    def test_exec_time_telaviv(self):
        result = self.executor._exec_time('сколько времени в тель-авив')
        assert result['success']
        assert 'Jerusalem' in result['timezone']

    def test_exec_time_moscow(self):
        result = self.executor._exec_time('время moscow')
        assert result['success']

    def test_exec_time_unknown_city(self):
        result = self.executor._exec_time('время')
        assert not result['success']
        assert 'blocked' in result.get('status', '').lower()

    def test_exec_currency_success(self):
        self.tool_layer.use.return_value = {'success': True, 'rate': 3.5}
        result = self.executor._exec_currency('курс доллара')
        assert result['success']

    def test_exec_currency_fallback(self):
        self.tool_layer.use.side_effect = [
            {'success': False},
            {'success': True, 'results': []},
        ]
        result = self.executor._exec_currency('курс евро')
        assert result.get('intent') == 'currency'

    def test_exec_sports(self):
        self.tool_layer.use.return_value = {'success': True, 'results': []}
        result = self.executor._exec_sports('расписание матчей')
        assert result.get('intent') == 'sports'

    def test_exec_user_files_blocked(self):
        self.tool_layer.use.return_value = {'success': False}
        result = self.executor._exec_user_files('найди документ')
        assert not result['success']

    def test_exec_user_files_success(self):
        self.tool_layer.use.return_value = {'success': True, 'files': ['a.txt']}
        result = self.executor._exec_user_files('найди документ /docs')
        assert result['success']

    def test_exec_general(self):
        result = self.executor._exec_general('do something')
        assert result['success']
        assert 'file' in result

    # ── execute (main entry point) ────────────────────────────────────────

    def test_execute_email(self):
        result = self.executor.execute('отправь email на test@example.com')
        assert 'intent' in result

    def test_execute_general_pdf(self):
        result = self.executor.execute('создай pdf отчёт')
        assert 'intent' in result

    def test_execute_general_excel(self):
        result = self.executor.execute('создай excel таблицу')
        assert 'intent' in result

    def test_execute_general_health(self):
        with patch('importlib.import_module', side_effect=ImportError):
            result = self.executor.execute('проверь здоровье системы health check')
        assert 'intent' in result

    def test_legacy_detect_pdf(self):
        assert self.executor._legacy_detect('создай pdf') == 'pdf'

    def test_legacy_detect_excel(self):
        assert self.executor._legacy_detect('создай excel') == 'excel'

    def test_legacy_detect_pptx(self):
        assert self.executor._legacy_detect('сделай слайды powerpoint') == 'pptx'

    def test_legacy_detect_archive(self):
        assert self.executor._legacy_detect('запакуй в zip') == 'archive'

    def test_legacy_detect_screenshot(self):
        assert self.executor._legacy_detect('сделай скриншот') == 'screenshot'

    def test_legacy_detect_chart(self):
        assert self.executor._legacy_detect('построй график') == 'chart'

    def test_legacy_detect_translate(self):
        assert self.executor._legacy_detect('переведи текст') == 'translate'

    def test_legacy_detect_health(self):
        assert self.executor._legacy_detect('system status') == 'health'

    def test_legacy_detect_general(self):
        assert self.executor._legacy_detect('do something random') == 'general'
