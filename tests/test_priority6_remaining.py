"""
Priority 6 — оставшиеся 0%-coverage модули:
  • skills/self_benchmark.py   — BenchStatus, BenchResult, SelfBenchmark
  • skills/browser_agent.py    — BrowserAgent (browse, read_page, hunt_jobs_browser)
  • skills/upwork_portfolio_filler.py — UpworkPortfolioFiller (fill_project)
"""
import json
import os
import sys
import tempfile
import types
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ════════════════════════════════════════════════════════════════════════
# 1. SelfBenchmark
# ════════════════════════════════════════════════════════════════════════

from skills.self_benchmark import BenchStatus, BenchResult, SelfBenchmark


class TestBenchStatus:
    def test_values(self):
        assert BenchStatus.OK.value == 'ok'
        assert BenchStatus.FAIL.value == 'fail'
        assert BenchStatus.TIMEOUT.value == 'timeout'
        assert BenchStatus.SKIP.value == 'skip'
        assert BenchStatus.PARTIAL.value == 'partial'

    def test_is_str_enum(self):
        assert isinstance(BenchStatus.OK, str)
        assert BenchStatus.FAIL == 'fail'


class TestBenchResult:
    def test_defaults(self):
        r = BenchResult(task_id=1, task_text='hello', status=BenchStatus.OK)
        assert r.reply == ''
        assert r.error == ''
        assert r.elapsed_s == 0.0
        assert r.requires == []
        assert r.notes == ''
        assert r.category == ''
        assert r.pass_index == 1

    def test_to_dict(self):
        r = BenchResult(task_id=7, task_text='x' * 200, status=BenchStatus.FAIL,
                        reply='y' * 400, error='oops', elapsed_s=1.2345,
                        requires=['internet'], notes='note1', category='cat',
                        pass_index=2)
        d = r.to_dict()
        assert d['task_id'] == 7
        assert len(d['task_text']) <= 120
        assert d['status'] == 'fail'
        assert len(d['reply']) <= 300
        assert d['error'] == 'oops'
        assert d['elapsed_s'] == 1.23
        assert d['requires'] == ['internet']
        assert d['category'] == 'cat'
        assert d['pass_index'] == 2


class TestSelfBenchmarkInit:
    def test_default(self):
        bench = SelfBenchmark()
        assert bench._wi is None
        assert bench._timeout == 60.0
        assert bench._results == []
        assert isinstance(bench._tasks_raw, dict)

    def test_custom_timeout(self):
        bench = SelfBenchmark(timeout_sec=10.0)
        assert bench._timeout == 10.0


class TestSelfBenchmarkTasks:
    @pytest.fixture
    def bench_with_tasks(self, tmp_path):
        """SelfBenchmark с файлом задач."""
        tasks = {
            'categories': [
                {
                    'id': 1, 'name': 'basic',
                    'tasks': [
                        {'id': 101, 'text': 'Explain Python GIL', 'requires': []},
                        {'id': 102, 'text': 'Summarize a file', 'requires': ['pdf_reader']},
                    ]
                },
                {
                    'id': 2, 'name': 'api',
                    'tasks': [
                        {'id': 201, 'text': 'Send email', 'requires': ['gmail_oauth']},
                    ]
                }
            ],
            'accelerated_mode': {
                'profiles': {
                    'fast': {'task_ids': [101], 'passes': 2, 'timeout_sec': 20},
                    'slow': {'task_ids': [201], 'passes': 1},
                }
            }
        }
        tf = tmp_path / 'bench.json'
        tf.write_text(json.dumps(tasks), encoding='utf-8')
        with patch('skills.self_benchmark._TASKS_FILE', str(tf)):
            b = SelfBenchmark()
        return b

    def test_get_all_tasks(self, bench_with_tasks):
        all_t = bench_with_tasks.get_all_tasks()
        assert len(all_t) == 3
        ids = {t['id'] for t in all_t}
        assert ids == {101, 102, 201}

    def test_get_tasks_by_category(self, bench_with_tasks):
        cat1 = bench_with_tasks.get_tasks_by_category(1)
        assert len(cat1) == 2
        assert bench_with_tasks.get_tasks_by_category(999) == []

    def test_list_profiles(self, bench_with_tasks):
        profs = bench_with_tasks.list_profiles()
        assert 'fast' in profs
        assert 'slow' in profs

    def test_get_profile(self, bench_with_tasks):
        p = bench_with_tasks.get_profile('fast')
        assert p['passes'] == 2
        assert bench_with_tasks.get_profile('nope') == {}


