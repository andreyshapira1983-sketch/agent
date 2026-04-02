# Покрытие: tools/browser_tool.py
import os
import sys
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tools.browser_tool import BrowserPage, BrowserTool


class TestBrowserPage(unittest.TestCase):

    def test_init_defaults(self):
        p = BrowserPage('http://x', 'Title', 'text', '<html/>')
        self.assertEqual(p.url, 'http://x')
        self.assertEqual(p.status, 200)
        self.assertTrue(p.success)
        self.assertEqual(p.links, [])
        self.assertIsNone(p.screenshot_b64)

    def test_init_with_args(self):
        p = BrowserPage('http://x', 'T', 'txt', '<h>', screenshot_b64='abc',
                         links=['http://link'], status=404, success=False,
                         error='not found')
        self.assertEqual(p.status, 404)
        self.assertFalse(p.success)
        self.assertEqual(p.error, 'not found')

    def test_to_dict(self):
        p = BrowserPage('http://x', 'Title', 'a' * 600, '<html/>',
                         links=['http://1', 'http://2'])
        d = p.to_dict()
        self.assertEqual(d['url'], 'http://x')
        self.assertEqual(d['links_count'], 2)
        self.assertLessEqual(len(d['text_preview']), 500)
        self.assertIn('success', d)


class TestBrowserToolInit(unittest.TestCase):

    def test_defaults(self):
        bt = BrowserTool()
        self.assertEqual(bt.name, 'browser')
        self.assertTrue(bt.headless)
        self.assertEqual(bt.timeout, 30000)
        self.assertIsNone(bt._page)
        self.assertEqual(bt._backend, 'none')

    def test_custom_args(self):
        wc = MagicMock()
        bt = BrowserTool(headless=False, timeout=5000, web_crawler=wc)
        self.assertFalse(bt.headless)
        self.assertEqual(bt.timeout, 5000)
        self.assertIs(bt.web_crawler, wc)


class TestBrowserToolNoPage(unittest.TestCase):
    """Все методы без инициализированной страницы."""

    def setUp(self):
        self.bt = BrowserTool()

    def test_click_no_page(self):
        self.assertFalse(self.bt.click('#btn'))

    def test_fill_no_page(self):
        self.assertFalse(self.bt.fill('#input', 'text'))

    def test_press_no_page(self):
        self.assertFalse(self.bt.press('#input', 'Enter'))

    def test_wait_for_no_page(self):
        self.assertFalse(self.bt.wait_for('#el'))

    def test_evaluate_no_page(self):
        self.assertIsNone(self.bt.evaluate('1+1'))

    def test_scroll_no_page(self):
        self.bt.scroll(0, 100)  # should not raise

    def test_get_text_no_page(self):
        self.assertEqual(self.bt.get_text(), '')

    def test_get_links_no_page(self):
        self.assertEqual(self.bt.get_links(), [])

    def test_get_element_text_no_page(self):
        self.assertEqual(self.bt.get_element_text('#el'), '')

    def test_get_attribute_no_page(self):
        self.assertEqual(self.bt.get_attribute('#el', 'href'), '')

    def test_screenshot_no_page(self):
        self.assertIsNone(self.bt.screenshot())

    def test_new_tab_no_context(self):
        self.assertFalse(self.bt.new_tab())

    def test_current_url_no_page(self):
        self.bt._current_url = 'http://saved'
        self.assertEqual(self.bt.current_url, 'http://saved')


