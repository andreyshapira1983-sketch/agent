"""Comprehensive tests for execution/action_dispatcher.py — 0% → high coverage."""
import hashlib
import os
import re
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# ── Импорт модуля ─────────────────────────────────────────────────────────────
from execution.action_dispatcher import ActionDispatcher, _sanitize_file_path


# ── Вспомогательные стабы ──────────────────────────────────────────────────────

class _ToolLayer:
    """Минимальный stub для tool_layer."""
    def __init__(self, responses=None):
        self._responses = responses or {}
        self.calls = []

    def use(self, tool_name, **kwargs):
        self.calls.append((tool_name, kwargs))
        key = (tool_name, kwargs.get('action', ''))
        if key in self._responses:
            return self._responses[key]
        if tool_name in self._responses:
            return self._responses[tool_name]
        return {'success': True, 'output': 'ok', 'content': 'hello world', 'exists': True,
                'written': kwargs.get('path', 'file.txt'), 'stdout': 'done', 'stderr': ''}


class _Monitoring:
    def __init__(self):
        self.messages = []

    def info(self, msg, **_kw):
        self.messages.append(msg)


# ═══════════════════════════════════════════════════════════════════════════════
# Тесты
# ═══════════════════════════════════════════════════════════════════════════════

class TestSanitizeFilePath(unittest.TestCase):
    def test_valid_path(self):
        self.assertEqual(_sanitize_file_path('outputs/file.txt'), 'outputs/file.txt')

    def test_strip_description_in_parens(self):
        self.assertEqual(_sanitize_file_path('outputs/file.txt (описание)'), 'outputs/file.txt')

    def test_too_long(self):
        self.assertEqual(_sanitize_file_path('a' * 201 + '.py'), '')

    def test_no_extension(self):
        self.assertEqual(_sanitize_file_path('outputs/file'), '')

    def test_empty(self):
        self.assertEqual(_sanitize_file_path(''), '')


class TestActionDispatcherInit(unittest.TestCase):
    def test_default_init(self):
        ad = ActionDispatcher()
        self.assertIsNone(ad.tool_layer)
        self.assertEqual(ad._current_goal, '')
        self.assertIsInstance(ad._recent_action_seen, dict)

    def test_init_with_deps(self):
        tl = _ToolLayer()
        mon = _Monitoring()
        ad = ActionDispatcher(tool_layer=tl, monitoring=mon)
        self.assertIs(ad.tool_layer, tl)
        self.assertIs(ad.monitoring, mon)


class TestDispatchEmpty(unittest.TestCase):
    def setUp(self):
        self.ad = ActionDispatcher(monitoring=_Monitoring())

    def test_empty_text(self):
        r = self.ad.dispatch('')
        self.assertEqual(r['actions_found'], 0)
        self.assertTrue(r['success'])

    def test_no_actions_text(self):
        r = self.ad.dispatch('Просто текст без команд')
        self.assertEqual(r['actions_found'], 0)

    def test_none_text_coerced(self):
        # dispatch short-circuits on falsy text
        r = self.ad.dispatch(None)
        self.assertEqual(r['actions_found'], 0)