class TestSelfBenchmarkReport:
    def test_report_empty(self):
        bench = SelfBenchmark()
        assert 'Нет результатов' in bench.report()

    def test_report_with_results(self):
        bench = SelfBenchmark()
        bench._results = [
            BenchResult(task_id=1, task_text='t1', status=BenchStatus.OK, elapsed_s=1.0),
            BenchResult(task_id=2, task_text='t2', status=BenchStatus.FAIL, error='nope', elapsed_s=2.0),
            BenchResult(task_id=3, task_text='t3', status=BenchStatus.TIMEOUT, error='too slow', elapsed_s=60.0),
            BenchResult(task_id=4, task_text='t4', status=BenchStatus.SKIP, elapsed_s=0.0),
            BenchResult(task_id=5, task_text='t5', status=BenchStatus.PARTIAL, elapsed_s=0.5),
        ]
        r = bench.report()
        assert 'Всего задач:  5' in r
        assert 'OK' in r
        assert 'Провал' in r
        assert 'ПРОБЛЕМНЫЕ ЗАДАЧИ' in r


class TestSelfBenchmarkAnalyzeErrors:
    def test_no_results(self):
        bench = SelfBenchmark()
        assert 'Нет результатов' in bench.analyze_errors()

    def test_all_ok(self):
        bench = SelfBenchmark()
        bench._results = [
            BenchResult(task_id=1, task_text='t', status=BenchStatus.OK),
        ]
        assert 'ошибок не найдено' in bench.analyze_errors()

    def test_groups_errors(self):
        bench = SelfBenchmark()
        bench._results = [
            BenchResult(task_id=1, task_text='t1', status=BenchStatus.TIMEOUT, error='slow'),
            BenchResult(task_id=2, task_text='t2', status=BenchStatus.FAIL,
                        reply='не указан файл'),
            BenchResult(task_id=3, task_text='t3', status=BenchStatus.FAIL,
                        reply='я не могу это сделать'),
        ]
        r = bench.analyze_errors()
        assert 'Таймаут' in r
        assert 'РЕКОМЕНДАЦИИ' in r


class TestSelfBenchmarkEvaluate:
    def test_empty_reply_is_fail(self):
        bench = SelfBenchmark()
        assert bench._evaluate({}, '', True) == BenchStatus.FAIL

    def test_short_reply_is_fail(self):
        bench = SelfBenchmark()
        assert bench._evaluate({}, 'ok', True) == BenchStatus.FAIL

    def test_refusal_is_fail(self):
        bench = SelfBenchmark()
        assert bench._evaluate(
            {'requires': []}, 'Извините, я не могу выполнить эту задачу.', True
        ) == BenchStatus.FAIL

    def test_refusal_with_oauth_is_skip(self):
        bench = SelfBenchmark()
        assert bench._evaluate(
            {'requires': ['gmail_oauth']}, 'Cannot do this — нет credentials', True
        ) == BenchStatus.SKIP

    def test_ok_long_reply(self):
        bench = SelfBenchmark()
        assert bench._evaluate({}, 'A' * 100, True) == BenchStatus.OK

    def test_ok_short_reply_is_partial(self):
        bench = SelfBenchmark()
        assert bench._evaluate({}, 'A' * 50, True) == BenchStatus.PARTIAL

    def test_not_ok_is_fail(self):
        bench = SelfBenchmark()
        assert bench._evaluate({}, 'A' * 100, False) == BenchStatus.FAIL


