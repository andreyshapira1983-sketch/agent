"""Read Reddit through its `.rss` feed: pages are script-built and `.json` is refused (403)."""
from __future__ import annotations

from urllib.parse import urlsplit

_HOSTS = frozenset({"reddit.com", "www.reddit.com", "old.reddit.com", "new.reddit.com",
                    "np.reddit.com"})


def reddit_feed_url(url: str) -> str | None:
    """Feed URL for a Reddit page; None if not Reddit or already a feed/JSON URL."""
    parts = urlsplit(url)
    if (parts.hostname or "").lower() not in _HOSTS:
        return None
    path = parts.path or "/"
    if path.endswith((".rss", ".json")):
        return None
    feed = f"https://www.reddit.com{path.rstrip('/')}/.rss"
    return f"{feed}?{parts.query}" if parts.query else feed


def feed_as_text(xml_text: str, *, limit: int = 25) -> str:
    """Feed to readable text: title, URL and content of each entry (rss_fetch's safe parser)."""
    from tools.rss_fetch import _parse_feed
    from tools.web_fetch import WebFetchTool

    title, _kind, entries = _parse_feed(xml_text, limit=limit)
    lines = [f"Reddit feed: {title}"]
    for i, entry in enumerate(entries, 1):
        body = WebFetchTool._strip_html(entry.get("summary") or "")
        lines.append(f"\n[{i}] {entry.get('title', '')}\n{entry.get('url', '')}\n"
                     f"{entry.get('published_at', '')}\n{body[:1500]}")
    return "\n".join(lines)
