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
    DEFAULT_MAX_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
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
    def _resolver(host, port, family, socktype, proto, flags):  # noqa: ANN001, ARG001
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 443))]

    return _resolver


# ============================================================
# Construction
# ============================================================

class TestConstruction:
    def test_defaults(self):
        t = WebFetchTool()
        assert t.max_bytes == DEFAULT_MAX_BYTES
        assert t.timeout_seconds == DEFAULT_TIMEOUT_SECONDS
        assert t.risk == "read_only"
        assert t.risk_for({}) == "read_only"

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

    def test_non_ascii_url_rejected(self):
        with pytest.raises(PermissionError, match="ASCII"):
            WebFetchTool().run(url="https://пример.рф")

    def test_file_scheme_rejected(self):
        with pytest.raises(PermissionError, match="scheme"):
            WebFetchTool().run(url="file:///etc/passwd")

    def test_data_scheme_rejected(self):
        with pytest.raises(PermissionError, match="scheme"):
            WebFetchTool().run(url="data:text/plain,hello")

    def test_ftp_scheme_rejected(self):
        with pytest.raises(PermissionError, match="scheme"):
            WebFetchTool().run(url="ftp://example.com/x")

    def test_no_hostname_rejected(self):
        with pytest.raises(ValueError, match="hostname"):
            WebFetchTool().run(url="https:///path")


# ============================================================
# Local-network block-list
# ============================================================

class TestLocalNetworkBlock:
    @pytest.mark.parametrize("url", [
        "http://localhost/x",
        "http://127.0.0.1/x",
        "http://10.0.0.1/x",
        "http://192.168.1.1/x",
        "http://172.16.0.1/x",
        "http://169.254.169.254/latest/meta-data/",  # AWS metadata!
        "http://0.0.0.0/x",
        "http://[::1]/x",                            # IPv6 loopback
    ])
    def test_local_targets_refused(self, url: str):
        with pytest.raises(PermissionError):
            WebFetchTool().run(url=url)

    def test_public_ip_passes_local_check(self):
        """1.1.1.1 is public — should not be refused at the SSRF gate.
        We use a stub opener so no real call goes out."""
        opener = _opener_with(b"<html><body>hello</body></html>")
        out = WebFetchTool(opener=opener).run(url="http://1.1.1.1/")
        assert out["status_code"] == 200

    def test_hostname_resolving_to_private_ip_rejected(self):
        opener = _opener_with(b"<html><body>hello</body></html>")
        tool = WebFetchTool(opener=opener, resolver=_resolver_for("127.0.0.1"))

        with pytest.raises(PermissionError, match="public global"):
            tool.run(url="https://looks-public.example/")

    def test_final_redirect_url_revalidated(self):
        opener = _opener_with(
            b"<html><body>hello</body></html>",
            final_url="http://127.0.0.1/admin",
        )

        with pytest.raises(PermissionError, match="public global"):
            WebFetchTool(opener=opener).run(url="https://example.com/")


# ============================================================
# Happy path
# ============================================================

class TestHappyPath:
    def test_simple_html_strips_to_text(self):
        body = b"<html><body><h1>Title</h1><p>Para A</p><p>Para B</p></body></html>"
        opener = _opener_with(body)
        out = WebFetchTool(opener=opener).run(url="https://example.com/")
        assert out["url"] == "https://example.com/"
        assert out["requested_url"] == "https://example.com/"
        assert out["status_code"] == 200
        assert "Title" in out["text"]
        assert "Para A" in out["text"]
        assert "<" not in out["text"]  # tags gone
        assert out["text_truncated"] is False
        assert len(out["content_hash"]) == 64
        assert out["bytes"] == len(body)

    def test_script_and_style_blocks_removed(self):
        body = (
            b"<html><head><style>.x{color:red}</style>"
            b"<script>alert('x')</script></head>"
            b"<body><p>visible</p></body></html>"
        )
        opener = _opener_with(body)
        out = WebFetchTool(opener=opener).run(url="https://example.com/")
        assert "visible" in out["text"]
        assert "alert" not in out["text"]
        assert "color:red" not in out["text"]

    def test_html_entities_decoded(self):
        body = b"<p>5 &lt; 10 &amp; 10 &gt; 5</p>"
        opener = _opener_with(body)
        out = WebFetchTool(opener=opener).run(url="https://example.com/")
        assert "5 < 10 & 10 > 5" in out["text"]

    def test_plain_text_passes_through(self):
        body = b"just some plain text."
        opener = _opener_with(body, content_type="text/plain")
        out = WebFetchTool(opener=opener).run(url="https://example.com/")
        assert out["text"] == "just some plain text."

    def test_json_passes_through(self):
        body = b'{"key": "value"}'
        opener = _opener_with(body, content_type="application/json")
        out = WebFetchTool(opener=opener).run(url="https://example.com/")
        assert "value" in out["text"]

    def test_user_agent_header_set(self):
        opener = _opener_with(b"<p>x</p>")
        WebFetchTool(opener=opener).run(url="https://example.com/")
        req = opener.last_request
        assert req.headers.get("User-agent", "").startswith("AutonomousAgent/")

    def test_fetched_at_iso8601(self):
        opener = _opener_with(b"<p>x</p>")
        out = WebFetchTool(opener=opener).run(url="https://example.com/")
        assert "T" in out["fetched_at"]  # crude ISO check
        assert "+" in out["fetched_at"] or "Z" in out["fetched_at"]

    def test_final_url_is_reported_after_redirect(self):
        opener = _opener_with(b"<p>x</p>", final_url="https://example.com/final")

        out = WebFetchTool(opener=opener).run(url="https://example.com/start")

        assert out["url"] == "https://example.com/final"
        assert out["requested_url"] == "https://example.com/start"