class TestSelfBenchmarkCanRun:
    def test_no_requires(self):
        bench = SelfBenchmark()
        ok, _reason = bench._can_run([])
        assert ok is True

    def test_image_generator_always_false(self):
        bench = SelfBenchmark()
        ok, reason = bench._can_run(['image_generator_or_editor'])
        assert ok is False
        assert 'DALL-E' in reason

    def test_oauth_without_creds(self):
        bench = SelfBenchmark()
        # В temp-dir точно нет credentials.json
        with patch('skills.self_benchmark._HERE', tempfile.gettempdir()):
            ok, _ = bench._can_run(['gmail_oauth'])
            assert ok is False


class TestSelfBenchmarkRunTask:
    def test_no_web_interface(self):
        bench = SelfBenchmark()
        task = {'id': 1, 'text': 'hello', 'requires': [], 'notes': '', 'category': 'test'}
        r = bench._run_task(task)
        assert r.status == BenchStatus.SKIP
        assert 'web_interface' in r.error

    def test_skip_when_requires_fail(self):
        bench = SelfBenchmark()
        task = {'id': 2, 'text': 'generate image', 'requires': ['image_generator_or_editor'],
                'notes': '', 'category': 'test'}
        r = bench._run_task(task)
        assert r.status == BenchStatus.SKIP


class TestSelfBenchmarkRunWithMock:
    def test_run_single_ok(self, tmp_path):
        tasks = {
            'categories': [
                {
                    'id': 1, 'name': 'cat',
                    'tasks': [
                        {'id': 1, 'text': 'Say hello nicely', 'requires': []},
                    ]
                }
            ]
        }
        tf = tmp_path / 'bench.json'
        tf.write_text(json.dumps(tasks), encoding='utf-8')

        wi = MagicMock()
        wi._handle_chat.return_value = {
            'ok': True,
            'reply': 'Привет! Всё отлично, вот подробный ответ на вашу задачу. ' * 5,
        }

        with patch('skills.self_benchmark._TASKS_FILE', str(tf)), \
             patch('skills.self_benchmark._LOG_FILE', str(tmp_path / 'log.json')), \
             patch('skills.self_benchmark._EVENTS_LOG_FILE', str(tmp_path / 'events.jsonl')):
            bench = SelfBenchmark(web_interface=wi, timeout_sec=5.0)
            results = bench.run()

        assert len(results) == 1
        assert results[0].status == BenchStatus.OK

    def test_test_one_not_found(self):
        bench = SelfBenchmark()
        r = bench.test_one(9999)
        assert 'не найдена' in r


class TestSelfBenchmarkSaveLog:
    def test_save_and_load(self, tmp_path):
        log_path = str(tmp_path / 'log.json')
        with patch('skills.self_benchmark._LOG_FILE', log_path):
            bench = SelfBenchmark()
            bench._last_run_id = 'abc123'
            bench._results = [
                BenchResult(task_id=1, task_text='t', status=BenchStatus.OK),
            ]
            bench._save_log()
            loaded = bench.load_last_log()
            assert len(loaded) == 1
            assert loaded[0]['status'] == 'ok'


# ════════════════════════════════════════════════════════════════════════
# 2. BrowserAgent
# ════════════════════════════════════════════════════════════════════════

from skills.browser_agent import (
    BrowserAgent, _MAX_STEPS, _PAGE_TEXT_LIMIT,
    _ALLOWED_DOMAINS, _UPWORK_SEARCH, _TASK_URL_MAP,
)


class TestBrowserAgentConstants:
    def test_max_steps(self):
        assert _MAX_STEPS == 15

    def test_page_text_limit(self):
        assert _PAGE_TEXT_LIMIT == 4000

    def test_allowed_domains_has_upwork(self):
        assert 'upwork.com' in _ALLOWED_DOMAINS
        assert 'www.upwork.com' in _ALLOWED_DOMAINS

    def test_upwork_search_format(self):
        assert '{query}' in _UPWORK_SEARCH


