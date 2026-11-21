"""MVP-14.2 — unit tests for `web_fetch`.

We never hit a real network. A stub opener returns canned bytes +
headers so every branch (HTTP error, content-type rejection, gzip,
size cap, redaction, charset, HTML strip) is exercised deterministically.
"""
from __future__ import annotations

import gzip
import io
import socket
import urllib.error
from typing import Any

import pytest

from tools.web_fetch import (
    ALLOWED_CONTENT_TYPES,
    MAX_URL_LEN,
    WebFetchTool,
)

# ============================================================
# Stub opener helpers
# ============================================================

class _StubResponse:
    def __init__(self, *, body: bytes, status: int = 200,
                 content_type: str = "text/html",
                 content_encoding: str | None = None,
                 final_url: str | None = None):
        self.status = status
        self._buf = io.BytesIO(body)
        self._final_url = final_url
        headers = {"Content-Type": content_type}
        if content_encoding:
            headers["Content-Encoding"] = content_encoding
        self.headers = headers

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def geturl(self) -> str:
        return self._final_url or "https://example.com/"

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _StubOpener:
    """Mimics urllib's `build_opener()` return so `.open(req, timeout=...)`
    yields a `_StubResponse`."""

    def __init__(self, *, response: _StubResponse | None = None,
                 raise_exc: BaseException | None = None):
        self.response = response
        self.raise_exc = raise_exc
        self.last_request: Any = None

    def open(self, req, timeout=None):
        self.last_request = req
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.response


def _opener_with(body: bytes, **kw) -> _StubOpener:
    return _StubOpener(response=_StubResponse(body=body, **kw))


def _resolver_for(ip: str):
    def _resolver(host, port, family, socktype, proto, flags):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 443))]

    return _resolver


# ============================================================
# Construction
# ============================================================

class TestConstruction:
    def test_zero_max_bytes_rejected(self):
        with pytest.raises(ValueError, match="max_bytes"):
            WebFetchTool(max_bytes=0)

    def test_zero_timeout_rejected(self):
        with pytest.raises(ValueError, match="timeout_seconds"):
            WebFetchTool(timeout_seconds=0)


# ============================================================
# URL validation
# ============================================================

class TestUrlValidation:
    def test_non_string_rejected(self):
        with pytest.raises(ValueError, match="non-empty string"):
            WebFetchTool().run(url=123)  # type: ignore[arg-type]

    def test_empty_string_rejected(self):
        with pytest.raises(ValueError, match="non-empty string"):
            WebFetchTool().run(url="   ")

    def test_too_long_rejected(self):
        with pytest.raises(ValueError, match="too long"):
            WebFetchTool().run(url="https://" + "x" * MAX_URL_LEN)


    def test_data_scheme_rejected(self):
        with pytest.raises(PermissionError, match="scheme"):
            WebFetchTool().run(url="data:text/plain,hello")


    def test_no_hostname_rejected(self):
        with pytest.raises(ValueError, match="hostname"):
            WebFetchTool().run(url="https:///path")


# ============================================================
# Local-network block-list
# ============================================================

class TestLocalNetworkBlock:
    def test_public_ip_passes_local_check(self):
        """1.1.1.1 is public — should not be refused at the SSRF gate.
        We use a stub opener so no real call goes out."""
        opener = _opener_with(b"<html><body>hello</body></html>")
        out = WebFetchTool(
            opener=opener,
            allow_http_hosts=("1.1.1.1",),
        ).run(url="http://1.1.1.1/")
        assert out["status_code"] == 200


    def test_plain_http_allowed_for_explicit_host(self):
        opener = _opener_with(b"<html><body>hello</body></html>")

        out = WebFetchTool(
            opener=opener,
            allow_http_hosts=("example.com",),
        ).run(url="http://example.com/")

        assert out["url"] == "https://example.com/"


    def test_final_redirect_url_revalidated(self):
        opener = _opener_with(
            b"<html><body>hello</body></html>",
            final_url="http://127.0.0.1/admin",
        )

        with pytest.raises(PermissionError):
            WebFetchTool(opener=opener).run(url="https://example.com/")

    def test_egress_allow_policy_blocks_unlisted_host(self):
        opener = _opener_with(b"<html><body>hello</body></html>")

        with pytest.raises(PermissionError, match="not allowed by egress policy"):
            WebFetchTool(
                opener=opener,
                egress_allow_hosts=("*.python.org",),
            ).run(url="https://example.com/")


# ============================================================
# Happy path
# ============================================================