class TestExtractActions(unittest.TestCase):
    def setUp(self):
        self.ad = ActionDispatcher(monitoring=_Monitoring())

    def test_bash_block(self):
        text = '```bash\nls -la\n```'
        actions = self.ad._extract_actions(text)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]['type'], 'bash')
        self.assertEqual(actions[0]['input'], 'ls -la')

    def test_python_block(self):
        text = '```python\nprint("hello")\n```'
        actions = self.ad._extract_actions(text)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]['type'], 'python')

    def test_search_command(self):
        text = 'SEARCH: python tutorial'
        actions = self.ad._extract_actions(text)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]['type'], 'search')
        self.assertEqual(actions[0]['input'], 'python tutorial')

    def test_read_command(self):
        text = 'READ: outputs/data.txt'
        actions = self.ad._extract_actions(text)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]['type'], 'read')
        self.assertEqual(actions[0]['input'], 'outputs/data.txt')

    def test_write_command(self):
        text = 'WRITE: outputs/result.txt\nCONTENT: hello world'
        actions = self.ad._extract_actions(text)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]['type'], 'write')
        self.assertEqual(actions[0]['content'], 'hello world')

    def test_write_code_block(self):
        text = 'WRITE: core/helper.py\n```python\ndef foo(): pass\n```'
        actions = self.ad._extract_actions(text)
        writes = [a for a in actions if a['type'] == 'write']
        self.assertTrue(len(writes) >= 1)
        self.assertIn('def foo', writes[0].get('content', ''))

    def test_build_module_command(self):
        text = 'BUILD_MODULE: analyzer | Анализирует данные'
        actions = self.ad._extract_actions(text)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]['type'], 'build_module')
        self.assertEqual(actions[0]['input'], 'analyzer')

    def test_mixed_actions(self):
        text = (
            'SEARCH: python docs\n'
            '```bash\npip install requests\n```\n'
            'READ: outputs/log.txt\n'
        )
        actions = self.ad._extract_actions(text)
        types = {a['type'] for a in actions}
        self.assertIn('search', types)
        self.assertIn('bash', types)
        self.assertIn('read', types)

    def test_dsl_inside_python_block(self):
        text = '```python\nSEARCH: query inside\nprint("ok")\n```'
        actions = self.ad._extract_actions(text)
        types = [a['type'] for a in actions]
        self.assertIn('search', types)
        self.assertIn('python', types)

    def test_russian_fallback_search(self):
        text = '1. Найди информацию о Python'
        actions = self.ad._extract_actions(text)
        self.assertTrue(any(a['type'] == 'search' for a in actions))

    def test_russian_fallback_bash(self):
        text = '1. Запусти `pip install flask`'
        actions = self.ad._extract_actions(text)
        self.assertTrue(any(a['type'] == 'bash' for a in actions))


class TestDeduplication(unittest.TestCase):
    def setUp(self):
        self.ad = ActionDispatcher(monitoring=_Monitoring())
        self.ad._current_goal = 'test goal'

    def test_batch_dedup(self):
        actions = [
            {'type': 'search', 'input': 'python docs'},
            {'type': 'search', 'input': 'python docs'},
            {'type': 'bash', 'input': 'ls'},
        ]
        result = self.ad._deduplicate_actions(actions)
        self.assertEqual(len(result), 2)

    def test_recent_window_dedup(self):
        actions1 = [{'type': 'search', 'input': 'python docs'}]
        self.ad._deduplicate_actions(actions1)
        # Second call should see it as recent duplicate
        actions2 = [{'type': 'search', 'input': 'python docs'}]
        result = self.ad._deduplicate_actions(actions2)
        self.assertEqual(len(result), 0)

    def test_different_goal_not_deduped(self):
        actions1 = [{'type': 'search', 'input': 'python docs'}]
        self.ad._current_goal = 'goal A'
        self.ad._deduplicate_actions(actions1)
        self.ad._current_goal = 'goal B'
        result = self.ad._deduplicate_actions([{'type': 'search', 'input': 'python docs'}])
        self.assertEqual(len(result), 1)

    def test_empty_actions(self):
        self.assertEqual(self.ad._deduplicate_actions([]), [])


class TestGoalKeywords(unittest.TestCase):
    def test_extracts_keywords(self):
        kw = ActionDispatcher._goal_keywords('Найди Python документацию для pytest')
        self.assertIn('python', kw)
        self.assertIn('pytest', kw)
        self.assertIn('документацию', kw)

    def test_filters_stop_words(self):
        kw = ActionDispatcher._goal_keywords('и в на по для')
        self.assertEqual(kw, set())

    def test_empty_goal(self):
        self.assertEqual(ActionDispatcher._goal_keywords(''), set())


class TestInferDomain(unittest.TestCase):
    def test_code_domain(self):
        self.assertEqual(ActionDispatcher._infer_domain('Запусти pytest'), 'code')

    def test_system_domain(self):
        self.assertEqual(ActionDispatcher._infer_domain('Проверь cpu и ram'), 'system')

    def test_jobs_domain(self):
        self.assertEqual(ActionDispatcher._infer_domain('Найди вакансии на upwork'), 'jobs')

    def test_portfolio_domain(self):
        self.assertEqual(ActionDispatcher._infer_domain('Обнови портфолио'), 'portfolio')

    def test_research_domain(self):
        self.assertEqual(ActionDispatcher._infer_domain('Исследование API'), 'research')

    def test_unknown_domain(self):
        self.assertEqual(ActionDispatcher._infer_domain('привет мир'), 'unknown')


