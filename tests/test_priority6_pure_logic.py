"""
Priority-6 coverage: pure-logic 0% modules.
  - validation/data_validation.py  (DataValidator, ValidationResult, schemas)
  - multilingual/multilingual.py   (MultilingualSystem, detect_language, translate)
  - software_dev/software_dev.py   (CodeAnalysisResult, TestSuite, BuildResult, analyze)
  - skills/portfolio_builder.py    (PortfolioBuilder, format, send)
"""
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ═══════════════════════════════════════════════════════════════════════════
# 1. validation/data_validation.py
# ═══════════════════════════════════════════════════════════════════════════
from validation.data_validation import DataValidator, ValidationResult, ValidationStatus


class TestValidationStatus(unittest.TestCase):
    def test_values(self):
        self.assertEqual(ValidationStatus.OK.value, 'ok')
        self.assertEqual(ValidationStatus.WARNING.value, 'warning')
        self.assertEqual(ValidationStatus.ERROR.value, 'error')


class TestValidationResult(unittest.TestCase):
    def test_default_ok(self):
        r = ValidationResult()
        self.assertTrue(r.is_valid)
        self.assertEqual(r.status, ValidationStatus.OK)
        self.assertEqual(r.errors, [])

    def test_add_error(self):
        r = ValidationResult()
        r.add_error('bad value', field='name')
        self.assertFalse(r.is_valid)
        self.assertEqual(r.status, ValidationStatus.ERROR)
        self.assertIn('name', r.field_errors)

    def test_add_warning(self):
        r = ValidationResult()
        r.add_warning('hmm')
        self.assertTrue(r.is_valid)
        self.assertEqual(r.status, ValidationStatus.WARNING)
        self.assertEqual(len(r.warnings), 1)

    def test_warning_doesnt_override_error(self):
        r = ValidationResult()
        r.add_error('err')
        r.add_warning('warn')
        self.assertEqual(r.status, ValidationStatus.ERROR)

    def test_to_dict(self):
        r = ValidationResult()
        r.add_error('x')
        d = r.to_dict()
        self.assertIn('status', d)
        self.assertIn('is_valid', d)
        self.assertFalse(d['is_valid'])

    def test_str_ok(self):
        r = ValidationResult()
        self.assertIn('OK', str(r))

    def test_str_error(self):
        r = ValidationResult()
        r.add_error('boom')
        self.assertIn('ОШИБКА', str(r))


class TestDataValidatorInit(unittest.TestCase):
    def test_default(self):
        v = DataValidator()
        self.assertFalse(v.strict)

    def test_strict(self):
        v = DataValidator(strict=True)
        self.assertTrue(v.strict)

    def test_system_schemas_loaded(self):
        v = DataValidator()
        self.assertIn('task', v._schemas)
        self.assertIn('episode', v._schemas)
        self.assertIn('llm_response', v._schemas)
        self.assertIn('command', v._schemas)
        self.assertIn('tool_call', v._schemas)


class TestDataValidatorValidate(unittest.TestCase):
    def setUp(self):
        self.v = DataValidator()

    def test_valid_task(self):
        r = self.v.validate({'goal': 'do something', 'priority': 2}, schema_name='task')
        self.assertTrue(r.is_valid)

    def test_missing_required_field(self):
        r = self.v.validate({}, schema_name='task')
        self.assertFalse(r.is_valid)
        self.assertIn('goal', r.field_errors)

    def test_wrong_type(self):
        r = self.v.validate({'goal': 123}, schema_name='task')
        self.assertFalse(r.is_valid)

    def test_min_length_violation(self):
        r = self.v.validate({'goal': ''}, schema_name='task')
        self.assertFalse(r.is_valid)

    def test_max_value_violation(self):
        r = self.v.validate({'goal': 'test', 'priority': 10}, schema_name='task')
        self.assertFalse(r.is_valid)

    def test_non_dict_data(self):
        r = self.v.validate('not a dict', schema_name='task')
        self.assertFalse(r.is_valid)
        self.assertIn('dict', r.errors[0])

    def test_unknown_schema(self):
        r = self.v.validate({'x': 1}, schema_name='nonexistent')
        self.assertTrue(r.is_valid)
        self.assertEqual(len(r.warnings), 1)

    def test_inline_schema(self):
        schema = {'name': {'type': str, 'required': True, 'min': 2}}
        r = self.v.validate({'name': 'AB'}, schema=schema)
        self.assertTrue(r.is_valid)

    def test_allowed_values_pass(self):
        r = self.v.validate(
            {'action': 'test', 'verdict': 'safe'},
            schema_name='sandbox_action',
        )
        self.assertTrue(r.is_valid)

    def test_allowed_values_fail(self):
        r = self.v.validate(
            {'action': 'test', 'verdict': 'bad_value'},
            schema_name='sandbox_action',
        )
        self.assertFalse(r.is_valid)

    def test_strict_mode(self):
        v = DataValidator(strict=True)
        # strict mode: warnings from actual schema validation become errors
        v.register_schema('lax', {'name': {'type': str, 'required': False}})
        r = v.validate({'name': 123}, schema_name='lax')
        # type mismatch is always error
        self.assertFalse(r.is_valid)

    def test_episode_valid(self):
        r = self.v.validate(
            {'task': 'do x', 'action': 'did y', 'result': 'ok', 'success': True},
            schema_name='episode',
        )
        self.assertTrue(r.is_valid)

    def test_tuple_type_check(self):
        # 'command' schema: timeout type is (int, float)
        r = self.v.validate(
            {'cmd': 'ls', 'timeout': 30},
            schema_name='command',
        )
        self.assertTrue(r.is_valid)
        r2 = self.v.validate(
            {'cmd': 'ls', 'timeout': 30.5},
            schema_name='command',
        )
        self.assertTrue(r2.is_valid)