# ============================================================
# Content-type policy
# ============================================================

class TestContentTypePolicy:
    def test_binary_content_type_refused(self):
        opener = _opener_with(b"\x00\x01\x02", content_type="application/octet-stream")
        with pytest.raises(PermissionError, match="content-type"):
            WebFetchTool(opener=opener).run(url="https://example.com/")

    def test_image_refused(self):
        opener = _opener_with(b"\x89PNG", content_type="image/png")
        with pytest.raises(PermissionError, match="content-type"):
            WebFetchTool(opener=opener).run(url="https://example.com/x.png")

    @pytest.mark.parametrize("ct", list(ALLOWED_CONTENT_TYPES))
    def test_allowed_types_pass(self, ct: str):
        opener = _opener_with(b"<p>ok</p>", content_type=ct)
        out = WebFetchTool(opener=opener).run(url="https://example.com/")
        assert out["content_type"] == ct

    def test_content_type_with_charset_suffix_accepted(self):
        opener = _opener_with(b"<p>x</p>", content_type="text/html; charset=utf-8")
        out = WebFetchTool(opener=opener).run(url="https://example.com/")
        assert "x" in out["text"]

    def test_empty_content_type_accepted(self):
        opener = _opener_with(b"<p>x</p>", content_type="")
        out = WebFetchTool(opener=opener).run(url="https://example.com/")
        assert "x" in out["text"]


# ============================================================
# Truncation
# ============================================================

class TestTruncation:
    def test_oversize_body_truncated(self):
        # Build a body > max_bytes; verify the response signals truncation.
        body = b"<p>" + b"a" * (200) + b"</p>"
        tool = WebFetchTool(max_bytes=10, opener=_opener_with(body))
        out = tool.run(url="https://example.com/")
        assert out["text_truncated"] is True
        assert out["bytes"] == 10

    def test_exact_size_not_truncated(self):
        body = b"abcdefghij"  # 10 bytes
        out = WebFetchTool(max_bytes=10, opener=_opener_with(body,
                                                              content_type="text/plain")).run(
            url="https://example.com/"
        )
        assert out["text_truncated"] is False


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


# ============================================================
# Network errors
# ============================================================

class TestNetworkErrors:
    def test_http_error_surfaces_clean(self):
        opener = _StubOpener(raise_exc=urllib.error.HTTPError(
            url="https://x", code=404, msg="Not Found", hdrs=None, fp=None,
        ))
        with pytest.raises(ValueError, match="HTTP 404"):
            WebFetchTool(opener=opener).run(url="https://x/")

    def test_url_error_surfaces_clean(self):
        opener = _StubOpener(raise_exc=urllib.error.URLError("dns fail"))
        with pytest.raises(ValueError, match="URL error"):
            WebFetchTool(opener=opener).run(url="https://x/")


# ============================================================
# Charset handling
# ============================================================

class TestCharset:
    def test_utf8_default(self):
        body = "проверка".encode("utf-8")
        opener = _opener_with(body, content_type="text/plain")
        out = WebFetchTool(opener=opener).run(url="https://example.com/")
        assert "проверка" in out["text"]

    def test_explicit_charset_in_header(self):
        body = "проверка".encode("cp1251")
        opener = _opener_with(body,
                              content_type="text/plain; charset=cp1251")
        out = WebFetchTool(opener=opener).run(url="https://example.com/")
        assert "проверка" in out["text"]


# ============================================================
# Redaction
# ============================================================

class TestRedaction:
    def test_secret_in_body_redacted(self):
        secret = "sk-" + "B" * 48
        body = f"<p>API key: {secret}</p>".encode("utf-8")
        opener = _opener_with(body)
        out = WebFetchTool(opener=opener).run(url="https://example.com/")
        assert secret not in out["text"]


# ============================================================
# content_hash
# ============================================================

class TestContentHash:
    def test_same_body_same_hash(self):
        body = b"<p>identical content</p>"
        a = WebFetchTool(opener=_opener_with(body)).run(url="https://x/")
        b = WebFetchTool(opener=_opener_with(body)).run(url="https://x/")
        assert a["content_hash"] == b["content_hash"]

    def test_different_body_different_hash(self):
        a = WebFetchTool(opener=_opener_with(b"<p>a</p>")).run(url="https://x/")
        b = WebFetchTool(opener=_opener_with(b"<p>b</p>")).run(url="https://x/")
        assert a["content_hash"] != b["content_hash"]


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

    def test_well_formed_passes(self):
        ok, warnings = WebFetchTool().validate_output(self._ok())
        assert ok
        assert warnings == []

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