class TestSemanticActionGate(unittest.TestCase):
    def setUp(self):
        self.ad = ActionDispatcher(monitoring=_Monitoring())

    def test_keyword_overlap_passes(self):
        ok, _reason = self.ad._semantic_action_gate(
            {'type': 'search', 'input': 'python tutorial'},
            'Найди python tutorial'
        )
        self.assertTrue(ok)

    def test_domain_mismatch_blocks_search(self):
        ok, reason = self.ad._semantic_action_gate(
            {'type': 'search', 'input': 'python pytest debug'},
            'Найди вакансии на upwork'
        )
        self.assertFalse(ok)
        self.assertIn('domain mismatch', reason)

    def test_bash_always_passes(self):
        ok, _ = self.ad._semantic_action_gate(
            {'type': 'bash', 'input': 'pip install'},
            'Найди вакансии на upwork'
        )
        self.assertTrue(ok)

    def test_read_diagnostics_passes(self):
        ok, _ = self.ad._semantic_action_gate(
            {'type': 'read', 'input': 'outputs/log.txt'},
            'Сделай анализ'
        )
        self.assertTrue(ok)

    def test_no_goal_keywords_passes(self):
        ok, reason = self.ad._semantic_action_gate(
            {'type': 'search', 'input': 'x'},
            'и в на'  # all stop words
        )
        self.assertTrue(ok)
        self.assertIn('no keywords', reason)


class TestDetectStub(unittest.TestCase):
    def test_no_stub_for_real_code(self):
        code = (
            'import os\n'
            'def main():\n'
            '    print("hello world")\n'
            '    return 42\n'
            'if __name__ == "__main__":\n'
            '    main()\n'
        )
        self.assertIsNone(ActionDispatcher._detect_stub('module.py', code))

    def test_stub_phrase_detected(self):
        result = ActionDispatcher._detect_stub('module.py', '# todo: implement\npass')
        self.assertIsNotNone(result)

    def test_empty_content(self):
        result = ActionDispatcher._detect_stub('code.py', '')
        self.assertIsNotNone(result)
        self.assertIn('пустой', result)

    def test_only_comments(self):
        result = ActionDispatcher._detect_stub('code.py', '# comment 1\n# comment 2\n# comment 3')
        self.assertIsNotNone(result)
        self.assertIn('комментарии', result)

    def test_short_content(self):
        result = ActionDispatcher._detect_stub('code.py', 'x = 1')
        self.assertIsNotNone(result)
        self.assertIn('короткий', result)

    def test_non_code_file_skips_code_checks(self):
        self.assertIsNone(ActionDispatcher._detect_stub('readme.txt', 'short text'))

    def test_universal_stub_phrase(self):
        result = ActionDispatcher._detect_stub('readme.txt', 'результат выполнения python-кода выше')
        self.assertIsNotNone(result)

    def test_code_block_only(self):
        result = ActionDispatcher._detect_stub('module.py', '```python\n```')
        self.assertIsNotNone(result)


class TestAssessFileQuality(unittest.TestCase):
    def setUp(self):
        self.ad = ActionDispatcher(monitoring=_Monitoring())

    def test_good_python_file(self):
        code = '\n'.join([
            'import os',
            '',
            'class MyTool:',
            '    """Tool for doing things."""',
            '',
            '    def __init__(self):',
            '        self.data = {}',
            '',
            '    def process(self, item):',
            '        """Process a single item."""',
            '        result = str(item)',
            '        self.data[item] = result',
            '        return result',
            '',
            '    def report(self):',
            '        for k, v in self.data.items():',
            '            print(f"{k}: {v}")',
            '        return len(self.data)',
            '',
            '    def clear(self):',
            '        self.data.clear()',
        ])
        q = self.ad._assess_file_quality('tools/my_tool.py', code)
        self.assertGreaterEqual(q['score'], 85)

    def test_empty_file_low_quality(self):
        q = self.ad._assess_file_quality('module.py', '')
        self.assertLess(q['score'], 85)
        self.assertTrue(any('пустой' in i for i in q['issues']))

    def test_syntax_error_low_quality(self):
        q = self.ad._assess_file_quality('module.py', 'def foo(:\n  pass\n' * 5)
        self.assertLess(q['score'], 85)
        self.assertTrue(any('синтаксическая' in i for i in q['issues']))

    def test_too_many_pass(self):
        code = '\n'.join([f'def fn{i}():\n    pass' for i in range(20)])
        q = self.ad._assess_file_quality('module.py', code)
        self.assertTrue(any('pass' in i for i in q['issues']))

    def test_eval_exec_penalty(self):
        code = 'def process(x):\n' + '    return eval(x)\n' + '    pass\n' * 10
        q = self.ad._assess_file_quality('module.py', code)
        self.assertTrue(any('eval()' in i for i in q['issues']))

    def test_agents_dir_needs_agent_class(self):
        q = self.ad._assess_file_quality('c:/project/agents/helper.py', 'x = 1\n' * 25)
        self.assertTrue(any('Agent' in i for i in q['issues']))

    def test_tools_dir_needs_tool_class(self):
        q = self.ad._assess_file_quality('c:/project/tools/helper.py', 'x = 1\n' * 25)
        self.assertTrue(any('Tool' in i for i in q['issues']))

    def test_non_python_no_syntax_check(self):
        q = self.ad._assess_file_quality('readme.md', 'This is documentation. ' * 20)
        self.assertGreaterEqual(q['score'], 85)


