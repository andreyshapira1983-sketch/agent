"""RSS / Atom fetch tool tests.

No real network is used; a stub opener returns canned feed bytes.
"""

from __future__ import annotations

import gzip
import io
import urllib.error
from typing import Any

import pytest

from tools.rss_fetch import (
    DEFAULT_MAX_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_ENTRIES_CAP,
    RssFetchTool,
)


class _StubResponse:
    def __init__(self, *, body: bytes, status: int = 200,
                 content_type: str = "application/rss+xml",
                 content_encoding: str | None = None):
        self.status = status
        self._buf = io.BytesIO(body)
        self.headers = {"Content-Type": content_type}
        if content_encoding:
            self.headers["Content-Encoding"] = content_encoding

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _StubOpener:
    def __init__(self, response: _StubResponse | None = None, raise_exc: BaseException | None = None):
        self.response = response
        self.raise_exc = raise_exc
        self.last_request: Any = None

    def open(self, req, timeout=None):
        self.last_request = req
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.response


def _opener(body: bytes, **kwargs) -> _StubOpener:
    return _StubOpener(_StubResponse(body=body, **kwargs))


RSS = b"""<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Example Feed</title>
    <item>
      <title>First item</title>
      <link>https://example.com/first</link>
      <description>First summary.</description>
      <pubDate>Fri, 29 May 2026 10:00:00 GMT</pubDate>
      <guid>first-id</guid>
    </item>
    <item>
      <title>Second item</title>
      <link>https://example.com/second</link>
      <description>Second summary.</description>
    </item>
  </channel>
</rss>
"""

ATOM = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Feed</title>
  <entry>
    <title>Atom item</title>
    <link href="https://example.com/atom"/>
    <summary>Atom summary.</summary>
    <updated>2026-05-29T10:00:00Z</updated>
    <id>atom-id</id>
  </entry>
</feed>
"""


def test_defaults_and_risk():
    tool = RssFetchTool()

    assert tool.max_bytes == DEFAULT_MAX_BYTES
    assert tool.timeout_seconds == DEFAULT_TIMEOUT_SECONDS
    assert tool.risk == "read_only"
    assert tool.risk_for({}) == "read_only"


@pytest.mark.parametrize("kwargs", [
    {"max_bytes": 0},
    {"timeout_seconds": 0},
])
def test_invalid_construction_rejected(kwargs: dict):
    with pytest.raises(ValueError):
        RssFetchTool(**kwargs)


def test_rss_feed_parsed():
    opener = _opener(RSS)
    out = RssFetchTool(opener=opener).run(
        url="https://example.com/feed.xml",
        max_entries=1,
    )

    assert out["title"] == "Example Feed"
    assert out["feed_type"] == "rss"
    assert len(out["entries"]) == 1
    assert out["entries"][0]["title"] == "First item"
    assert out["entries"][0]["url"] == "https://example.com/first"
    assert len(out["content_hash"]) == 64
    assert opener.last_request.headers["User-agent"].startswith("AutonomousAgent/RSS")


def test_atom_feed_parsed():
    out = RssFetchTool(opener=_opener(ATOM, content_type="application/atom+xml")).run(
        url="https://example.com/atom.xml",
    )

    assert out["feed_type"] == "atom"
    assert out["entries"][0]["url"] == "https://example.com/atom"
    assert out["entries"][0]["published_at"] == "2026-05-29T10:00:00Z"


def test_gzip_feed_decompressed():
    raw = gzip.compress(RSS)
    out = RssFetchTool(opener=_opener(raw, content_encoding="gzip")).run(
        url="https://example.com/feed.xml",
    )

    assert out["entries"][0]["title"] == "First item"


def test_content_type_rejected():
    with pytest.raises(PermissionError, match="content-type"):
        RssFetchTool(opener=_opener(RSS, content_type="image/png")).run(
            url="https://example.com/feed.xml",
        )


def test_malformed_xml_rejected():
    with pytest.raises(ValueError, match="XML parse"):
        RssFetchTool(opener=_opener(b"<rss><channel>")).run(
            url="https://example.com/feed.xml",
        )


def test_http_and_url_errors_surface_cleanly():
    http_error = urllib.error.HTTPError(
        url="https://example.com/feed.xml",
        code=404,
        msg="Not Found",
        hdrs=None,
        fp=None,
    )
    with pytest.raises(ValueError, match="HTTP 404"):
        RssFetchTool(opener=_StubOpener(raise_exc=http_error)).run(
            url="https://example.com/feed.xml",
        )

    with pytest.raises(ValueError, match="URL error"):
        RssFetchTool(opener=_StubOpener(raise_exc=urllib.error.URLError("dns fail"))).run(
            url="https://example.com/feed.xml",
        )


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "http://localhost/feed.xml",
    "http://127.0.0.1/feed.xml",
    "https://пример.рф/feed.xml",
])
def test_unsafe_urls_rejected(url: str):
    with pytest.raises((PermissionError, ValueError)):
        RssFetchTool(opener=_opener(RSS)).run(url=url)


def test_max_entries_capped():
    out = RssFetchTool(opener=_opener(RSS)).run(
        url="https://example.com/feed.xml",
        max_entries=MAX_ENTRIES_CAP + 100,
    )

    assert len(out["entries"]) == 2


def test_validate_output():
    tool = RssFetchTool(opener=_opener(RSS))
    out = tool.run(url="https://example.com/feed.xml")

    ok, issues = tool.validate_output(out)

    assert ok
    assert issues == []
    bad = dict(out)
    bad["entries"] = "nope"
    ok, issues = tool.validate_output(bad)
    assert not ok
    assert "entries" in issues[0]


def test_validate_empty_feed_warns_not_fails():
    empty = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Empty</title></channel></rss>"""
    out = RssFetchTool(opener=_opener(empty)).run(url="https://example.com/feed.xml")

    ok, issues = RssFetchTool().validate_output(out)

    assert ok
    assert "no parsed entries" in issues[0]


def test_validate_missing_key_fails():
    tool = RssFetchTool(opener=_opener(RSS))
    out = tool.run(url="https://example.com/feed.xml")
    del out["content_hash"]

    ok, issues = tool.validate_output(out)

    assert not ok
    assert "missing keys" in issues[0]