class TestDataValidatorContract(unittest.TestCase):
    def test_register_and_run(self):
        v = DataValidator()

        def must_have_id(data):
            r = ValidationResult()
            if 'id' not in data:
                r.add_error('missing id')
            return r

        v.register_contract('has_id', must_have_id)
        r = v.validate_contract('has_id', {'id': 1})
        self.assertTrue(r.is_valid)

        r2 = v.validate_contract('has_id', {'name': 'test'})
        self.assertFalse(r2.is_valid)

    def test_unknown_contract(self):
        v = DataValidator()
        r = v.validate_contract('nope', {})
        self.assertTrue(r.is_valid)
        self.assertEqual(len(r.warnings), 1)


class TestDataValidatorRequireValid(unittest.TestCase):
    def test_pass(self):
        v = DataValidator()
        result = v.require_valid({'goal': 'ok'}, schema_name='task')
        self.assertTrue(result.is_valid)

    def test_raises(self):
        v = DataValidator()
        with self.assertRaises(ValueError):
            v.require_valid({}, schema_name='task')


class TestDataValidatorChecks(unittest.TestCase):
    def setUp(self):
        self.v = DataValidator()

    def test_check_type_ok(self):
        r = self.v.check_type('hello', str, field='x')
        self.assertTrue(r.is_valid)

    def test_check_type_fail(self):
        r = self.v.check_type(123, str, field='x')
        self.assertFalse(r.is_valid)

    def test_check_not_empty_ok(self):
        r = self.v.check_not_empty('hello')
        self.assertTrue(r.is_valid)

    def test_check_not_empty_fail_none(self):
        r = self.v.check_not_empty(None)
        self.assertFalse(r.is_valid)

    def test_check_not_empty_fail_string(self):
        r = self.v.check_not_empty('')
        self.assertFalse(r.is_valid)

    def test_check_not_empty_fail_list(self):
        r = self.v.check_not_empty([])
        self.assertFalse(r.is_valid)

    def test_check_range_ok(self):
        r = self.v.check_range(5, min_val=1, max_val=10, field='n')
        self.assertTrue(r.is_valid)

    def test_check_range_below(self):
        r = self.v.check_range(0, min_val=1, field='n')
        self.assertFalse(r.is_valid)

    def test_check_range_above(self):
        r = self.v.check_range(100, max_val=10, field='n')
        self.assertFalse(r.is_valid)

    def test_check_pattern_ok(self):
        r = self.v.check_pattern('hello123', r'^[a-z]+\d+$', field='code')
        self.assertTrue(r.is_valid)

    def test_check_pattern_fail(self):
        r = self.v.check_pattern('!!', r'^[a-z]+$', field='code')
        self.assertFalse(r.is_valid)

    def test_sanitize_string(self):
        s = self.v.sanitize_string('  hello\x00world  ', max_length=100)
        self.assertEqual(s, 'helloworld')

    def test_sanitize_truncate(self):
        s = self.v.sanitize_string('a' * 200, max_length=10)
        self.assertEqual(len(s), 10)

    def test_sanitize_non_string(self):
        s = self.v.sanitize_string(42)
        self.assertEqual(s, '42')


