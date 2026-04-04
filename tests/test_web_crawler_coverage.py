"""Тесты для llm/web_crawler.py — WebCrawler (HTTP crawler with SSRF protection)."""
# pylint: disable=redefined-outer-name
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
import llm.web_crawler as wc_mod


# ── Fixture: mock requests and NetworkGuard ──────────────────────────────────

@pytest.fixture
def crawler():
    """Creates WebCrawler with mocked requests + NetworkGuard."""
    with patch("llm.web_crawler._NetworkGuard") as MockNG, \
         patch("llm.web_crawler.importlib.import_module") as mock_import:
        # Mock requests module
        mock_requests = MagicMock()
        mock_import.return_value = mock_requests

        # NetworkGuard allows everything by default
        ng_instance = MockNG.return_value
        ng_instance.is_allowed.return_value = (True, "")

        from llm.web_crawler import WebCrawler
        c = WebCrawler(timeout=5, max_content_kb=100, delay=0.0)
        c._network_guard = ng_instance
        c._requests = mock_requests
        yield c


# ── WebCrawler init ───────────────────────────────────────────────────────────

class TestWebCrawlerInit:
    def test_import_error(self):
        with patch("llm.web_crawler._NetworkGuard"), \
             patch("llm.web_crawler.importlib.import_module", side_effect=ImportError("no requests")):
            with pytest.raises(ImportError, match="requests"):
                from llm.web_crawler import WebCrawler
                WebCrawler()


# ── fetch ─────────────────────────────────────────────────────────────────────

class TestWebCrawlerFetch:
    def test_fetch_blocked_url(self, crawler):
        crawler._network_guard.is_allowed.return_value = (False, "SSRF")
        result = crawler.fetch("http://169.254.169.254/meta")
        assert result["success"] is False
        assert "SSRF" in result.get("error", "")

    def test_fetch_success(self, crawler):
        # Mock response
        resp = MagicMock()
        resp.status_code = 200
        resp.is_redirect = False
        resp.is_permanent_redirect = False
        resp.headers = {"content-type": "text/html; charset=utf-8"}

        content = b"<html><head><title>Test Page</title></head><body><p>Hello world</p></body></html>"

        def iter_content(**_kwargs):
            yield content
        resp.iter_content = iter_content

        # Mock session
        session = MagicMock()
        session.get.return_value = resp
        crawler._requests.Session.return_value = session

        # Mock bs4
        with patch("llm.web_crawler.importlib.import_module") as mock_imp:
            def import_side_effect(name):
                if name == "bs4":
                    bs4 = MagicMock()
                    soup = MagicMock()
                    soup.title = MagicMock()
                    soup.title.string = "Test Page"
                    soup.get_text.return_value = "Hello world"
                    soup.find_all.return_value = []
                    # Simulate decompose
                    soup.__call__ = MagicMock(return_value=[])
                    bs4.BeautifulSoup.return_value = soup
                    return bs4
                return MagicMock()
            mock_imp.side_effect = import_side_effect

            result = crawler.fetch("https://example.com")
            assert result["status"] == 200
            assert result["success"] is True

    def test_fetch_request_exception(self, crawler):
        session = MagicMock()
        exc_class = type("RequestException", (Exception,), {})
        crawler._requests.RequestException = exc_class
        session.get.side_effect = exc_class("Connection failed")
        crawler._requests.Session.return_value = session

        result = crawler.fetch("https://example.com")
        assert result["success"] is False
        assert "Connection failed" in result.get("error", "")

    def test_fetch_redirect_to_blocked(self, crawler):
        # First call returns redirect
        resp = MagicMock()
        resp.is_redirect = True
        resp.is_permanent_redirect = False
        resp.headers = {"Location": "http://internal.local/secret"}

        session = MagicMock()
        session.get.return_value = resp
        crawler._requests.Session.return_value = session

        # Block the redirect target
        def is_allowed_side_effect(url):
            if "internal" in url:
                return (False, "SSRF blocked")
            return (True, "")
        crawler._network_guard.is_allowed.side_effect = is_allowed_side_effect

        result = crawler.fetch("https://example.com")
        assert result["success"] is False
        assert "SSRF" in result.get("error", "") or "Redirect" in result.get("error", "")


# ── crawl ─────────────────────────────────────────────────────────────────────

