"""Tests for tools/builtins/web_fetch.py — WebFetchTool.

We avoid network access by stubbing urllib.request.urlopen via the
`urlopen` symbol imported in web_fetch's module namespace.
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

from tools.builtins.web_fetch import WebFetchTool, _is_private, _extract_html


# ════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════

def _fake_response(body: bytes, *, status: int = 200, content_type: str = "text/html; charset=utf-8", url: str = "https://example.com/"):
    resp = MagicMock()
    resp.read.side_effect = lambda *a, **k: body
    resp.status = status
    resp.headers = {"Content-Type": content_type}
    resp.geturl.return_value = url
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


# ════════════════════════════════════════════════════════════════════
# Validation
# ════════════════════════════════════════════════════════════════════

class TestValidation:

    def test_missing_url_fails(self):
        result = WebFetchTool().execute()
        assert not result.success
        assert "url" in result.error.lower()

    def test_unsupported_scheme(self):
        result = WebFetchTool().execute(url="file:///etc/passwd")
        assert not result.success
        assert "scheme" in result.error.lower()

    def test_missing_host(self):
        result = WebFetchTool().execute(url="https:///path")
        assert not result.success

    def test_private_address_blocked_by_default(self):
        result = WebFetchTool().execute(url="http://127.0.0.1/")
        assert not result.success
        assert "private" in result.error.lower() or "localhost" in result.error.lower()

    def test_localhost_blocked(self):
        result = WebFetchTool().execute(url="http://localhost:8080/health")
        assert not result.success

    def test_private_addr_allowed_with_flag(self):
        with patch(
            "tools.builtins.web_fetch.urllib.request.urlopen",
            return_value=_fake_response(b"<html><body>ok</body></html>"),
        ):
            result = WebFetchTool().execute(url="http://127.0.0.1/", allow_private=True)
        assert result.success


# ════════════════════════════════════════════════════════════════════
# HTML extraction
# ════════════════════════════════════════════════════════════════════

class TestHTMLExtraction:

    def test_extract_title_and_body(self):
        html = b"""
            <html><head><title>Test Page</title></head>
            <body><h1>Hello</h1><p>world</p>
            <script>evil()</script>
            </body></html>
        """
        with patch(
            "tools.builtins.web_fetch.urllib.request.urlopen",
            return_value=_fake_response(html),
        ):
            result = WebFetchTool().execute(url="https://example.com/")
        assert result.success
        assert result.output["title"] == "Test Page"
        assert "Hello" in result.output["text"]
        assert "world" in result.output["text"]
        assert "evil()" not in result.output["text"]

    def test_strips_style_blocks(self):
        html = b"<html><body>Visible<style>.x{display:none}</style></body></html>"
        with patch(
            "tools.builtins.web_fetch.urllib.request.urlopen",
            return_value=_fake_response(html),
        ):
            result = WebFetchTool().execute(url="https://example.com/")
        assert "display" not in result.output["text"]
        assert "Visible" in result.output["text"]

    def test_non_html_returned_as_text(self):
        body = b"plain text data"
        with patch(
            "tools.builtins.web_fetch.urllib.request.urlopen",
            return_value=_fake_response(body, content_type="text/plain"),
        ):
            result = WebFetchTool().execute(url="https://example.com/")
        assert result.success
        assert result.output["text"] == "plain text data"
        assert result.output["title"] == ""


# ════════════════════════════════════════════════════════════════════
# Truncation
# ════════════════════════════════════════════════════════════════════

class TestTruncation:

    def test_truncates_long_output(self):
        # 35K of HTML body content
        body = b"<html><body>" + b"a" * 35_000 + b"</body></html>"
        with patch(
            "tools.builtins.web_fetch.urllib.request.urlopen",
            return_value=_fake_response(body),
        ):
            result = WebFetchTool().execute(url="https://example.com/")
        assert result.output["truncated"] is True
        assert len(result.output["text"]) <= 30_000


# ════════════════════════════════════════════════════════════════════
# Error handling
# ════════════════════════════════════════════════════════════════════

class TestErrors:

    def test_http_error_returns_fail(self):
        err = HTTPError("https://example.com/", 404, "Not Found", {}, None)
        with patch("tools.builtins.web_fetch.urllib.request.urlopen", side_effect=err):
            result = WebFetchTool().execute(url="https://example.com/")
        assert not result.success
        assert "404" in result.error

    def test_url_error_returns_fail(self):
        err = URLError("name resolution failed")
        with patch("tools.builtins.web_fetch.urllib.request.urlopen", side_effect=err):
            result = WebFetchTool().execute(url="https://example.com/")
        assert not result.success
        assert "network" in result.error.lower()


# ════════════════════════════════════════════════════════════════════
# SSRF helpers
# ════════════════════════════════════════════════════════════════════

class TestPrivateHostHelper:

    @pytest.mark.parametrize("host", [
        "localhost", "127.0.0.1", "10.0.0.1", "192.168.1.1", "172.16.0.5",
        "::1",
    ])
    def test_blocks_private(self, host):
        assert _is_private(host) is True

    @pytest.mark.parametrize("host", [
        "example.com", "8.8.8.8",
    ])
    def test_allows_public_dns_names_and_ips(self, host):
        # Domain names are NOT pre-resolved here — they pass _is_private's
        # check. The actual block happens at socket-resolution time if at all.
        assert _is_private(host) is False


# ════════════════════════════════════════════════════════════════════
# Spec
# ════════════════════════════════════════════════════════════════════

def test_spec_metadata():
    tool = WebFetchTool()
    spec = tool.spec
    assert spec.name == "web_fetch"
    assert spec.is_destructive is False
    assert spec.requires_approval is False
    assert "url" in spec.parameters