class TestDataValidatorSchemas(unittest.TestCase):
    """Test various built-in schemas."""

    def setUp(self):
        self.v = DataValidator()

    def test_tool_call_valid(self):
        r = self.v.validate({'tool': 'search', 'params': {}}, schema_name='tool_call')
        self.assertTrue(r.is_valid)

    def test_web_fetch_valid(self):
        r = self.v.validate({'url': 'http://example.com', 'content': 'hi'},
                            schema_name='web_fetch')
        self.assertTrue(r.is_valid)

    def test_web_fetch_short_url(self):
        r = self.v.validate({'url': 'x'}, schema_name='web_fetch')
        self.assertFalse(r.is_valid)

    def test_ethical_evaluation_valid(self):
        r = self.v.validate(
            {'action': 'read file', 'verdict': 'approved', 'score': 0.9},
            schema_name='ethical_evaluation',
        )
        self.assertTrue(r.is_valid)

    def test_ethical_evaluation_bad_verdict(self):
        r = self.v.validate(
            {'action': 'hack', 'verdict': 'idk'},
            schema_name='ethical_evaluation',
        )
        self.assertFalse(r.is_valid)

    def test_experience_pattern_valid(self):
        r = self.v.validate(
            {'pattern': 'retry on fail', 'frequency': 5, 'avg_success': 0.8},
            schema_name='experience_pattern',
        )
        self.assertTrue(r.is_valid)

    def test_verification_result_valid(self):
        r = self.v.validate(
            {'claim': 'earth is round', 'verified': True, 'confidence': 0.99},
            schema_name='verification_result',
        )
        self.assertTrue(r.is_valid)

    def test_cycle_result_valid(self):
        r = self.v.validate(
            {'cycle_id': 1, 'success': True, 'errors': []},
            schema_name='cycle_result',
        )
        self.assertTrue(r.is_valid)

    def test_knowledge_source_valid(self):
        r = self.v.validate(
            {'url_or_path': 'https://arxiv.org/123', 'source_type': 'arxiv'},
            schema_name='knowledge_source',
        )
        self.assertTrue(r.is_valid)

    def test_knowledge_source_bad_type(self):
        r = self.v.validate(
            {'url_or_path': 'x', 'source_type': 'unknown'},
            schema_name='knowledge_source',
        )
        self.assertFalse(r.is_valid)

    def test_register_custom_schema(self):
        self.v.register_schema('custom', {
            'x': {'type': int, 'required': True, 'min': 0, 'max': 100},
        })
        r = self.v.validate({'x': 50}, schema_name='custom')
        self.assertTrue(r.is_valid)

    def test_pattern_field_in_schema(self):
        self.v.register_schema('email', {
            'addr': {'type': str, 'required': True, 'pattern': r'^.+@.+\..+$'},
        })
        r = self.v.validate({'addr': 'test@example.com'}, schema_name='email')
        self.assertTrue(r.is_valid)
        r2 = self.v.validate({'addr': 'bad'}, schema_name='email')
        self.assertFalse(r2.is_valid)


# ═══════════════════════════════════════════════════════════════════════════
# 2. multilingual/multilingual.py
# ═══════════════════════════════════════════════════════════════════════════
from multilingual.multilingual import MultilingualSystem, TranslationResult, LANGUAGES


class TestLANGUAGES(unittest.TestCase):
    def test_contains_ru_en(self):
        self.assertIn('ru', LANGUAGES)
        self.assertIn('en', LANGUAGES)
        self.assertEqual(len(LANGUAGES), 15)


class TestTranslationResult(unittest.TestCase):
    def test_create(self):
        tr = TranslationResult('привет', 'hello', 'ru', 'en', 0.95)
        self.assertEqual(tr.original, 'привет')
        self.assertEqual(tr.translated, 'hello')

    def test_to_dict(self):
        tr = TranslationResult('x', 'y', 'ru', 'en')
        d = tr.to_dict()
        self.assertIn('original', d)
        self.assertIn('translated', d)
        self.assertIn('confidence', d)