class TestBrowserToolWithMockPage(unittest.TestCase):
    """Методы с замоканной страницей Playwright."""

    def setUp(self):
        self.bt = BrowserTool()
        self.page = MagicMock()
        self.bt._page = self.page
        self.bt._context = MagicMock()
        self.bt._browser = MagicMock()
        self.bt._playwright = MagicMock()
        self.bt._backend = 'playwright'

    def test_click_success(self):
        self.assertTrue(self.bt.click('#btn'))
        self.page.click.assert_called_once()

    def test_click_exception(self):
        self.page.click.side_effect = Exception('no element')
        self.assertFalse(self.bt.click('#btn'))

    def test_fill_success(self):
        self.assertTrue(self.bt.fill('#in', 'hello'))
        self.page.fill.assert_called_once_with('#in', 'hello')

    def test_fill_exception(self):
        self.page.fill.side_effect = Exception('err')
        self.assertFalse(self.bt.fill('#in', 'hello'))

    def test_press_success(self):
        self.assertTrue(self.bt.press('#in', 'Enter'))

    def test_press_exception(self):
        self.page.press.side_effect = Exception('err')
        self.assertFalse(self.bt.press('#in', 'Enter'))

    def test_wait_for_success(self):
        self.assertTrue(self.bt.wait_for('#el'))

    def test_wait_for_timeout(self):
        self.page.wait_for_selector.side_effect = Exception('timeout')
        self.assertFalse(self.bt.wait_for('#el'))

    def test_evaluate(self):
        self.page.evaluate.return_value = 42
        self.assertEqual(self.bt.evaluate('21*2'), 42)

    def test_evaluate_error(self):
        self.page.evaluate.side_effect = Exception('js error')
        self.assertIsNone(self.bt.evaluate('bad js'))

    def test_scroll(self):
        self.bt.scroll(0, 500)
        self.page.evaluate.assert_called_once_with('window.scrollBy(0, 500)')

    def test_get_text(self):
        self.page.evaluate.return_value = 'page text'
        self.assertEqual(self.bt.get_text(), 'page text')

    def test_get_links(self):
        self.page.evaluate.return_value = ['http://a', 'http://b']
        links = self.bt.get_links()
        self.assertEqual(links, ['http://a', 'http://b'])

    def test_get_element_text(self):
        loc = MagicMock()
        loc.first.inner_text.return_value = 'element text'
        self.page.locator.return_value = loc
        self.assertEqual(self.bt.get_element_text('#el'), 'element text')

    def test_get_element_text_error(self):
        self.page.locator.side_effect = Exception('not found')
        self.assertEqual(self.bt.get_element_text('#el'), '')

    def test_get_attribute(self):
        self.page.get_attribute.return_value = 'http://link'
        self.assertEqual(self.bt.get_attribute('a', 'href'), 'http://link')

    def test_get_attribute_none(self):
        self.page.get_attribute.return_value = None
        self.assertEqual(self.bt.get_attribute('a', 'href'), '')

    def test_get_attribute_error(self):
        self.page.get_attribute.side_effect = Exception('err')
        self.assertEqual(self.bt.get_attribute('a', 'href'), '')

    def test_screenshot_bytes(self):
        self.page.screenshot.return_value = b'\x89PNG'
        result = self.bt.screenshot()
        self.assertIsNotNone(result)
        self.assertIsInstance(result, str)

    def test_screenshot_with_path(self):
        self.page.screenshot.return_value = b'\x89PNG'
        import tempfile
        path = os.path.join(tempfile.gettempdir(), 'test_ss.png')
        result = self.bt.screenshot(path=path)
        self.assertIsNotNone(result)

    def test_screenshot_error(self):
        self.page.screenshot.side_effect = Exception('fail')
        self.assertIsNone(self.bt.screenshot())

    def test_new_tab(self):
        new_page = MagicMock()
        self.bt._context.new_page.return_value = new_page
        self.assertTrue(self.bt.new_tab())
        self.assertIs(self.bt._page, new_page)

    def test_new_tab_error(self):
        self.bt._context.new_page.side_effect = Exception('err')
        self.assertFalse(self.bt.new_tab())

    def test_current_url_from_page(self):
        type(self.page).url = PropertyMock(return_value='http://current')
        self.assertEqual(self.bt.current_url, 'http://current')

    def test_current_url_page_error(self):
        type(self.page).url = PropertyMock(side_effect=Exception('err'))
        self.bt._current_url = 'http://fallback'
        self.assertEqual(self.bt.current_url, 'http://fallback')

    def test_close(self):
        browser = self.bt._browser
        pw = self.bt._playwright
        self.bt.close()
        browser.close.assert_called_once()
        pw.stop.assert_called_once()
        self.assertIsNone(self.bt._page)
        self.assertIsNone(self.bt._browser)

    def test_close_exception_safe(self):
        self.bt._browser.close.side_effect = Exception('err')
        self.bt.close()  # should not raise
        self.assertIsNone(self.bt._page)