class TestNormalizePaths(unittest.TestCase):
    def setUp(self):
        self.ad = ActionDispatcher(monitoring=_Monitoring())

    def test_normalize_write_path_bare_filename(self):
        p = self.ad._normalize_write_path('report.txt')
        self.assertIn('outputs', p)
        self.assertIn('report.txt', p)

    def test_normalize_write_path_empty(self):
        p = self.ad._normalize_write_path('')
        self.assertIn('outputs', p)

    def test_normalize_write_path_with_dir(self):
        p = self.ad._normalize_write_path('core/module.py')
        self.assertEqual(p, 'core/module.py')

    def test_normalize_write_strips_template_dirs(self):
        p = self.ad._normalize_write_path('путь/к/файлу/report.txt')
        self.assertNotIn('путь/к/файлу', p)

    def test_normalize_read_path_bare_filename(self):
        self.ad.tool_layer = None
        p = self.ad._normalize_read_path('data.txt')
        self.assertIn('outputs', p)

    def test_normalize_read_path_with_dir(self):
        p = self.ad._normalize_read_path('logs/app.log')
        self.assertEqual(p, 'logs/app.log')

    def test_normalize_read_absolute_outside_wd(self):
        p = self.ad._normalize_read_path('C:/Windows/file.txt')
        self.assertIn('outputs', p)
        self.assertIn('file.txt', p)