class TestMultilingualDetect(unittest.TestCase):
    def setUp(self):
        self.ms = MultilingualSystem()

    def test_detect_russian(self):
        self.assertEqual(self.ms.detect_language('Привет, как дела?'), 'ru')

    def test_detect_empty(self):
        self.assertEqual(self.ms.detect_language(''), 'ru')  # default

    def test_detect_chinese(self):
        self.assertEqual(self.ms.detect_language('你好世界这是中文'), 'zh')

    def test_detect_japanese(self):
        # Need enough kana vs CJK to trigger 'ja' over 'zh'
        result = self.ms.detect_language('こんにちはせかいです')
        self.assertEqual(result, 'ja')

    def test_detect_korean(self):
        self.assertEqual(self.ms.detect_language('안녕하세요'), 'ko')

    def test_detect_arabic(self):
        self.assertEqual(self.ms.detect_language('مرحبا بالعالم'), 'ar')

    def test_detect_ukrainian(self):
        lang = self.ms.detect_language('Привіт як справи їжак')
        self.assertEqual(lang, 'uk')

    def test_detect_latin_fallback(self):
        # Pure Latin text without LLM -> fallback 'en'
        lang = self.ms.detect_language('Hello world, how are you?')
        self.assertEqual(lang, 'en')

    def test_detect_with_llm(self):
        core = MagicMock()
        core.reasoning.return_value = 'de'
        ms = MultilingualSystem(cognitive_core=core)
        lang = ms.detect_language('Guten Tag, wie geht es Ihnen?')
        self.assertEqual(lang, 'de')

    def test_detect_llm_bad_response(self):
        core = MagicMock()
        core.reasoning.return_value = 'unknown_lang_code_here'
        ms = MultilingualSystem(cognitive_core=core)
        lang = ms.detect_language('Some ambiguous text here')
        self.assertEqual(lang, 'en')  # fallback


class TestMultilingualTranslate(unittest.TestCase):
    def test_same_language_noop(self):
        ms = MultilingualSystem()
        r = ms.translate('Привет', target_lang='ru')
        self.assertEqual(r.translated, 'Привет')
        self.assertEqual(r.confidence, 1.0)

    def test_no_core_returns_original(self):
        ms = MultilingualSystem()
        r = ms.translate('Hello', target_lang='ru', source_lang='en')
        self.assertEqual(r.translated, 'Hello')
        self.assertEqual(r.confidence, 0.0)

    def test_with_core(self):
        core = MagicMock()
        core.reasoning.return_value = 'Привет'
        ms = MultilingualSystem(cognitive_core=core)
        r = ms.translate('Hello', target_lang='ru', source_lang='en')
        self.assertEqual(r.translated, 'Привет')
        self.assertEqual(r.confidence, 0.9)
        core.reasoning.assert_called_once()

    def test_translate_cache(self):
        core = MagicMock()
        core.reasoning.return_value = 'Bonjour'
        ms = MultilingualSystem(cognitive_core=core)
        r1 = ms.translate('Hello', target_lang='fr', source_lang='en')
        r2 = ms.translate('Hello', target_lang='fr', source_lang='en')
        self.assertEqual(r1.translated, r2.translated)
        self.assertEqual(core.reasoning.call_count, 1)  # cached

    def test_translate_core_error(self):
        core = MagicMock()
        core.reasoning.side_effect = RuntimeError('fail')
        ms = MultilingualSystem(cognitive_core=core)
        r = ms.translate('Hello', target_lang='fr', source_lang='en')
        self.assertEqual(r.confidence, 0.0)

    def test_translate_batch(self):
        core = MagicMock()
        core.reasoning.return_value = 'ok'
        ms = MultilingualSystem(cognitive_core=core)
        results = ms.translate_batch(['a', 'b'], target_lang='ru', source_lang='en')
        self.assertEqual(len(results), 2)


class TestMultilingualAdapt(unittest.TestCase):
    def test_adapt_same_lang(self):
        ms = MultilingualSystem()
        out = ms.adapt_for_user('Привет', 'ru')
        self.assertEqual(out, 'Привет')

    def test_adapt_calls_translate(self):
        core = MagicMock()
        core.reasoning.return_value = 'Hello'
        ms = MultilingualSystem(cognitive_core=core)
        out = ms.adapt_for_user('Привет', 'en')
        self.assertEqual(out, 'Hello')