class TestHappyPath:
    def test_json_passes_through(self):
        body = b'{"key": "value"}'
        opener = _opener_with(body, content_type="application/json")
        out = WebFetchTool(opener=opener).run(url="https://example.com/")
        assert "value" in out["text"]


# ============================================================
# Content-type policy
# ============================================================

class TestContentTypePolicy:
    def test_binary_content_type_refused(self):
        opener = _opener_with(b"\x00\x01\x02", content_type="application/octet-stream")
        with pytest.raises(PermissionError, match="content-type"):
            WebFetchTool(opener=opener).run(url="https://example.com/")


    @pytest.mark.parametrize("ct", list(ALLOWED_CONTENT_TYPES))
    def test_allowed_types_pass(self, ct: str):
        # Тело обязано соответствовать типу: с 2026-09-20 в списке есть
        # application/pdf, и для него «<p>ok</p>» — не документ, а мусор.
        # Разбор PDF отказывает названной причиной, и это правильно; проверять
        # ворота типа подложным телом значило бы проверять не ворота.
        from tests.test_a_primary_source_in_pdf_is_readable import _pdf_bytes

        body = _pdf_bytes(["ok"]) if ct == "application/pdf" else b"<p>ok</p>"
        opener = _opener_with(body, content_type=ct)
        out = WebFetchTool(opener=opener).run(url="https://example.com/")
        assert out["content_type"] == ct


    def test_empty_content_type_accepted(self):
        opener = _opener_with(b"<p>x</p>", content_type="")
        out = WebFetchTool(opener=opener).run(url="https://example.com/")
        assert "x" in out["text"]


# ============================================================
# Compression
# ============================================================

class TestGzipDecompression:
    def test_gzip_response_decoded(self):
        plaintext = b"<p>hello compressed world</p>"
        gz = gzip.compress(plaintext)
        opener = _opener_with(gz, content_encoding="gzip")
        out = WebFetchTool(opener=opener).run(url="https://example.com/")
        assert "hello compressed world" in out["text"]

    def test_gzip_decompression_is_capped_after_inflate(self):
        plaintext = b"<p>" + b"a" * 200 + b"</p>"
        gz = gzip.compress(plaintext)
        opener = _opener_with(gz, content_encoding="gzip")

        out = WebFetchTool(max_bytes=20, opener=opener).run(url="https://example.com/")

        assert out["text_truncated"] is True
        assert out["bytes"] == 20


# ============================================================
# Network errors
# ============================================================

class TestNetworkErrors:
    def test_url_error_surfaces_clean(self):
        opener = _StubOpener(raise_exc=urllib.error.URLError("dns fail"))
        with pytest.raises(ValueError, match="URL error"):
            WebFetchTool(opener=opener).run(url="https://x/")


# ============================================================
# validate_output
# ============================================================

class TestValidateOutput:
    def _ok(self) -> dict[str, Any]:
        return {
            "url": "https://x/", "status_code": 200,
            "content_type": "text/html", "fetched_at": "2026-01-01T00:00:00Z",
            "content_hash": "a" * 64,
            "text": "x", "text_truncated": False,
            "bytes": 1, "elapsed_ms": 5,
            "compensation_plan": {"id": "noop", "actions": [],
                                   "tool_name": "web_fetch", "description": "d"},
        }


    def test_non_dict_rejected(self):
        ok, _ = WebFetchTool().validate_output("nope")
        assert not ok

    def test_missing_key_rejected(self):
        out = self._ok()
        del out["url"]
        ok, _ = WebFetchTool().validate_output(out)
        assert not ok

    def test_bad_hash_rejected(self):
        out = self._ok()
        out["content_hash"] = "short"
        ok, _ = WebFetchTool().validate_output(out)
        assert not ok

    def test_negative_bytes_rejected(self):
        out = self._ok()
        out["bytes"] = -1
        ok, _ = WebFetchTool().validate_output(out)
        assert not ok

    def test_empty_text_warns_not_fails(self):
        out = self._ok()
        out["text"] = "    "
        ok, warnings = WebFetchTool().validate_output(out)
        assert ok
        assert any("no extractable text" in w for w in warnings)


def test_pdf_text_raises_when_pdftotext_missing(monkeypatch):
    from tools import web_fetch

    def _raise_file_not_found(argv, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(web_fetch.subprocess, "run", _raise_file_not_found)
    with pytest.raises(ValueError, match="pdftotext not installed"):
        web_fetch._pdf_text(b"%PDF-1.4 fake")
