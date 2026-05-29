"""RSS / Atom fetch tool tests.

No real network is used; a stub opener returns canned feed bytes.
"""

from __future__ import annotations

import gzip
import io
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
    def __init__(self, response: _StubResponse):
        self.response = response
        self.last_request: Any = None

    def open(self, req, timeout=None):
        self.last_request = req
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