class TestMultilingualUtils(unittest.TestCase):
    def test_supported_languages(self):
        ms = MultilingualSystem()
        langs = ms.supported_languages()
        self.assertIsInstance(langs, dict)
        self.assertEqual(len(langs), 15)

    def test_cache_size_and_clear(self):
        ms = MultilingualSystem()
        self.assertEqual(ms.cache_size(), 0)
        ms._cache['key'] = 'val'
        self.assertEqual(ms.cache_size(), 1)
        ms.clear_cache()
        self.assertEqual(ms.cache_size(), 0)

    def test_normalize_no_core(self):
        ms = MultilingualSystem()
        out = ms.normalize_terminology('test', 'ML', 'en')
        self.assertEqual(out, 'test')

    def test_normalize_with_core(self):
        core = MagicMock()
        core.reasoning.return_value = 'machine learning'
        ms = MultilingualSystem(cognitive_core=core)
        out = ms.normalize_terminology('ML', 'ai', 'en')
        self.assertEqual(out, 'machine learning')

    def test_summarize_no_core(self):
        ms = MultilingualSystem()
        out = ms.summarize_multilingual('long text here ' * 100, target_lang='ru')
        self.assertTrue(len(out) <= 300)

    def test_summarize_with_core(self):
        core = MagicMock()
        core.reasoning.return_value = 'Краткое резюме.'
        ms = MultilingualSystem(cognitive_core=core)
        out = ms.summarize_multilingual('long text', target_lang='ru')
        self.assertEqual(out, 'Краткое резюме.')


# ═══════════════════════════════════════════════════════════════════════════
# 3. software_dev/software_dev.py
# ═══════════════════════════════════════════════════════════════════════════
from software_dev.software_dev import (
    CodeAnalysisResult, TestSuite, BuildResult, SoftwareDevelopmentSystem,
)


class TestCodeAnalysisResult(unittest.TestCase):
    def test_create(self):
        r = CodeAnalysisResult('test.py')
        self.assertEqual(r.path, 'test.py')
        self.assertEqual(r.lines_total, 0)
        self.assertEqual(r.functions, [])

    def test_to_dict(self):
        r = CodeAnalysisResult('x.py')
        r.functions = ['foo', 'bar']
        d = r.to_dict()
        self.assertEqual(d['path'], 'x.py')
        self.assertEqual(d['functions'], ['foo', 'bar'])


class TestTestSuite(unittest.TestCase):
    def test_create(self):
        ts = TestSuite('src.py', 'import pytest', 'pytest')
        self.assertEqual(ts.source_path, 'src.py')
        self.assertEqual(ts.code, 'import pytest')

    def test_save(self):
        ts = TestSuite('src.py', '# test\ndef test_one(): pass\n')
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, 'test_out.py')
            ok = ts.save(path)
            self.assertTrue(ok)
            self.assertTrue(os.path.exists(path))

    def test_save_bad_path(self):
        ts = TestSuite('x.py', 'code')
        # Path with reserved characters (Windows)
        try:
            ok = ts.save('Z:\\nonexistent_drive_xyz_999\\sub\\path.py')
            self.assertFalse(ok)
        except (OSError, ValueError):
            pass  # acceptable: error raised instead of False


class TestBuildResult(unittest.TestCase):
    def test_create(self):
        br = BuildResult('pytest', True, 'all passed', 1.5, 0)
        self.assertTrue(br.success)
        self.assertEqual(br.returncode, 0)

    def test_to_dict(self):
        br = BuildResult('cmd', False, 'x' * 5000, 2.0, 1)
        d = br.to_dict()
        self.assertEqual(d['success'], False)
        self.assertLessEqual(len(d['output']), 2000)


class TestSoftwareDevAnalyze(unittest.TestCase):
    def test_analyze_python_file(self):
        code = (
            'import os\n\n'
            'class MyClass:\n'
            '    pass\n\n'
            'def my_func():\n'
            '    return 42\n\n'
            'def another():\n'
            '    # TODO: fix this\n'
            '    pass\n'
        )
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False,
                                          encoding='utf-8') as f:
            f.write(code)
            path = f.name
        try:
            sds = SoftwareDevelopmentSystem()
            r = sds.analyze(path)
            self.assertIsInstance(r, CodeAnalysisResult)
            self.assertIn('MyClass', r.classes)
            self.assertIn('my_func', r.functions)
            self.assertIn('another', r.functions)
            self.assertIn('os', r.imports)
            self.assertGreater(r.lines_total, 0)
            self.assertGreater(r.lines_code, 0)
            # Should detect TODO marker
            todo_issues = [i for i in r.issues if 'TODO' in i.get('message', '')]
            self.assertGreater(len(todo_issues), 0)
        finally:
            os.unlink(path)

    def test_analyze_syntax_error_file(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False,
                                          encoding='utf-8') as f:
            f.write('def broken(:\n    pass\n')
            path = f.name
        try:
            sds = SoftwareDevelopmentSystem()
            r = sds.analyze(path)
            errors = [i for i in r.issues if i['severity'] == 'error']
            self.assertGreater(len(errors), 0)
        finally:
            os.unlink(path)

    def test_analyze_nonexistent(self):
        sds = SoftwareDevelopmentSystem()
        r = sds.analyze('/nonexistent/file.py')
        errors = [i for i in r.issues if i['severity'] == 'error']
        self.assertGreater(len(errors), 0)

    def test_analyze_directory(self):
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, 'a.py'), 'w', encoding='utf-8') as f:
                f.write('def foo(): pass\n')
            with open(os.path.join(td, 'b.py'), 'w', encoding='utf-8') as f:
                f.write('class Bar: pass\n')
            sds = SoftwareDevelopmentSystem()
            r = sds.analyze(td)
            self.assertIn('foo', r.functions)
            self.assertIn('Bar', r.classes)

    def test_build_history_empty(self):
        sds = SoftwareDevelopmentSystem()
        h = sds.build_history()
        self.assertEqual(h, [])