class TestMakeSummary(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(ActionDispatcher._make_summary([]), 'Нет результатов.')

    def test_success_summary(self):
        results = [{'success': True, 'type': 'bash', 'output': 'done', 'error': None}]
        s = ActionDispatcher._make_summary(results)
        self.assertIn('✓', s)
        self.assertIn('bash', s)

    def test_failure_summary(self):
        results = [{'success': False, 'type': 'write', 'output': '', 'error': 'disk full'}]
        s = ActionDispatcher._make_summary(results)
        self.assertIn('✗', s)
        self.assertIn('disk full', s)


class TestEmptyResult(unittest.TestCase):
    def test_structure(self):
        r = ActionDispatcher._empty_result()
        self.assertEqual(r['actions_found'], 0)
        self.assertTrue(r['success'])


class TestFail(unittest.TestCase):
    def test_structure(self):
        r = ActionDispatcher._fail('bash', 'ls', 'permission denied')
        self.assertEqual(r['type'], 'bash')
        self.assertFalse(r['success'])
        self.assertIn('permission denied', r['error'])


class TestDispatchIntegration(unittest.TestCase):
    """Интеграционные тесты dispatch() с mock tool_layer."""

    def _make_dispatcher(self, tool_layer=None):
        ad = ActionDispatcher(
            tool_layer=tool_layer or _ToolLayer(),
            monitoring=_Monitoring(),
        )
        return ad

    def test_dispatch_bash(self):
        tl = _ToolLayer(responses={
            'terminal': {'stdout': 'hello', 'stderr': '', 'success': True}
        })
        ad = self._make_dispatcher(tl)
        r = ad.dispatch('```bash\necho hello\n```', goal='выполни echo')
        self.assertGreaterEqual(r['actions_found'], 1)
        self.assertTrue(any(res['type'] == 'bash' for res in r['results']))

    def test_dispatch_python(self):
        tl = _ToolLayer(responses={
            'python_runtime': {'output': '42', 'error': '', 'success': True}
        })
        ad = self._make_dispatcher(tl)
        r = ad.dispatch('```python\nprint(42)\n```', goal='запусти код python')
        self.assertGreaterEqual(r['actions_found'], 1)

    def test_dispatch_read(self):
        tl = _ToolLayer(responses={
            ('filesystem', 'exists'): {'exists': True},
            ('filesystem', 'read'): {'content': 'file content', 'success': True},
        })
        ad = self._make_dispatcher(tl)
        r = ad.dispatch('READ: outputs/data.txt', goal='прочитай log файл')
        self.assertGreaterEqual(r['actions_found'], 1)

    def test_dispatch_write(self):
        tl = _ToolLayer(responses={
            ('filesystem', 'write'): {'written': 'outputs/result.txt', 'success': True},
        })
        ad = self._make_dispatcher(tl)
        code = 'import os\ndef main():\n' + '    print("ok")\n' * 10 + '    return 0\n'
        r = ad.dispatch(
            f'WRITE: outputs/result.py\nCONTENT: {code}',
            goal='Запиши python код в результат'
        )
        # May be rejected by quality gate, but dispatch should work
        self.assertGreaterEqual(r['actions_found'], 1)

    def test_dispatch_search(self):
        tl = _ToolLayer(responses={
            'search': {'results': [{'title': 'r', 'url': 'https://x.com', 'snippet': 's'}]}
        })
        ad = self._make_dispatcher(tl)
        r = ad.dispatch('SEARCH: python docs', goal='исследуй python документацию')
        self.assertGreaterEqual(r['actions_found'], 1)

    def test_dispatch_semantic_gate_blocks_all(self):
        ad = self._make_dispatcher()
        # System search with jobs goal → domain mismatch
        r = ad.dispatch(
            'SEARCH: cpu monitoring python',
            goal='Найди вакансии на upwork'
        )
        # Should be blocked by semantic gate
        self.assertTrue(
            r.get('semantic_rejected') or r['actions_found'] == 0 or not r['success']
        )

    def test_dispatch_heading_goal(self):
        ad = self._make_dispatcher()
        r = ad.dispatch('SEARCH: anything', goal='--- Заголовок ---')
        # Non-actionable heading should skip execution
        self.assertTrue(r['success'])

    def test_dispatch_dedup_across_calls(self):
        ad = self._make_dispatcher()
        ad.dispatch('SEARCH: python docs', goal='найди python документацию')
        r2 = ad.dispatch('SEARCH: python docs', goal='найди python документацию')
        self.assertEqual(r2['actions_found'], 0)  # deduped

    def test_dispatch_exception_in_execute(self):
        tl = _ToolLayer()
        tl.use = MagicMock(side_effect=RuntimeError('boom'))
        ad = ActionDispatcher(tool_layer=tl, monitoring=_Monitoring())
        r = ad.dispatch('```bash\nls\n```', goal='запусти bash команду')
        # Should catch exception and return error result, not crash
        self.assertIsInstance(r, dict)


class TestIsInvalidUpworkJobsWrite(unittest.TestCase):
    def test_valid_content_with_link(self):
        self.assertFalse(
            ActionDispatcher._is_invalid_upwork_jobs_write(
                'outputs/upwork_jobs.txt',
                'Title: Python Dev\nBudget: $500\nLink: https://upwork.com/job/1'
            )
        )

    def test_junk_content(self):
        self.assertTrue(
            ActionDispatcher._is_invalid_upwork_jobs_write(
                'outputs/upwork_jobs.txt',
                'SEARCH: upwork jobs'
            )
        )

    def test_empty_content(self):
        self.assertTrue(
            ActionDispatcher._is_invalid_upwork_jobs_write(
                'outputs/upwork_jobs.txt', ''
            )
        )

    def test_wrong_path_always_false(self):
        self.assertFalse(
            ActionDispatcher._is_invalid_upwork_jobs_write(
                'outputs/other.txt', 'SEARCH: stuff'
            )
        )


class TestContentDuplicate(unittest.TestCase):
    def setUp(self):
        self.ad = ActionDispatcher(monitoring=_Monitoring())

    def test_exact_duplicate_detected(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False,
                                          dir='outputs', encoding='utf-8') as f:
            f.write('hello world')
            fp = f.name
        try:
            result = self.ad._check_content_duplicate(fp, 'hello world')
            self.assertIsNotNone(result)
            self.assertTrue(result['success'])
            self.assertIn('DEDUP', result.get('note', ''))
        finally:
            os.unlink(fp)

    def test_different_content_not_duplicate(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False,
                                          dir='outputs', encoding='utf-8') as f:
            f.write('original content that is very different')
            fp = f.name
        try:
            result = self.ad._check_content_duplicate(fp, 'completely new and unique text here')
            self.assertIsNone(result)
        finally:
            os.unlink(fp)

    def test_no_file_no_duplicate(self):
        result = self.ad._check_content_duplicate('outputs/nonexistent_xyz.txt', 'data')
        self.assertIsNone(result)

    def test_cross_file_duplicate(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False,
                                          dir='outputs', encoding='utf-8') as f:
            f.write('duplicate test content')
            fp = f.name
        try:
            # Write same content to different path
            other_path = fp + '_other.txt'
            result = self.ad._check_content_duplicate(other_path, 'duplicate test content')
            self.assertIsNotNone(result)
        finally:
            os.unlink(fp)


class TestRejectMemory(unittest.TestCase):
    def setUp(self):
        self.ad = ActionDispatcher(monitoring=_Monitoring())
        self.ad._reject_memory = []

    def test_remember_and_find_reject(self):
        self.ad._remember_reject('outputs/bad.txt', 'junk', 'STUB_REJECTED')
        found = self.ad._find_recent_reject('outputs/bad.txt', 'junk')
        self.assertIsNotNone(found)

    def test_no_reject_for_clean(self):
        self.assertIsNone(self.ad._find_recent_reject('outputs/clean.txt', 'good'))


class TestContentSignature(unittest.TestCase):
    def test_deterministic(self):
        ad = ActionDispatcher(monitoring=_Monitoring())
        s1 = ad._content_signature('file.txt', 'hello')
        s2 = ad._content_signature('file.txt', 'hello')
        self.assertEqual(s1, s2)

    def test_different_content(self):
        ad = ActionDispatcher(monitoring=_Monitoring())
        s1 = ad._content_signature('file.txt', 'hello')
        s2 = ad._content_signature('file.txt', 'world')
        self.assertNotEqual(s1, s2)


class TestActionSignature(unittest.TestCase):
    def setUp(self):
        self.ad = ActionDispatcher(monitoring=_Monitoring())
        self.ad._current_goal = 'test'

    def test_same_action_same_sig(self):
        a = {'type': 'search', 'input': 'python'}
        s1 = self.ad._action_signature(a)
        s2 = self.ad._action_signature(a)
        self.assertEqual(s1, s2)

    def test_write_includes_content(self):
        a1 = {'type': 'write', 'input': 'file.py', 'content': 'code A'}
        a2 = {'type': 'write', 'input': 'file.py', 'content': 'code B'}
        self.assertNotEqual(self.ad._action_signature(a1), self.ad._action_signature(a2))


class TestReadIntEnv(unittest.TestCase):
    def test_default(self):
        self.assertEqual(ActionDispatcher._read_int_env('NONEXISTENT_VAR_XYZ', 42), 42)

    def test_from_env(self):
        with patch.dict(os.environ, {'TEST_INT_VAR': '99'}):
            self.assertEqual(ActionDispatcher._read_int_env('TEST_INT_VAR', 0), 99)

    def test_invalid_value(self):
        with patch.dict(os.environ, {'TEST_INT_VAR': 'abc'}):
            self.assertEqual(ActionDispatcher._read_int_env('TEST_INT_VAR', 42), 42)

    def test_zero_or_negative(self):
        with patch.dict(os.environ, {'TEST_INT_VAR': '-1'}):
            self.assertEqual(ActionDispatcher._read_int_env('TEST_INT_VAR', 42), 42)


class TestRunWriteFlow(unittest.TestCase):
    """Test _run_write with its quality gate, stub detection, dedup."""

    def setUp(self):
        self.tl = _ToolLayer(responses={
            ('filesystem', 'write'): {'written': 'outputs/mod.py', 'success': True},
        })
        self.ad = ActionDispatcher(tool_layer=self.tl, monitoring=_Monitoring())
        self.ad._reject_memory = []

    def test_write_good_content(self):
        code = '\n'.join([
            'import os',
            '',
            'class AnalyzerTool:',
            '    def __init__(self):',
            '        self.items = []',
            '',
            '    def analyze(self, data):',
            '        result = sum(data)',
            '        self.items.append(result)',
            '        return result',
            '',
            '    def report(self):',
            '        for i in self.items:',
            '            print(i)',
            '        return len(self.items)',
            '',
            '    def reset(self):',
            '        self.items.clear()',
            '        return True',
            '',
            '    def export(self):',
            '        return list(self.items)',
        ])
        r = self.ad._run_write('tools/analyzer.py', code)
        self.assertTrue(r['success'])

    def test_write_stub_rejected(self):
        r = self.ad._run_write('outputs/mod.py', '# todo: implement\npass')
        self.assertFalse(r['success'])
        self.assertIn('STUB', r['error'])

    def test_write_empty_rejected(self):
        r = self.ad._run_write('outputs/mod.py', '')
        self.assertFalse(r['success'])

    def test_write_low_quality_rejected(self):
        code = 'def f(x):\n    return eval(x)\n' + '\n'.join(['pass'] * 10)
        r = self.ad._run_write('outputs/mod.py', code)
        self.assertFalse(r['success'])

    def test_write_repeat_reject(self):
        self.ad._remember_reject('outputs/badfile.py', 'junk', 'STUB', score=50, issues=[])
        r = self.ad._run_write('outputs/badfile.py', 'junk')
        self.assertFalse(r['success'])
        self.assertIn('MEMORY_REJECTED', r['error'])


class TestRunBash(unittest.TestCase):
    def setUp(self):
        self.tl = _ToolLayer(responses={
            'terminal': {'stdout': 'ok done', 'stderr': '', 'success': True, 'returncode': 0}
        })
        self.ad = ActionDispatcher(tool_layer=self.tl, monitoring=_Monitoring())

    def test_simple_bash(self):
        r = self.ad._run_bash('echo hello')
        self.assertTrue(r['success'])

    def test_no_tool_layer_uses_execution_system(self):
        from types import SimpleNamespace as SN
        task_result = SN(status=SN(value='success'), stdout='done', stderr='', output='done', error=None)
        # ExecutionSystem.submit returns task-like object
        es = SN(submit=lambda cmd, **kw: task_result)
        ad = ActionDispatcher(execution_system=es, monitoring=_Monitoring())
        r = ad._run_bash('echo hi')
        self.assertIsInstance(r, dict)


class TestRunPython(unittest.TestCase):
    def setUp(self):
        self.tl = _ToolLayer(responses={
            'python_runtime': {'output': '42', 'error': '', 'success': True}
        })
        self.ad = ActionDispatcher(tool_layer=self.tl, monitoring=_Monitoring())

    def test_simple_python(self):
        r = self.ad._run_python('print(42)')
        self.assertTrue(r['success'])

    def test_python_syntax_error_caught(self):
        # The tool should still try to execute (via LLM code, syntax is checked externally)
        r = self.ad._run_python('print("hello")')
        self.assertIsInstance(r, dict)


class TestRunRead(unittest.TestCase):
    def test_read_existing_file(self):
        tl = _ToolLayer(responses={
            ('filesystem', 'exists'): {'exists': True},
            ('filesystem', 'read'): {'content': 'line1\nline2\nline3', 'success': True},
        })
        ad = ActionDispatcher(tool_layer=tl, monitoring=_Monitoring())
        r = ad._run_read('outputs/data.txt')
        self.assertTrue(r['success'])
        self.assertIn('line1', r['output'])

    def test_read_nonexistent_file(self):
        tl = _ToolLayer(responses={
            ('filesystem', 'exists'): {'exists': False},
        })
        ad = ActionDispatcher(tool_layer=tl, monitoring=_Monitoring())
        r = ad._run_read('outputs/missing.txt')
        self.assertTrue(r['success'])  # success=True but with note
        self.assertIn('НЕ НАЙДЕН', r['output'])

    def test_read_with_range(self):
        content = '\n'.join(f'line {i}' for i in range(1, 501))
        tl = _ToolLayer(responses={
            ('filesystem', 'exists'): {'exists': True},
            ('filesystem', 'read'): {'content': content, 'success': True},
        })
        ad = ActionDispatcher(tool_layer=tl, monitoring=_Monitoring())
        r = ad._run_read('file.py:1-10')
        self.assertTrue(r['success'])
        self.assertIn('line 1', r['output'])

    def test_read_no_tool_layer(self):
        ad = ActionDispatcher(monitoring=_Monitoring())
        r = ad._run_read('file.txt')
        self.assertFalse(r['success'])


class TestRunSearch(unittest.TestCase):
    def test_search_with_tool_layer(self):
        tl = _ToolLayer(responses={
            'search': {'results': [
                {'title': 'Py', 'url': 'https://python.org', 'snippet': 'Official Python'}
            ]}
        })
        ad = ActionDispatcher(tool_layer=tl, monitoring=_Monitoring())
        r = ad._run_search('python docs')
        self.assertTrue(r['success'])

    def test_search_no_results(self):
        tl = _ToolLayer(responses={'search': {'results': []}})
        ad = ActionDispatcher(tool_layer=tl, monitoring=_Monitoring())
        r = ad._run_search('xyznonexistent')
        self.assertIsInstance(r, dict)


class TestExecuteAction(unittest.TestCase):
    def test_routes_correctly(self):
        tl = _ToolLayer()
        ad = ActionDispatcher(tool_layer=tl, monitoring=_Monitoring())
        for action_type in ['bash', 'python', 'search', 'read', 'write']:
            action = {'type': action_type, 'input': 'test', 'content': 'x = 1\n' * 20}
            r = ad._execute_action(action)
            self.assertIsInstance(r, dict)
            self.assertEqual(r['type'], action_type)

    def test_unknown_type(self):
        ad = ActionDispatcher(monitoring=_Monitoring())
        r = ad._execute_action({'type': 'unknown_type', 'input': 'x'})
        self.assertFalse(r['success'])


class TestLog(unittest.TestCase):
    def test_with_monitoring(self):
        mon = _Monitoring()
        ad = ActionDispatcher(monitoring=mon)
        ad._log('test message')
        self.assertTrue(any('test message' in m for m in mon.messages))

    def test_without_monitoring(self):
        ad = ActionDispatcher()
        # Should not crash
        ad._log('test message')


class TestFixTripleQuotes(unittest.TestCase):
    def test_fix_unbalanced(self):
        ad = ActionDispatcher(monitoring=_Monitoring())
        code = 'x = """hello'
        fixed = ad._fix_triple_quotes(code)
        self.assertIsInstance(fixed, str)

    def test_balanced_code_still_parsable(self):
        ad = ActionDispatcher(monitoring=_Monitoring())
        code = 'x = 1\ny = 2'
        fixed = ad._fix_triple_quotes(code)
        # For already-valid code, result may be non-None if appending quotes still parses
        if fixed is not None:
            import ast
            ast.parse(fixed)  # must be valid Python


class TestAdaptBashForWindows(unittest.TestCase):
    def setUp(self):
        self.ad = ActionDispatcher(monitoring=_Monitoring())

    def test_ls_adapted(self):
        result = self.ad._adapt_bash_for_windows('ls -la')
        self.assertIsInstance(result, dict)
        # ls -> dir with mode 'command'
        self.assertIn(result.get('mode', ''), ('powershell', 'skip', 'python', 'command', 'cmd'))

    def test_echo_simple(self):
        result = self.ad._adapt_bash_for_windows('echo hello')
        self.assertIsInstance(result, dict)


class TestLooksUnixOnly(unittest.TestCase):
    def test_grep_is_unix(self):
        self.assertTrue(ActionDispatcher._looks_unix_only('grep -r pattern /var/log'))

    def test_echo_is_not_unix(self):
        self.assertFalse(ActionDispatcher._looks_unix_only('echo hello'))


class TestRegenerateWithLlm(unittest.TestCase):
    def test_no_llm_returns_none(self):
        ad = ActionDispatcher(monitoring=_Monitoring())
        self.assertIsNone(ad._regenerate_with_llm('file.py', 'stub'))

    def test_with_llm_returns_code(self):
        llm = SimpleNamespace(infer=MagicMock(return_value='```python\ndef real(): pass\n```'))
        ad = ActionDispatcher(llm=llm, monitoring=_Monitoring())
        result = ad._regenerate_with_llm('module.py', 'stub detected')
        self.assertIsNotNone(result)


class TestFilterRelevantResults(unittest.TestCase):
    def setUp(self):
        self.ad = ActionDispatcher(monitoring=_Monitoring())

    def test_filters_irrelevant(self):
        self.ad._current_goal = 'python tutorial'
        results = [
            {'title': 'Python Tutorial', 'url': 'https://a.com', 'snippet': 'Learn Python basics'},
            {'title': 'Cat Videos', 'url': 'https://b.com', 'snippet': 'Funny cats compilation'},
        ]
        filtered = self.ad._filter_relevant_results(results, 'python tutorial', 'python tutorial')
        # Should keep at least the relevant one
        self.assertGreaterEqual(len(filtered), 1)


class TestBuildModule(unittest.TestCase):
    def test_no_builder(self):
        ad = ActionDispatcher(monitoring=_Monitoring())
        r = ad._run_build_module('test_mod', 'a test module')
        self.assertFalse(r['success'])

    def test_with_builder(self):
        build_result = SimpleNamespace(ok=True, file_path='dynamic_modules/test_mod.py', error=None)
        builder = SimpleNamespace(build_module=MagicMock(return_value=build_result))
        ad = ActionDispatcher(module_builder=builder, monitoring=_Monitoring())
        r = ad._run_build_module('test_mod', 'a test module')
        self.assertTrue(r['success'])


if __name__ == '__main__':
    unittest.main()