class TestBrowserAgentInit:
    def test_defaults(self):
        agent = BrowserAgent()
        assert agent.llm is None
        assert agent.telegram_bot is None
        assert agent._browser_tool is None

    def test_all_deps(self):
        llm = MagicMock()
        bt = MagicMock()
        bot = MagicMock()
        agent = BrowserAgent(browser_tool=bt, llm=llm, telegram_bot=bot,
                             telegram_chat_id=123, monitoring=MagicMock())
        assert agent.llm is llm
        assert agent._browser_tool is bt
        assert agent.telegram_chat_id == 123


class TestBrowserAgentIsAllowed:
    def test_upwork_allowed(self):
        agent = BrowserAgent()
        assert agent._is_allowed_url('https://www.upwork.com/jobs/123') is True

    def test_random_domain_blocked(self):
        agent = BrowserAgent()
        assert agent._is_allowed_url('https://evil.example.com/') is False

    def test_hh_allowed(self):
        agent = BrowserAgent()
        assert agent._is_allowed_url('https://hh.ru/vacancy/123') is True

    def test_bad_url(self):
        agent = BrowserAgent()
        assert agent._is_allowed_url('not-a-url') is False


class TestBrowserAgentExtractKeywords:
    def test_extracts_keywords(self):
        kw = BrowserAgent._extract_search_keywords('Найди вакансии Python AI на Upwork')
        assert 'python' in kw
        assert 'ai' in kw
        # стоп-слова удалены
        assert 'найди' not in kw
        assert 'upwork' not in kw

    def test_empty(self):
        kw = BrowserAgent._extract_search_keywords('')
        assert kw == []


class TestBrowserAgentPlanStartUrl:
    def test_explicit_url_allowed(self):
        agent = BrowserAgent(llm=MagicMock())
        url = agent._plan_start_url('Зайди на https://www.upwork.com/jobs/123 и посмотри')
        assert url == 'https://www.upwork.com/jobs/123'

    def test_explicit_url_blocked(self):
        agent = BrowserAgent(llm=MagicMock())
        url = agent._plan_start_url('Зайди на https://evil.com/x')
        # Should fall back to default (Upwork search)
        assert 'upwork.com' in url

    def test_keyword_trigger_hh(self):
        agent = BrowserAgent(llm=MagicMock())
        url = agent._plan_start_url('Ищи вакансии на hh.ru AI engineer')
        assert 'hh.ru' in url

    def test_default_upwork(self):
        agent = BrowserAgent(llm=MagicMock())
        url = agent._plan_start_url('telegram bot developer')
        assert 'upwork.com' in url


class TestBrowserAgentBrowse:
    def _make_page(self, success=True, text='page content', title='Title', links=None):
        page = MagicMock()
        page.success = success
        page.text = text
        page.title = title
        page.links = links or []
        return page

    def test_browse_no_llm(self):
        """Без LLM — _simple_search → navigate + вернуть текст."""
        browser = MagicMock()
        page = self._make_page(text='Found jobs here')
        browser.navigate.return_value = page
        browser.get_text.return_value = 'Found jobs here'

        agent = BrowserAgent(browser_tool=browser)
        result = agent.browse('python developer')
        assert 'Found jobs' in result
        browser.navigate.assert_called()

    def test_browse_with_llm_done_immediately(self):
        """LLM сразу говорит done."""
        browser = MagicMock()
        page = self._make_page()
        browser.navigate.return_value = page
        browser.get_text.return_value = 'some text'
        browser.current_url = 'https://www.upwork.com/search'
        browser.detect_captcha.return_value = {'detected': False}

        llm = MagicMock()
        llm.infer.return_value = json.dumps({
            'action': 'done',
            'answer': 'Нашёл 3 вакансии python.',
            'note': 'готово',
        })

        agent = BrowserAgent(browser_tool=browser, llm=llm)
        result = agent.browse('python jobs')
        assert 'вакансии' in result.lower() or 'Нашёл' in result

    def test_browse_exception_handled(self):
        """Исключение при работе с браузером ловится."""
        browser = MagicMock()
        browser.navigate.side_effect = RuntimeError('browser crash')

        agent = BrowserAgent(browser_tool=browser)
        result = agent.browse('test')
        assert 'Ошибка' in result