class TestBrowserToolNavigate(unittest.TestCase):

    def test_navigate_playwright(self):
        bt = BrowserTool()
        page = MagicMock()
        response = MagicMock()
        response.status = 200
        page.goto.return_value = response
        page.title.return_value = 'Test Page'
        page.evaluate.return_value = 'text content'
        page.content.return_value = '<html></html>'
        type(page).url = PropertyMock(return_value='http://test.com')
        bt._page = page
        bt._backend = 'playwright'

        result = bt.navigate('http://test.com')
        self.assertIsInstance(result, BrowserPage)
        self.assertTrue(result.success)
        self.assertEqual(result.title, 'Test Page')

    def test_navigate_error(self):
        bt = BrowserTool()
        page = MagicMock()
        page.goto.side_effect = Exception('network error')
        bt._page = page
        bt._backend = 'playwright'

        result = bt.navigate('http://fail.com')
        self.assertFalse(result.success)
        self.assertIn('network error', result.error)

    def test_navigate_fallback_crawler(self):
        wc = MagicMock()
        wc.fetch.return_value = {
            'url': 'http://via-crawler.com',
            'title': 'Crawled',
            'text': 'content',
            'links': ['http://link'],
            'status': 200,
            'success': True,
        }
        bt = BrowserTool(web_crawler=wc)
        bt._backend = 'crawler'
        with patch.object(bt, '_ensure_browser', return_value=False):
            result = bt.navigate('http://via-crawler.com')
        self.assertTrue(result.success)
        self.assertEqual(result.title, 'Crawled')

    def test_navigate_fallback_no_crawler(self):
        bt = BrowserTool()
        bt._backend = 'crawler'
        with patch.object(bt, '_ensure_browser', return_value=False):
            result = bt.navigate('http://no-crawler.com')
        self.assertFalse(result.success)
        self.assertIn('Playwright', result.error)

    def test_navigate_fallback_crawler_error(self):
        wc = MagicMock()
        wc.fetch.side_effect = Exception('crawl failed')
        bt = BrowserTool(web_crawler=wc)
        bt._backend = 'crawler'
        with patch.object(bt, '_ensure_browser', return_value=False):
            result = bt.navigate('http://err.com')
        self.assertFalse(result.success)


class TestBrowserToolEnsureBrowser(unittest.TestCase):

    def test_ensure_already_has_page(self):
        bt = BrowserTool()
        bt._page = MagicMock()
        self.assertTrue(bt._ensure_browser())

    def test_ensure_import_error(self):
        bt = BrowserTool()
        with patch.dict('sys.modules', {'playwright': None, 'playwright.sync_api': None}):
            with patch('builtins.__import__', side_effect=ImportError):
                result = bt._ensure_browser()
                self.assertFalse(result)
                self.assertEqual(bt._backend, 'crawler')

    def test_ensure_runtime_error(self):
        bt = BrowserTool()
        mock_pw = MagicMock()
        mock_pw_instance = MagicMock()
        mock_pw_instance.start.side_effect = RuntimeError('no browser')
        mock_pw.return_value = mock_pw_instance
        with patch.dict('sys.modules', {'playwright': MagicMock(),
                                         'playwright.sync_api': MagicMock()}):
            with patch('playwright.sync_api.sync_playwright', mock_pw):
                result = bt._ensure_browser()
                self.assertFalse(result)


if __name__ == '__main__':
    unittest.main()