# ═══════════════════════════════════════════════════════════════════════════
# 4. skills/portfolio_builder.py
# ═══════════════════════════════════════════════════════════════════════════
from skills.portfolio_builder import PortfolioBuilder, ANDREY_PORTFOLIO_PROJECTS


class TestPortfolioProjects(unittest.TestCase):
    def test_projects_exist(self):
        self.assertEqual(len(ANDREY_PORTFOLIO_PROJECTS), 6)

    def test_project_structure(self):
        for p in ANDREY_PORTFOLIO_PROJECTS:
            self.assertIn('title', p)
            self.assertIn('role', p)
            self.assertIn('description', p)
            self.assertIn('skills', p)
            self.assertIsInstance(p['skills'], list)

    def test_descriptions_within_upwork_limit(self):
        for p in ANDREY_PORTFOLIO_PROJECTS:
            self.assertLessEqual(len(p['description']), 700)


class TestPortfolioBuilder(unittest.TestCase):
    def setUp(self):
        self.pb = PortfolioBuilder()

    def test_get_project_valid(self):
        p = self.pb.get_project(0)
        self.assertIn('title', p)

    def test_get_project_invalid(self):
        p = self.pb.get_project(100)
        self.assertEqual(p, {})

    def test_get_project_negative(self):
        p = self.pb.get_project(-1)
        self.assertEqual(p, {})

    def test_format_for_telegram(self):
        p = ANDREY_PORTFOLIO_PROJECTS[0]
        text = self.pb.format_for_telegram(p, 0)
        self.assertIn('<b>', text)
        self.assertIn(p['title'], text)
        self.assertIn('1/6', text)

    def test_format_for_chat(self):
        p = ANDREY_PORTFOLIO_PROJECTS[1]
        text = self.pb.format_for_chat(p, 1)
        self.assertIn('**', text)
        self.assertIn(p['title'], text)
        self.assertIn('2/6', text)

    def test_send_all_no_bot(self):
        count = self.pb.send_all_to_telegram()
        self.assertEqual(count, 0)

    def test_send_one_no_bot(self):
        ok = self.pb.send_one_to_telegram(0)
        self.assertFalse(ok)

    def test_send_all_with_bot(self):
        bot = MagicMock()
        pb = PortfolioBuilder(telegram_bot=bot, telegram_chat_id=123)
        count = pb.send_all_to_telegram()
        self.assertEqual(count, 6)
        self.assertEqual(bot.send.call_count, 6)

    def test_send_one_with_bot(self):
        bot = MagicMock()
        pb = PortfolioBuilder(telegram_bot=bot, telegram_chat_id=123)
        ok = pb.send_one_to_telegram(0)
        self.assertTrue(ok)
        bot.send.assert_called_once()

    def test_send_one_bad_index(self):
        bot = MagicMock()
        pb = PortfolioBuilder(telegram_bot=bot, telegram_chat_id=123)
        ok = pb.send_one_to_telegram(999)
        self.assertFalse(ok)

    def test_send_all_with_error(self):
        bot = MagicMock()
        bot.send.side_effect = RuntimeError('network')
        pb = PortfolioBuilder(telegram_bot=bot, telegram_chat_id=123)
        count = pb.send_all_to_telegram()
        self.assertEqual(count, 0)

    def test_format_long_description(self):
        project = {
            'title': 'Test',
            'role': 'Dev',
            'description': 'A' * 700,
            'skills': ['Python'],
        }
        text = self.pb.format_for_telegram(project, 0)
        self.assertIn('...', text)


if __name__ == '__main__':
    unittest.main()