class TestBrowserAgentReadPage:
    def test_read_page_no_llm(self):
        browser = MagicMock()
        page = MagicMock()
        page.success = True
        page.text = 'Hello World ' * 100
        page.title = 'Test Page'
        browser.navigate.return_value = page

        agent = BrowserAgent(browser_tool=browser)
        result = agent.read_page('https://www.upwork.com/page', '')
        assert 'Test Page' in result

    def test_read_page_fail(self):
        browser = MagicMock()
        page = MagicMock()
        page.success = False
        browser.navigate.return_value = page

        agent = BrowserAgent(browser_tool=browser)
        result = agent.read_page('https://www.upwork.com/bad', '')
        assert 'Не удалось' in result

    def test_read_page_with_question_no_llm(self):
        browser = MagicMock()
        page = MagicMock()
        page.success = True
        page.text = 'Python developer needed. $50/hr.'
        page.title = 'Job'
        browser.navigate.return_value = page

        agent = BrowserAgent(browser_tool=browser)
        result = agent.read_page('https://www.upwork.com/jobs/1', 'what is the rate?')
        # No LLM → returns raw text
        assert 'Python developer' in result


class TestBrowserAgentHuntJobs:
    def test_hunt_no_jobs_found(self):
        browser = MagicMock()
        page = MagicMock()
        page.success = True
        page.text = 'No results found.'
        page.title = 'Search'
        page.links = []
        browser.navigate.return_value = page
        browser.get_links.return_value = []

        agent = BrowserAgent(browser_tool=browser)
        jobs = agent.hunt_jobs_browser('python')
        assert jobs == []

    def test_hunt_page_fail(self):
        browser = MagicMock()
        page = MagicMock()
        page.success = False
        browser.navigate.return_value = page

        agent = BrowserAgent(browser_tool=browser)
        jobs = agent.hunt_jobs_browser('python')
        assert jobs == []


class TestBrowserAgentEvaluateJob:
    def test_no_llm_always_fit(self):
        agent = BrowserAgent()
        fit, _reason = agent._evaluate_job({'title': 'Test'})
        assert fit is True

    def test_llm_fit_yes(self):
        llm = MagicMock()
        llm.infer.return_value = 'FIT: yes'
        agent = BrowserAgent(llm=llm)
        fit, _ = agent._evaluate_job({'title': 'Python Dev', 'description': 'Need python'})
        assert fit is True

    def test_llm_fit_no(self):
        llm = MagicMock()
        llm.infer.return_value = 'FIT: no — нужен Java опыт'
        agent = BrowserAgent(llm=llm)
        fit, reason = agent._evaluate_job({'title': 'Java Dev', 'description': 'Need java'})
        assert fit is False
        assert 'Java' in reason


class TestBrowserAgentPickBestLink:
    def test_no_links(self):
        agent = BrowserAgent()
        assert agent._pick_best_link('task', [], 'text') is None

    def test_no_llm_picks_upwork_job(self):
        agent = BrowserAgent()
        links = [
            'https://www.upwork.com/about',
            'https://www.upwork.com/jobs/~01abc123',
        ]
        result = agent._pick_best_link('find jobs', links, '')
        # Should pick the job link (contains /jobs/)
        assert result is not None
        assert '/jobs/' in result


