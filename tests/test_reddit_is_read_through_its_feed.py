"""Страница Reddit читается через свою ленту `.rss`.

24.09, 15:57: «посмотри в Reddit» — web_fetch r/LocalLLaMA вернул заглушку
«Reddit» (6 символов), агент пересказывал вторичные дайджесты. С сервера:
`.json` — 403, `.rss` — 200 и посты целиком.
"""
from __future__ import annotations

from tests.test_web_fetch import _opener_with, _resolver_for
from tools.reddit_feed import reddit_feed_url
from tools.web_fetch import WebFetchTool

_ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>LocalLLaMA</title>
<entry><title>Is 16 GB VRAM enough for useful local AI?</title>
<link href="https://www.reddit.com/r/LocalLLaMA/comments/abc/is_16gb/"/>
<updated>2026-09-23T10:00:00+00:00</updated>
<content type="html">&lt;p&gt;Benchmarks on 12-16 GB cards, license honesty.&lt;/p&gt;</content></entry>
</feed>"""


def test_a_reddit_page_maps_to_its_feed() -> None:
    assert reddit_feed_url("https://www.reddit.com/r/LocalLLaMA/") == "https://www.reddit.com/r/LocalLLaMA/.rss"
    assert reddit_feed_url("https://old.reddit.com/r/LocalLLaMA/top/?t=week") == (
        "https://www.reddit.com/r/LocalLLaMA/top/.rss?t=week")
    assert reddit_feed_url("https://www.reddit.com/r/LocalLLaMA/.rss") is None
    assert reddit_feed_url("https://example.com/r/LocalLLaMA/") is None


def test_web_fetch_returns_the_posts_not_the_script_stub() -> None:
    opener = _opener_with(_ATOM, content_type="application/atom+xml; charset=UTF-8",
                          final_url="https://www.reddit.com/r/LocalLLaMA/.rss")
    out = WebFetchTool(opener=opener, resolver=_resolver_for("151.101.1.140")).run(
        url="https://www.reddit.com/r/LocalLLaMA/")
    assert opener.last_request.full_url == "https://www.reddit.com/r/LocalLLaMA/.rss"
    assert "Is 16 GB VRAM enough" in out["text"] and "license honesty" in out["text"]
    assert out["requested_url"] == "https://www.reddit.com/r/LocalLLaMA/"