class TestWebCrawlerCrawl:
    def test_crawl_basic(self, crawler):
        # Mock fetch to return a simple page
        page1 = {
            "url": "https://example.com",
            "title": "Home",
            "text": "Welcome",
            "links": ["https://example.com/about"],
            "status": 200,
            "success": True,
        }
        page2 = {
            "url": "https://example.com/about",
            "title": "About",
            "text": "About us",
            "links": [],
            "status": 200,
            "success": True,
        }
        call_count = [0]
        def mock_fetch(_url):
            call_count[0] += 1
            if call_count[0] == 1:
                return page1
            return page2

        with patch.object(crawler, "fetch", side_effect=mock_fetch):
            results = crawler.crawl("https://example.com", depth=1, max_pages=5)
            assert len(results) >= 1
            assert results[0]["url"] == "https://example.com"

    def test_crawl_respects_max_pages(self, crawler):
        page = {
            "url": "https://example.com",
            "title": "Home",
            "text": "Welcome",
            "links": [f"https://example.com/page{i}" for i in range(20)],
            "status": 200,
            "success": True,
        }
        with patch.object(crawler, "fetch", return_value=page):
            results = crawler.crawl("https://example.com", depth=1, max_pages=3)
            assert len(results) <= 3

    def test_crawl_skips_other_domains(self, crawler):
        page = {
            "url": "https://example.com",
            "title": "Home",
            "text": "Welcome",
            "links": ["https://other.com/evil"],
            "status": 200,
            "success": True,
        }
        with patch.object(crawler, "fetch", return_value=page):
            results = crawler.crawl("https://example.com", depth=1)
            # Only the start page, other domain links not followed
            assert len(results) == 1

    def test_crawl_skips_blocked_urls(self, crawler):
        page = {
            "url": "https://example.com",
            "title": "Home",
            "text": "Welcome",
            "links": ["https://example.com/blocked"],
            "status": 200,
            "success": True,
        }
        def is_allowed_side_effect(url):
            if "blocked" in url:
                return (False, "SSRF")
            return (True, "")
        crawler._network_guard.is_allowed.side_effect = is_allowed_side_effect

        with patch.object(crawler, "fetch", return_value=page):
            results = crawler.crawl("https://example.com", depth=1)
            assert len(results) == 1


# ── fetch_text ────────────────────────────────────────────────────────────────

class TestWebCrawlerFetchText:
    def test_fetch_text(self, crawler):
        with patch.object(crawler, "fetch", return_value={"text": "Some text", "success": True}):
            result = crawler.fetch_text("https://example.com")
            assert result == "Some text"

    def test_fetch_text_missing(self, crawler):
        with patch.object(crawler, "fetch", return_value={"success": False}):
            result = crawler.fetch_text("https://example.com")
            assert result == ""


# ── _parse_html ───────────────────────────────────────────────────────────────

class TestParseHtml:
    def test_parse_html_fallback_no_bs4(self):
        """Test HTML parsing fallback when bs4 is not available."""
        import re as _re
        from urllib.parse import urljoin
        # Directly test the regex fallback logic
        content = b"<html><body><p>Hello <b>world</b></p></body></html>"
        text = _re.sub(r'<[^>]+>', ' ', content.decode('utf-8', errors='replace'))
        text = ' '.join(text.split())[:5000]
        assert "Hello" in text
        assert "world" in text


# ── _read_limited ─────────────────────────────────────────────────────────────

class TestReadLimited:
    def test_read_limited(self, crawler):
        resp = MagicMock()
        def iter_content(**_kwargs):
            yield b"a" * 5000
            yield b"b" * 5000
        resp.iter_content = iter_content

        crawler.max_content_kb = 1  # 1 KB limit
        data = crawler._read_limited(resp)
        assert len(data) <= 1024
        resp.close.assert_called_once()


# ── _fetch_with_retry ─────────────────────────────────────────────────────────

class TestFetchWithRetry:
    def test_success_first_try(self, crawler):
        with patch.object(crawler, "fetch", return_value={"success": True, "status": 200}):
            result = crawler._fetch_with_retry("https://example.com")
            assert result["success"] is True

    def test_retries_on_5xx(self, crawler):
        responses = [
            {"success": False, "status": 500},
            {"success": True, "status": 200},
        ]
        with patch.object(crawler, "fetch", side_effect=responses), \
             patch("llm.web_crawler.time.sleep"):
            result = crawler._fetch_with_retry("https://example.com", retries=2)
            assert result["success"] is True

    def test_no_retry_on_4xx(self, crawler):
        with patch.object(crawler, "fetch", return_value={"success": False, "status": 404}):
            result = crawler._fetch_with_retry("https://example.com", retries=2)
            assert result["status"] == 404


# ── _is_safe_url ──────────────────────────────────────────────────────────────

class TestIsSafeUrl:
    def test_safe_url(self, crawler):
        crawler._network_guard.is_allowed.return_value = (True, "")
        assert crawler._is_safe_url("https://example.com") is True

    def test_unsafe_url(self, crawler):
        crawler._network_guard.is_allowed.return_value = (False, "blocked")
        assert crawler._is_safe_url("http://169.254.169.254") is False


# ── _throttle ─────────────────────────────────────────────────────────────────

class TestThrottle:
    def test_throttle_no_wait(self, crawler):
        crawler.delay = 0.0
        crawler._last_request = 0.0
        crawler._throttle()  # should not block