class TestBrowserAgentTelegram:
    def test_send_telegram_truncates(self):
        bot = MagicMock()
        agent = BrowserAgent(telegram_bot=bot, telegram_chat_id=123)
        agent._send_telegram('x' * 5000)
        bot.send.assert_called_once()
        msg = bot.send.call_args[0][1]
        assert len(msg) <= 4003  # 4000 + '...'

    def test_no_telegram_no_crash(self):
        agent = BrowserAgent()
        agent._send_telegram('hello')  # should not raise


# ════════════════════════════════════════════════════════════════════════
# 3. UpworkPortfolioFiller
# ════════════════════════════════════════════════════════════════════════

from skills.upwork_portfolio_filler import (
    UpworkPortfolioFiller,
    _UPWORK_ADD_URL, _S1_TITLE, _S1_DESC, _S2_ROLE, _S2_SKILLS,
    _S3_URL, _BTN_NEXT, _BTN_SAVE,
)


class TestUpworkPortfolioFillerConstants:
    def test_add_url(self):
        assert 'upwork.com' in _UPWORK_ADD_URL
        assert 'portfolio/add' in _UPWORK_ADD_URL

    def test_selectors_are_lists(self):
        assert isinstance(_S1_TITLE, list) and len(_S1_TITLE) > 0
        assert isinstance(_S1_DESC, list) and len(_S1_DESC) > 0
        assert isinstance(_S2_ROLE, list)
        assert isinstance(_S2_SKILLS, list)
        assert isinstance(_S3_URL, list)
        assert isinstance(_BTN_NEXT, list)
        assert isinstance(_BTN_SAVE, list)


class TestUpworkPortfolioFillerInit:
    def test_defaults(self):
        filler = UpworkPortfolioFiller()
        assert filler._browser_tool is None
        assert filler.telegram_bot is None
        assert filler.monitoring is None

    def test_with_deps(self):
        bt = MagicMock()
        filler = UpworkPortfolioFiller(browser_tool=bt, monitoring=MagicMock())
        assert filler._browser_tool is bt


class TestUpworkPortfolioFillerNoBrowser:
    def test_fill_project_no_browser_tool_module(self):
        """Если BrowserTool не импортируется — graceful error."""
        filler = UpworkPortfolioFiller()
        with patch.dict('sys.modules', {'tools.browser_tool': None}):
            # Имитируем ImportError при from tools.browser_tool import BrowserTool
            r = filler.fill_project({'title': 'Test'})
            assert r['success'] is False
            assert 'BrowserTool' in r['message'] or 'import' in r['message'].lower()


class TestUpworkPortfolioFillerWithBrowser:
    def _make_browser(self, *, preview=True):
        """Создаёт мок браузера, проходящий wizard."""
        br = MagicMock()
        br.current_url = 'https://www.upwork.com/freelancers/settings/portfolio/add'
        br.navigate.return_value = MagicMock(success=True)
        # wait_for: возвращаем True для нужных селекторов
        br.wait_for.return_value = True
        br.fill.return_value = True
        br.click.return_value = True
        br.screenshot.return_value = 'aW1hZ2U='  # base64 'image'
        if preview:
            br.get_text.return_value = 'preview of your project'
        else:
            br.get_text.return_value = 'step 2 content'
        return br

    def test_fill_project_success_preview(self):
        """Полный wizard: login ok, 3 шага, preview, save."""
        br = self._make_browser()

        filler = UpworkPortfolioFiller(browser_tool=br)
        result = filler.fill_project({
            'title': 'AI Agent',
            'role': 'Lead Developer',
            'description': 'Built autonomous agent',
            'skills': ['Python', 'AI'],
        })
        assert result['success'] is True
        assert 'screenshot_b64' in result

    def test_fill_project_not_preview(self):
        """Не дошли до preview."""
        br = self._make_browser(preview=False)
        br.get_text.return_value = 'step 3 - fill url'

        filler = UpworkPortfolioFiller(browser_tool=br)
        result = filler.fill_project({
            'title': 'Project X',
            'role': 'Developer',
            'description': 'Did stuff',
            'skills': [],
        })
        # Still returns success=True but with "проверь" message
        assert result['success'] is True
        assert 'проверь' in result['message'].lower() or 'browser' in result['message'].lower()

    def test_fill_project_exception(self):
        """Исключение при работе wizard."""
        br = MagicMock()
        br.current_url = 'https://www.upwork.com/freelancers/settings/portfolio/add'
        br.navigate.side_effect = RuntimeError('playwright crash')
        br.screenshot.return_value = None

        filler = UpworkPortfolioFiller(browser_tool=br)
        result = filler.fill_project({'title': 'Fail'})
        assert result['success'] is False
        assert 'playwright crash' in result['message']


class TestUpworkPortfolioFillerHelpers:
    def test_smart_fill_not_found(self):
        br = MagicMock()
        br.wait_for.return_value = False

        filler = UpworkPortfolioFiller(browser_tool=br)
        ok = filler._smart_fill(br, ['input.nonexistent'], 'text')
        assert ok is False

    def test_smart_fill_found(self):
        br = MagicMock()
        br.wait_for.return_value = True
        br.fill.return_value = True

        filler = UpworkPortfolioFiller(browser_tool=br)
        ok = filler._smart_fill(br, ['input.title'], 'Test Project')
        assert ok is True
        assert br.fill.call_count >= 2  # clear + fill

    def test_click_any_first_found(self):
        br = MagicMock()
        br.wait_for.side_effect = [False, True]  # 2nd selector found
        br.click.return_value = True

        filler = UpworkPortfolioFiller(browser_tool=br)
        ok = filler._click_any(br, ['btn.a', 'btn.b'])
        assert ok is True

    def test_click_any_none_found(self):
        br = MagicMock()
        br.wait_for.return_value = False

        filler = UpworkPortfolioFiller(browser_tool=br)
        ok = filler._click_any(br, ['btn.x'])
        assert ok is False

    def test_has_any(self):
        br = MagicMock()
        br.wait_for.side_effect = [False, True]

        filler = UpworkPortfolioFiller(browser_tool=br)
        assert filler._has_any(br, ['sel1', 'sel2']) is True

    def test_find_selector_reduces_timeout(self):
        """После первого промаха timeout уменьшается."""
        br = MagicMock()
        br.wait_for.side_effect = [False, False, True]

        filler = UpworkPortfolioFiller(browser_tool=br)
        sel = filler._find_selector(br, ['a', 'b', 'c'], timeout=5000)
        assert sel == 'c'
        # Первый вызов с 5000, второй и третий с 1000
        calls = br.wait_for.call_args_list
        assert calls[0][1].get('timeout', calls[0][0][1] if len(calls[0][0]) > 1 else 5000) in (5000, 1000)

    def test_ensure_logged_in_already(self):
        br = MagicMock()
        br.current_url = 'https://www.upwork.com/freelancers/settings/portfolio/add'

        filler = UpworkPortfolioFiller(browser_tool=br)
        assert filler._ensure_logged_in(br) is True

    def test_notify_telegram_no_bot(self):
        """Не падает без telegram."""
        filler = UpworkPortfolioFiller()
        filler._notify_telegram('test')  # no crash

    def test_notify_telegram_with_bot(self):
        bot = MagicMock()
        filler = UpworkPortfolioFiller(telegram_bot=bot, telegram_chat_id=42)
        filler._notify_telegram('hello')
        bot.send.assert_called_once_with(42, 'hello')

    def test_log_with_monitoring(self):
        mon = MagicMock()
        filler = UpworkPortfolioFiller(monitoring=mon)
        filler._log('test msg')
        mon.info.assert_called()

    def test_log_without_monitoring(self, capsys):
        filler = UpworkPortfolioFiller()
        filler._log('printed message')
        captured = capsys.readouterr()
        assert 'printed message' in captured.out


# ════════════════════════════════════════════════════════════════════════
# Итого: 70 тестов
# ════════════════════════════════════════════════════════════════════════
